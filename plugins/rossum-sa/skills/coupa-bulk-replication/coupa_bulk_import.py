#!/usr/bin/env python3
"""
coupa_bulk_import.py

Full initial load of Coupa master data into Rossum Data Storage.
Replicates newest-first (sorted by updated_at DESC) so datasets are usable
before the run completes.  Saves progress to coupa_import_state.json after
every DS flush — safe to kill and resume at any time.

Write strategy: insert_many (synchronous, 200 OK) in DS_BATCH_SIZE chunks.
This is faster than async bulk_write and avoids async queue buildup after kill.

Usage:
    python coupa_bulk_import.py                      # all datasets, full load
    python coupa_bulk_import.py --dataset purchase_orders
    python coupa_bulk_import.py --limit 1            # smoke test: 1 record each
    python coupa_bulk_import.py --resume             # continue from saved state
    python coupa_bulk_import.py --resume --dataset purchase_orders

    # With auto token refresh (runs unattended):
    python coupa_bulk_import.py --dataset purchase_orders \\
        --username user@example.com --password secret

Configuration:
    All credentials, URLs, and dataset definitions live in
    coupa_bulk_import.config.json next to this script (override with --config).
    See coupa_bulk_import.config.example.json for the schema.

    The script itself contains no credentials — same .py runs for every
    customer; per-customer values live in the gitignored config file.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Configuration — populated by load_config() at startup ───────────────────────
#
# These module-level names are filled from coupa_bulk_import.config.json
# (or whichever --config path the user passes). Nothing customer-specific
# is hardcoded in this file.

COUPA_CLIENT_ID:     str = ""
COUPA_CLIENT_SECRET: str = ""
COUPA_BASE_URL:      str = ""

ROSSUM_TOKEN:   str = ""
ROSSUM_DS_URL:  str = ""
ROSSUM_API_URL: str = ""

STATE_FILE    = Path("coupa_import_state.json")  # overridden by --state-file
DS_BATCH_SIZE = 5000                              # records per insert_many call

RE_REPLICATION_NOTICE = """
   ================================================================
   [NOTICE] >=90% of the first batch already exists in this
   collection. insert-dedup does NOT update existing documents:
   if you are re-replicating with a changed field list, stop this
   run, drop (or blue-green swap) the collection, and start fresh.
   A deliberate fresh run over a partially-loaded collection is
   fine - duplicates are skipped, missing records are filled in.
   ================================================================"""

DATASETS: dict[str, dict] = {}

CONFIG_PATH: Path | None = None  # set by load_config(); re-read on DS 401


def load_config(path: Path) -> None:
    """Populate module-level configuration from a JSON file."""
    global COUPA_CLIENT_ID, COUPA_CLIENT_SECRET, COUPA_BASE_URL
    global ROSSUM_TOKEN, ROSSUM_DS_URL, ROSSUM_API_URL
    global DS_BATCH_SIZE, DATASETS, CONFIG_PATH

    CONFIG_PATH = path

    if not path.exists():
        raise SystemExit(
            f"Config file not found: {path}\n"
            "Copy coupa_bulk_import.config.example.json to "
            f"{path.name} and fill it in."
        )

    cfg = json.loads(path.read_text())

    coupa = cfg.get("coupa") or {}
    COUPA_CLIENT_ID     = (coupa.get("client_id") or "").strip()
    COUPA_CLIENT_SECRET = (coupa.get("client_secret") or "").strip()
    COUPA_BASE_URL      = (coupa.get("base_url") or "").strip().rstrip("/")

    rossum = cfg.get("rossum") or {}
    ROSSUM_TOKEN   = (rossum.get("token") or "").strip()
    ROSSUM_DS_URL  = (rossum.get("ds_url") or "").strip().rstrip("/")
    ROSSUM_API_URL = (rossum.get("api_url") or "").strip().rstrip("/")

    DS_BATCH_SIZE = int(cfg.get("ds_batch_size", 5000))
    DATASETS      = cfg.get("datasets") or {}

    required = {
        "coupa.base_url":      COUPA_BASE_URL,
        "coupa.client_id":     COUPA_CLIENT_ID,
        "coupa.client_secret": COUPA_CLIENT_SECRET,
        "rossum.api_url":      ROSSUM_API_URL,
        "rossum.ds_url":       ROSSUM_DS_URL,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise SystemExit(f"Missing required config keys: {', '.join(missing)}")
    if not DATASETS:
        raise SystemExit(f"Config file {path} defines no datasets")

    seen: dict[str, str] = {}
    for ds_key, ds_cfg in DATASETS.items():
        coll = ds_cfg.get("collection", "")
        if coll in seen:
            raise SystemExit(
                f"Config error: datasets '{seen[coll]}' and '{ds_key}' both target "
                f"collection '{coll}'. Deterministic _id dedup requires one "
                "collection per dataset."
            )
        seen[coll] = ds_key


def resolve_dataset_keys(arg: str, datasets: dict) -> list[str]:
    """Resolve --dataset: 'all', a single key, or a comma-separated list."""
    if arg == "all":
        return list(datasets)
    keys = [k.strip() for k in arg.split(",") if k.strip()]
    unknown = [k for k in keys if k not in datasets]
    if unknown:
        raise SystemExit(
            f"Unknown dataset(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(datasets)) or '(none)'}"
        )
    return keys


def default_state_path(dataset_arg: str, keys: list[str], state_file: str | None) -> Path:
    """State-file selection: explicit --state-file wins; an explicit single
    dataset gets its own file; 'all' and comma lists share STATE_FILE."""
    if state_file:
        return Path(state_file)
    if dataset_arg != "all" and len(keys) == 1:
        return Path(f"coupa_import_state_{keys[0]}.json")
    return STATE_FILE


# ── Rossum auth ──────────────────────────────────────────────────────────────────

def refresh_rossum_token(username: str, password: str) -> str:
    """Obtain a fresh Rossum token via username+password login."""
    resp = requests.post(
        f"{ROSSUM_API_URL}/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()["key"]
    print("   [Rossum token refreshed]")
    return token


def reload_config_token() -> str:
    """Re-read rossum.token from the config file.

    A --resume relaunch always picks up a refreshed config token; this lets a
    RUNNING job do the same on a DS 401 instead of dying (supervisor restart
    stays the backstop).  Returns "" when the config is unreadable.
    """
    if CONFIG_PATH is None or not CONFIG_PATH.exists():
        return ""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    return ((cfg.get("rossum") or {}).get("token") or "").strip()


# ── Coupa auth ───────────────────────────────────────────────────────────────────

def get_coupa_token(scope: str) -> str:
    resp = requests.post(
        f"{COUPA_BASE_URL}/oauth2/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     COUPA_CLIENT_ID,
            "client_secret": COUPA_CLIENT_SECRET,
            "scope":         scope,
        },
        verify=False,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Coupa pagination ─────────────────────────────────────────────────────────────

def fetch_page(session: requests.Session, endpoint: str, fields: list,
               offset: int, anchor_ts: str) -> list:
    """Fetch one page of records sorted newest-first, anchored to anchor_ts."""
    params = {
        "fields":               json.dumps(fields),
        "order_by":             "updated_at",
        "dir":                  "desc",
        "offset":               offset,
        "updated-at[lt_or_eq]": anchor_ts,
    }
    resp = session.get(f"{COUPA_BASE_URL}/{endpoint}", params=params,
                       verify=False, timeout=120)
    resp.raise_for_status()
    return resp.json() or []


# ── Data Storage insert ──────────────────────────────────────────────────────────

_RETRYABLE = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

BatchResult = namedtuple("BatchResult", "inserted duplicates failed")

_DUPLICATE_KEY_CODE = 11000


def _is_duplicate_error(err: dict) -> bool:
    """Mongo duplicate-key: code 11000, or E11000 in the message (shape varies)."""
    return err.get("code") == _DUPLICATE_KEY_CODE or "E11000" in str(err.get("errmsg", ""))


def insert_batch(session: requests.Session, collection: str, records: list,
                 _retries: int = 5) -> BatchResult:
    """Synchronous insert_many — returns a BatchResult(inserted, duplicates, failed).

    Uses ordered=False so duplicate-key errors on resume are silently skipped
    rather than aborting the whole batch. Duplicate-key skips are informational
    (expected after resume or smoke test); other write failures are warned via
    [WARN] (document-level validation/size failures).
    Retries on transient SSL/connection errors with exponential backoff.
    """
    for attempt in range(1, _retries + 1):
        try:
            resp = session.post(
                f"{ROSSUM_DS_URL}/data/insert_many",
                json={"collectionName": collection, "documents": records, "ordered": False},
                timeout=120,
            )
            resp.raise_for_status()
            body         = resp.json().get("result", {}) or {}
            inserted     = len(body.get("inserted_ids") or [])
            write_errors = body.get("write_errors") or []
            duplicates   = sum(1 for e in write_errors if _is_duplicate_error(e))
            failed       = max(len(records) - inserted - duplicates, 0)
            if duplicates:
                print(f"   {duplicates} duplicate(s) skipped "
                      "(expected after resume or smoke test)")
            if failed:
                non_dup = [e for e in write_errors if not _is_duplicate_error(e)]
                print(f"   [WARN] {failed} document(s) failed in this batch. "
                      f"First error: {non_dup[0] if non_dup else 'n/a'}")
            return BatchResult(inserted, duplicates, failed)
        except _RETRYABLE as exc:
            if attempt == _retries:
                raise
            wait = 2 ** attempt
            print(f"   [RETRY {attempt}/{_retries}] {type(exc).__name__}: {exc} — retrying in {wait}s")
            time.sleep(wait)


def _updated_at(record: dict) -> str:
    """Return the updated_at value; Coupa uses both 'updated_at' and 'updated-at'."""
    return record.get("updated_at") or record.get("updated-at") or "n/a"


def assign_ids(records: list, id_key: str) -> tuple[list, int]:
    """Set each document's Mongo _id deterministically from its Coupa id.

    Re-inserting the same record then dedupes via duplicate-key skip
    (ordered=False) instead of creating a second copy.  Records missing
    the id keep an auto-generated _id — never _id: null, because two
    nulls would silently dedupe against each other.
    """
    missing = 0
    for rec in records:
        rid = rec.get(id_key)
        if rid is None:
            missing += 1
        else:
            rec["_id"] = rid
    return records, missing


# ── State file ───────────────────────────────────────────────────────────────────

def load_state(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(state: dict, path: Path) -> None:
    path.write_text(json.dumps(state, indent=2))


# ── Per-dataset import ───────────────────────────────────────────────────────────

def import_dataset(key: str, limit: int | None, resume: bool,
                   state: dict, ds_session: requests.Session,
                   state_path: Path = STATE_FILE,
                   username: str | None = None,
                   password: str | None = None) -> None:
    cfg   = DATASETS[key]
    ds_st = state.get(key, {})

    # Anchor: set once at start of a fresh run; reused on every resume so pagination
    # stays consistent even if new records arrive in Coupa mid-run.
    anchor_ts    = ds_st.get("anchor_updated_at") if resume else None
    start_offset = ds_st.get("offset", 0)         if resume else 0
    total        = ds_st.get("total_processed", 0) if resume else 0
    total_ins    = ds_st.get("total_inserted", ds_st.get("total_processed", 0)) if resume else 0

    if anchor_ts is None:
        anchor_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n── {key}  →  {cfg['collection']} ──")
    if resume and start_offset:
        print(f"   resuming at offset {start_offset}  (anchor: {anchor_ts})")
    else:
        print(f"   fresh run, anchor: {anchor_ts}")

    token         = get_coupa_token(cfg["scope"])
    coupa_session = requests.Session()
    coupa_session.verify = False
    coupa_session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    })

    offset  = start_offset
    buffer: list = []
    last_ts = ds_st.get("last_updated_at", "n/a")
    first_fresh_flush = not resume

    def flush(*, final: bool = False) -> None:
        """Insert buffered records into Data Storage and save state."""
        nonlocal total, total_ins, last_ts, buffer, first_fresh_flush
        if not buffer:
            return
        try:
            result = insert_batch(ds_session, cfg["collection"], buffer)
        except requests.HTTPError as exc:
            if exc.response.status_code != 401:
                raise
            if username and password:
                print("   [Rossum token expired — refreshing]")
                new_token = refresh_rossum_token(username, password)
            else:
                print("   [Rossum 401 — re-reading token from config]")
                new_token = reload_config_token()
                if not new_token:
                    raise
            ds_session.headers["Authorization"] = f"Bearer {new_token}"
            result = insert_batch(ds_session, cfg["collection"], buffer)  # retry once
        batch_size = len(buffer)
        total     += batch_size
        total_ins += result.inserted
        if first_fresh_flush:
            first_fresh_flush = False
            if batch_size and result.duplicates / batch_size >= 0.9:
                print(RE_REPLICATION_NOTICE)
        last_ts    = _updated_at(buffer[-1])
        buffer     = []
        state[key] = {
            "offset":            offset,
            "anchor_updated_at": anchor_ts,
            "last_updated_at":   last_ts,
            "total_processed":   total,
            "total_inserted":    total_ins,
            **({"completed": True} if final else {}),
        }
        save_state(state, state_path)
        print(f"   flushed → total {total:>7}  offset {offset:>7}  "
              f"last updated_at: {last_ts}")

    while True:
        # Fetch one Coupa page; auto-refresh token on 401
        try:
            page = fetch_page(coupa_session, cfg["endpoint"], cfg["fields"],
                              offset, anchor_ts)
        except requests.HTTPError as exc:
            if exc.response.status_code == 401:
                print("   [Coupa token expired — refreshing]")
                token = get_coupa_token(cfg["scope"])
                coupa_session.headers["Authorization"] = f"Bearer {token}"
                page = fetch_page(coupa_session, cfg["endpoint"], cfg["fields"],
                                  offset, anchor_ts)
            else:
                raise

        if not page:
            flush(final=True)
            if not buffer:  # flush already saved; ensure completed flag is set
                state[key] = {**state.get(key, {}), "completed": True}
                save_state(state, state_path)
            print(f"   complete — {total} records total")
            break

        # Honour --limit
        if limit is not None:
            remaining = limit - total - len(buffer)
            if remaining <= 0:
                flush()
                print(f"   limit {limit} reached — stopping")
                break
            page = page[:remaining]

        page, missing_ids = assign_ids(page, cfg.get("id_key", "id"))
        if missing_ids:
            print(f"   [WARN] {missing_ids} record(s) missing '{cfg.get('id_key', 'id')}' "
                  "— inserted without deterministic _id (will not dedupe)")

        buffer.extend(page)
        offset += len(page)

        if len(buffer) >= DS_BATCH_SIZE:
            flush()

        if limit is not None and (total + len(buffer)) >= limit:
            flush()
            print(f"   limit {limit} reached — stopping")
            break


# ── Supervision (--supervise) ────────────────────────────────────────────────────

def decide(completed: bool, child_alive: bool, restarts: int, max_restarts: int) -> str:
    """Per-dataset supervision decision: done | wait | relaunch | give_up.

    The state file's completed flag — not the child's exit code — is the
    source of truth for completion.
    """
    if completed:
        return "done"
    if child_alive:
        return "wait"
    if restarts < max_restarts:
        return "relaunch"
    return "give_up"


# ── CLI ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-import Coupa master data into Rossum Data Storage."
    )
    parser.add_argument(
        "--config",
        default="coupa_bulk_import.config.json",
        metavar="PATH",
        help="Configuration JSON (default: coupa_bulk_import.config.json next to this script)",
    )
    parser.add_argument(
        "--dataset",
        default="all",
        help="Dataset key, comma-separated list of keys, or 'all' (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N records per dataset (omit for full load)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from state file",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        metavar="PATH",
        help="State file path (default: coupa_import_state_<dataset>.json for a "
             "single explicit --dataset, else coupa_import_state.json)",
    )
    parser.add_argument(
        "--username",
        default=None,
        metavar="EMAIL",
        help="Rossum username for automatic token refresh (avoids manual token updates)",
    )
    parser.add_argument(
        "--password",
        default=None,
        metavar="PASS",
        help="Rossum password for automatic token refresh",
    )
    args = parser.parse_args()

    load_config(Path(args.config))

    keys = resolve_dataset_keys(args.dataset, DATASETS)

    state_path = default_state_path(args.dataset, keys, args.state_file)

    state = load_state(state_path) if args.resume else {}

    ds_session = requests.Session()
    ds_session.headers.update({
        "Authorization": f"Bearer {ROSSUM_TOKEN}",
        "Content-Type":  "application/json",
    })

    for key in keys:
        import_dataset(key, args.limit, args.resume, state, ds_session, state_path,
                       username=args.username, password=args.password)

    print("\nDone.")


if __name__ == "__main__":
    main()
