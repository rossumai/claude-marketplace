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
    Edit the constants below before running.  Fill in credentials from your
    Coupa Webhook Import hook settings and your Rossum org URL.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Credentials — fill in before running ────────────────────────────────────────

COUPA_CLIENT_ID     = "<coupa_client_id>"
COUPA_CLIENT_SECRET = "<coupa_client_secret>"
COUPA_BASE_URL      = "https://<customer>.coupahost.com"

ROSSUM_TOKEN   = "<rossum_bearer_token>"
ROSSUM_DS_URL  = "https://<org>.rossum.app/svc/data-storage/api/v1"
ROSSUM_API_URL = "https://<org>.rossum.app/api/v1"   # for token refresh

STATE_FILE    = Path("coupa_import_state.json")  # overridden by --state-file
DS_BATCH_SIZE = 5000                              # records per insert_many call

# ── Dataset definitions — mirror your Coupa Webhook Import hook settings ─────────
#
# Each key maps to one Coupa endpoint.  The `fields` list must exactly match the
# field projection configured in the corresponding Rossum import hook.
#
# Example entry — replace or extend with your actual datasets:
#
# DATASETS: dict[str, dict] = {
#     "purchase_orders": {
#         "endpoint":   "api/purchase_orders",
#         "collection": "purchase_orders",   # Data Storage collection name
#         "id_key":     "id",
#         "scope":      "core.purchase_order.read",
#         "fields": [
#             "id", "created_at", "updated_at", "po_number", "status",
#             {"supplier": ["id", "name", "display_name", "number"]},
#             # ... add all fields from your hook configuration
#         ],
#     },
# }

DATASETS: dict[str, dict] = {
    # TODO: populate from your Coupa Webhook Import hook settings
}


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

def insert_batch(session: requests.Session, collection: str, records: list,
                 _retries: int = 5) -> int:
    """Synchronous insert_many — returns number of inserted documents.

    Uses ordered=False so duplicate-key errors on resume are silently skipped
    rather than aborting the whole batch.  Warns if fewer documents were
    inserted than sent (document-level validation/size failures).
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
            body         = resp.json()
            inserted     = len(body.get("result", {}).get("inserted_ids") or [])
            write_errors = body.get("result", {}).get("write_errors") or []
            if write_errors or inserted < len(records):
                skipped = len(records) - inserted
                print(f"   [WARN] {skipped} document(s) skipped in this batch "
                      f"(write_errors={len(write_errors)}). "
                      f"First error: {write_errors[0] if write_errors else 'n/a'}")
            return inserted
        except _RETRYABLE as exc:
            if attempt == _retries:
                raise
            wait = 2 ** attempt
            print(f"   [RETRY {attempt}/{_retries}] {type(exc).__name__}: {exc} — retrying in {wait}s")
            time.sleep(wait)


def _updated_at(record: dict) -> str:
    """Return the updated_at value; Coupa uses both 'updated_at' and 'updated-at'."""
    return record.get("updated_at") or record.get("updated-at") or "n/a"


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

    def flush(*, final: bool = False) -> None:
        """Insert buffered records into Data Storage and save state."""
        nonlocal total, last_ts, buffer
        if not buffer:
            return
        try:
            insert_batch(ds_session, cfg["collection"], buffer)
        except requests.HTTPError as exc:
            if exc.response.status_code == 401 and username and password:
                print("   [Rossum token expired — refreshing]")
                new_token = refresh_rossum_token(username, password)
                ds_session.headers["Authorization"] = f"Bearer {new_token}"
                insert_batch(ds_session, cfg["collection"], buffer)  # retry once
            else:
                raise
        total   += len(buffer)
        last_ts  = _updated_at(buffer[-1])
        buffer   = []
        state[key] = {
            "offset":            offset,
            "anchor_updated_at": anchor_ts,
            "last_updated_at":   last_ts,
            "total_processed":   total,
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

        buffer.extend(page)
        offset += len(page)

        if len(buffer) >= DS_BATCH_SIZE:
            flush()

        if limit is not None and (total + len(buffer)) >= limit:
            flush()
            print(f"   limit {limit} reached — stopping")
            break


# ── CLI ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-import Coupa master data into Rossum Data Storage."
    )
    parser.add_argument(
        "--dataset",
        choices=[*DATASETS, "all"],
        default="all",
        help="Dataset to import (default: all)",
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
        help="State file path (default: coupa_import_state_<dataset>.json when "
             "--dataset is set, else coupa_import_state.json)",
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

    if args.state_file:
        state_path = Path(args.state_file)
    elif args.dataset != "all":
        state_path = Path(f"coupa_import_state_{args.dataset}.json")
    else:
        state_path = STATE_FILE

    keys  = list(DATASETS) if args.dataset == "all" else [args.dataset]
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
