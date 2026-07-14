#!/usr/bin/env python3
"""
coupa_bulk_import.py

Full initial load of Coupa master data into Rossum Data Storage.
Replicates newest-first (sorted by updated_at DESC) so datasets are usable
before the run completes.  Saves progress to coupa_import_state.json after
every DS flush — safe to kill and resume at any time.

Write strategy: insert_many (synchronous, 200 OK) in DS_BATCH_SIZE chunks.
This is faster than async bulk_write and avoids async queue buildup after kill.

Documents are inserted with _id = record[id_key]; re-inserts (smoke tests,
resume overlap, fresh runs over partial loads) are skipped by insert_batch's
_id existence check (duplicate-key parsing retained as fallback).

Usage:
    python coupa_bulk_import.py                      # all datasets, full load
    python coupa_bulk_import.py --dataset purchase_orders
    python coupa_bulk_import.py --dataset users,suppliers
    python coupa_bulk_import.py --limit 1            # smoke test: 1 record each
    python coupa_bulk_import.py --resume             # continue from saved state

    # Recommended for full runs — supervised, sleep-proof, self-healing:
    caffeinate -is python coupa_bulk_import.py --supervise --dataset all
    # (Linux: systemd-inhibit ... ; relaunch with --resume added to skip
    #  completed datasets and continue the rest.)

    # With auto token refresh (password-auth users only — SSO users must
    # pre-stage a long-lived token in the config instead):
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
import os
import signal
import subprocess
import sys
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
        if not ds_cfg.get("collection"):
            raise SystemExit(
                f"Config error: dataset '{ds_key}' has no collection set"
            )
        coll = ds_cfg["collection"]
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
    # dedupe order-preserving — a repeated key must not spawn two racing
    # children (--supervise) or double-process the same dataset unsupervised
    keys = list(dict.fromkeys(k.strip() for k in arg.split(",") if k.strip()))
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
               offset: int, anchor_ts: str, limit: int | None = None) -> list:
    """Fetch one page of records sorted newest-first, anchored to anchor_ts."""
    params = {
        "fields":               json.dumps(fields),
        "order_by":             "updated_at",
        "dir":                  "desc",
        "offset":               offset,
        "updated-at[lt_or_eq]": anchor_ts,
    }
    if limit is not None:
        params["limit"] = limit
    resp = session.get(f"{COUPA_BASE_URL}/{endpoint}", params=params,
                       verify=False, timeout=120)
    resp.raise_for_status()
    return resp.json() or []


def _bisect_count(probe) -> int:
    """Exact record count given probe(offset) -> bool ("a record exists at offset").

    Coupa has no count endpoint, but limit=1&offset=N returns a record iff
    count > N.  Exponential growth finds an empty upper bound, binary search
    pins the boundary — ~2*log2(count) probe calls total.
    """
    if not probe(0):
        return 0
    lo, hi = 0, 1          # invariant: probe(lo) True, probe(hi) unknown
    while probe(hi):
        lo, hi = hi, hi * 2
    while hi - lo > 1:     # probe(lo) True, probe(hi) False
        mid = (lo + hi) // 2
        if probe(mid):
            lo = mid
        else:
            hi = mid
    return hi


# ── Data Storage insert ──────────────────────────────────────────────────────────

_RETRYABLE = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

BatchResult = namedtuple("BatchResult", "inserted duplicates failed")

_DUPLICATE_KEY_CODE = 11000


def _is_duplicate_error(err) -> bool:
    """Mongo duplicate-key: code 11000, or E11000 in the message (shape varies)."""
    if not isinstance(err, dict):
        return "E11000" in str(err)
    return err.get("code") == _DUPLICATE_KEY_CODE or "E11000" in str(err.get("errmsg", ""))


def _existing_ids(session: requests.Session, collection: str, ids: list) -> set:
    """Return the subset of ids already present in the collection."""
    resp = session.post(
        f"{ROSSUM_DS_URL}/data/find",
        json={"collectionName": collection,
              "query": {"_id": {"$in": ids}},
              "projection": {"_id": 1},
              "limit": len(ids)},
        timeout=120,
    )
    resp.raise_for_status()
    return {doc["_id"] for doc in resp.json().get("result") or []}


def _collection_count(session: requests.Session, collection: str) -> int:
    """Total documents in a collection (DS aggregate $count; 0 when empty)."""
    resp = session.post(
        f"{ROSSUM_DS_URL}/data/aggregate",
        json={"collectionName": collection, "pipeline": [{"$count": "total"}]},
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json().get("result") or []
    return int(result[0]["total"]) if result else 0


def _insert_singly(session: requests.Session, collection: str, docs: list) -> tuple[int, int]:
    """Fallback for an opaque batch 400: insert per record, skipping poison docs."""
    inserted = failed = 0
    for doc in docs:
        resp = session.post(
            f"{ROSSUM_DS_URL}/data/insert_many",
            json={"collectionName": collection, "documents": [doc], "ordered": False},
            timeout=120,
        )
        if resp.status_code == 400:
            failed += 1
            print(f"   [WARN] poison document skipped (_id={doc.get('_id', 'n/a')}): "
                  f"{resp.text[:200]}")
            continue
        resp.raise_for_status()
        inserted += len((resp.json().get("result") or {}).get("inserted_ids") or [])
    return inserted, failed


def insert_batch(session: requests.Session, collection: str, records: list,
                 _retries: int = 5) -> BatchResult:
    """Check-then-insert — returns BatchResult(inserted, duplicates, failed).

    The DS REST layer reports duplicate-key write errors as an opaque
    HTTP 400 ("batch op errors occurred") with no write_errors detail
    (live-verified), so duplicates are filtered out with an _id existence
    check BEFORE inserting.  The 200-with-write_errors parsing is kept as
    a belt for servers/races that do return per-document errors.
    Retries the whole check+insert on transient SSL/connection errors;
    the re-run existence check absorbs partially-applied batches.  A record
    that shows up as "existing" only on a retry (not on the first attempt)
    was persisted by the interrupted earlier attempt itself — it is counted
    as "recovered" and added to inserted, not misclassified as a duplicate.
    An opaque batch 400 on the (already-deduped) insert call means one or
    more poison documents, not a duplicate — it falls back to _insert_singly
    so the rest of the batch still lands instead of crash-looping the batch.
    """
    existing_first: set | None = None
    for attempt in range(1, _retries + 1):
        try:
            ids = [r["_id"] for r in records if "_id" in r]
            existing = _existing_ids(session, collection, ids) if ids else set()
            if existing_first is None:
                existing_first = existing
            recovered = len(existing - existing_first)  # persisted by an interrupted earlier attempt
            seen: set = set()
            to_insert = []
            for r in records:
                rid = r.get("_id")
                if "_id" not in r:
                    to_insert.append(r)
                elif rid not in existing and rid not in seen:
                    seen.add(rid)
                    to_insert.append(r)
            duplicates = len(records) - len(to_insert) - recovered
            if not to_insert:
                if duplicates:
                    print(f"   {duplicates} duplicate(s) skipped "
                          "(expected after resume or smoke test)")
                return BatchResult(recovered, duplicates, 0)
            resp = session.post(
                f"{ROSSUM_DS_URL}/data/insert_many",
                json={"collectionName": collection, "documents": to_insert,
                      "ordered": False},
                timeout=120,
            )
            if resp.status_code == 400:
                # duplicates are already filtered — an opaque batch 400 means poison doc(s)
                ins, failed = _insert_singly(session, collection, to_insert)
                if duplicates:
                    print(f"   {duplicates} duplicate(s) skipped "
                          "(expected after resume or smoke test)")
                if failed:
                    print(f"   [WARN] {failed} document(s) failed in this batch (isolated per-record)")
                return BatchResult(ins + recovered, duplicates, failed)
            resp.raise_for_status()
            body         = resp.json().get("result", {}) or {}
            inserted     = len(body.get("inserted_ids") or [])
            write_errors = body.get("write_errors") or []
            late_dups    = sum(1 for e in write_errors if _is_duplicate_error(e))
            failed       = max(len(to_insert) - inserted - late_dups, 0)
            duplicates  += late_dups
            if duplicates:
                print(f"   {duplicates} duplicate(s) skipped "
                      "(expected after resume or smoke test)")
            if failed:
                non_dup = [e for e in write_errors if not _is_duplicate_error(e)]
                print(f"   [WARN] {failed} document(s) failed in this batch. "
                      f"First error: {non_dup[0] if non_dup else 'n/a'}")
            return BatchResult(inserted + recovered, duplicates, failed)
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

    Re-inserting the same record is then skipped by insert_batch's _id
    existence check (duplicate-key parsing retained as fallback) instead
    of creating a second copy.  Records missing the id keep an
    auto-generated _id — never _id: null, because two nulls would
    silently dedupe against each other.  Falsy ids (None, "", 0) are all
    treated as missing — a shared falsy value would otherwise collapse
    distinct records onto the same _id just like two nulls would.
    """
    missing = 0
    for rec in records:
        rid = rec.get(id_key)
        if not rid:
            missing += 1
        else:
            rec["_id"] = rid
    return records, missing


# ── State file ───────────────────────────────────────────────────────────────────

def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"State file {path} is corrupt ({exc}). "
            "Inspect/restore it, or delete it to start the dataset fresh."
        ) from exc


def save_state(state: dict, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)  # atomic — a kill mid-write can't truncate the state file


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

    if resume and ds_st and "total_inserted" not in ds_st:
        # Legacy state file predates total_inserted tracking. total_processed
        # is inflated by duplicates/failures from earlier runs, so seed from
        # a live DB count instead of trusting it.
        total_ins = _collection_count(ds_session, cfg["collection"])
        print(f"   legacy state file (no total_inserted) — seeded from DB count: {total_ins}")
    else:
        total_ins = ds_st.get("total_inserted", 0) if resume else 0

    if anchor_ts is None:
        anchor_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n── {key}  →  {cfg['collection']} ──")
    if resume and start_offset:
        print(f"   resuming at offset {start_offset}  (anchor: {anchor_ts})")
    else:
        print(f"   fresh run, anchor: {anchor_ts}")

    if not resume:
        preexisting = _collection_count(ds_session, cfg["collection"])
        if preexisting:
            print(f"   [WARN] collection '{cfg['collection']}' already holds {preexisting} "
                  "document(s). Records loaded by a pre-deterministic-_id script version "
                  "will NOT dedupe — wipe or blue-green swap before re-replicating (see SKILL.md).")

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


def count_datasets(keys: list[str], state: dict) -> None:
    """Print exact per-dataset counts (offset bisection, anchored like the job)."""
    for key in keys:
        cfg = DATASETS[key]
        anchor = state.get(key, {}).get("anchor_updated_at") \
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        token = get_coupa_token(cfg["scope"])
        session = requests.Session()
        session.verify = False
        session.headers.update({"Authorization": f"Bearer {token}",
                                "Accept": "application/json"})

        def probe(offset: int) -> bool:
            return bool(fetch_page(session, cfg["endpoint"], ["id"],
                                   offset, anchor, limit=1))

        n = _bisect_count(probe)
        done = state.get(key, {}).get("total_processed")
        pct = f"  ({done / n:.1%} processed)" if done and n else ""
        print(f"   {key:<28} {n:>9}  anchor: {anchor}{pct}")


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


def _terminate_children(children: dict) -> None:
    """Best-effort SIGTERM to every still-alive child (interrupt/crash cleanup)."""
    for child in children.values():
        if child.poll() is None:
            child.terminate()


def state_is_completed(state_path: Path, key: str) -> bool:
    """True iff the dataset's state file carries "completed": true."""
    try:
        return bool(json.loads(state_path.read_text()).get(key, {}).get("completed"))
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return False


def read_last_log_line(path: Path) -> str:
    """Last line of a child's log — the 'why' recorded on death/give-up."""
    try:
        lines = path.read_text().strip().splitlines()
    except OSError:
        return "(no log)"
    return lines[-1] if lines else "(empty log)"


def build_child_cmd(dataset: str, config: str, *, resume: bool,
                    username: str | None, password: str | None) -> list[str]:
    """Command line for one supervised child (single dataset, own state file).

    No --limit: --supervise + --limit is refused in main() (a limit-stopped
    child never writes the completed flag).
    """
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()),
           "--dataset", dataset, "--config", config]
    if resume:
        cmd.append("--resume")
    if username:
        cmd += ["--username", username]
    if password:
        cmd += ["--password", password]
    return cmd


def supervise(keys: list[str], args) -> int:
    """Spawn one child per dataset and babysit until all complete or give up.

    Decision table per sweep (see decide()): completed -> done; alive -> wait;
    dead without the flag -> relaunch with --resume up to args.max_restarts,
    then give up on that dataset.  Exit 0 iff every dataset completed.
    """
    Path("logs").mkdir(exist_ok=True)
    logs        = {k: Path(f"logs/{k}.log") for k in keys}
    # Single source of truth for the naming convention: children are always
    # launched with an explicit single --dataset, so they resolve the same
    # per-dataset default as default_state_path(k, [k], None) below.
    state_paths = {k: default_state_path(k, [k], None) for k in keys}
    children: dict = {}
    restarts    = {k: 0 for k in keys}
    status      = {}   # 'running' | 'done' | 'given_up'

    def slog(msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts} supervisor: {msg}", flush=True)

    def launch(key: str, resume: bool) -> None:
        cmd = build_child_cmd(key, args.config, resume=resume,
                              username=args.username, password=args.password)
        with open(logs[key], "ab") as log_f:      # Popen keeps its own fd
            children[key] = subprocess.Popen(cmd, stdout=log_f,
                                             stderr=subprocess.STDOUT)
        status[key] = "running"

    def _sigterm(*_):
        raise KeyboardInterrupt

    prev_sigterm = signal.signal(signal.SIGTERM, _sigterm)

    try:
        # Initial launches live inside the try: an interrupt at any point after
        # handler registration must terminate already-spawned children (not
        # orphan them) and exit 130.
        for key in keys:
            if args.resume and state_is_completed(state_paths[key], key):
                slog(f"{key}: already complete — skipping")
                status[key] = "done"
            else:
                slog(f"{key}: launching{' (--resume)' if args.resume else ''}")
                launch(key, resume=args.resume)

        while any(s == "running" for s in status.values()):
            time.sleep(args.poll_interval)
            for key in keys:
                if status[key] != "running":
                    continue
                child     = children[key]
                # poll() BEFORE the state read: a child seen dead has already
                # done its final state write, so completion is never misread
                # as a death (which would burn a restart slot).
                alive     = child.poll() is None
                completed = state_is_completed(state_paths[key], key)
                action    = decide(completed, alive, restarts[key], args.max_restarts)
                if action == "done":
                    slog(f"{key}: completed ({restarts[key]} restart(s))")
                    status[key] = "done"
                elif action == "relaunch":
                    restarts[key] += 1
                    slog(f"{key}: died (exit {child.returncode}) — resuming "
                         f"(attempt {restarts[key]}/{args.max_restarts}); "
                         f"last log line: {read_last_log_line(logs[key])}")
                    launch(key, resume=True)
                elif action == "give_up":
                    slog(f"{key}: exceeded {args.max_restarts} restarts — giving up "
                         f"(manual --resume needed); "
                         f"last log line: {read_last_log_line(logs[key])}")
                    status[key] = "given_up"
    except KeyboardInterrupt:
        slog("interrupted — terminating children")
        _terminate_children(children)
        return 130
    except Exception:
        slog("unexpected error — terminating children before propagating")
        _terminate_children(children)
        raise
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)

    given_up = [k for k, s in status.items() if s == "given_up"]
    if given_up:
        slog(f"finished with given-up dataset(s): {', '.join(given_up)} — exit 1")
        return 1
    slog("all datasets complete — exit 0")
    return 0


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
    parser.add_argument(
        "--supervise",
        action="store_true",
        help="Spawn one child per dataset and babysit: relaunch dead jobs with "
             "--resume (up to --max-restarts each), exit when all are complete "
             "or given up. Logs go to logs/<dataset>.log",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=60,
        metavar="SEC",
        help="Supervision sweep interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=3,
        metavar="N",
        help="Relaunch attempts per dataset before giving up on it (default: 3)",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="Print exact per-dataset record counts via offset bisection (uses "
             "the run's anchor from the state file when one exists) and exit",
    )
    args = parser.parse_args()

    if args.supervise and args.limit is not None:
        raise SystemExit("--limit cannot be combined with --supervise "
                         "(a limit-stopped child never writes the completed flag; "
                         "run smoke tests unsupervised)")
    if args.supervise and args.state_file:
        raise SystemExit("--state-file cannot be combined with --supervise "
                         "(children always use per-dataset state files)")
    if args.count and args.supervise:
        raise SystemExit("--count cannot be combined with --supervise")

    load_config(Path(args.config))

    keys = resolve_dataset_keys(args.dataset, DATASETS)

    if args.supervise:
        raise SystemExit(supervise(keys, args))

    state_path = default_state_path(args.dataset, keys, args.state_file)

    if args.count:
        state = load_state(state_path)
        count_datasets(keys, state)
        return

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
