#!/usr/bin/env python3
"""
coupa_bulk_import.py

Full initial load of Coupa master data into Rossum Data Storage.
Replicates newest-first (sorted by updated_at DESC) so datasets are usable
before the run completes.  Saves progress to coupa_import_state.json after
every DS flush — safe to kill and resume at any time.

Write strategy: insert_many (synchronous, 200 OK) in DS_BATCH_SIZE chunks.
This is faster than async bulk_write and avoids async queue buildup after kill.

Records are inserted exactly as received from Coupa, with auto-generated
Mongo _ids — structurally identical to records written by the Coupa import
extension (which upserts by the id FIELD and never touches _id).
Duplicate protection is layered on that id field (config id_key):
a unique partial index is the root guarantee (SKILL.md Phase 1 — verified
at the start of every full run; a confirmed-missing or non-partial index
ABORTS unless --no-unique-index-ok is passed); a pre-insert
existence check before EVERY batch keeps accounting exact and avoids
opaque-400 churn (re-inserts — smoke leftovers, resume overlap, mid-run
anchor-window entries — are skipped, whichever batch they land in); the
SKILL.md Phase 4 duplicate audit verifies the result.
--smoke [N] is the supported config test: insert the newest N records
(default 1), verify them, delete exactly what it landed; no state file is
written and the exit code reflects success.

Usage:
    python coupa_bulk_import.py                      # all datasets, full load
    python coupa_bulk_import.py --dataset purchase_orders
    python coupa_bulk_import.py --dataset users,suppliers
    python coupa_bulk_import.py --smoke              # self-cleaning smoke test
    python coupa_bulk_import.py --smoke 5 --dataset users
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
import random
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
                f"collection '{coll}'. Dedup and smoke cleanup key on the record "
                "id per collection - one collection per dataset required."
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


def make_coupa_session(scope: str) -> requests.Session:
    """Authed Coupa API session — single factory for import, smoke, count."""
    session = requests.Session()
    session.verify = False
    session.headers.update({
        "Authorization": f"Bearer {get_coupa_token(scope)}",
        "Accept":        "application/json",
    })
    return session


# ── Coupa rate limiting + backoff ────────────────────────────────────────────────

_RETRYABLE = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


class RateLimiter:
    """Min-interval throttle for Coupa requests (rate=None disables).

    Per-process only (spec §4.5): the supervisor splits the aggregate cap
    statically across children — no cross-process coordination.
    """

    def __init__(self, rate: float | None):
        self.min_interval = 1.0 / rate if rate else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if not self.min_interval:
            return
        now = time.monotonic()
        wait_for = self._last + self.min_interval - now
        if wait_for > 0:
            time.sleep(wait_for)
        self._last = time.monotonic()


LIMITER = RateLimiter(None)   # configured in main() from --rate / config cap

_BACKOFF_STATUSES = (429, 503)


def coupa_call(fn, *, _attempts: int = 8, _base: float = 5.0, _cap: float = 240.0):
    """Run a zero-arg callable returning a Response, throttled and retried.

    Retries 429/503 and connection-level errors with BLIND exponential
    backoff — Coupa sends no rate-limit headers and no Retry-After.
    Any other HTTP error (401 token expiry, 400 bad query) propagates
    immediately: token refresh and hard failures belong to the caller.
    A 429 under our self-imposed cap means another consumer is draining
    this OAuth client's budget — worth a loud line.
    """
    for attempt in range(1, _attempts + 1):
        LIMITER.wait()
        try:
            resp = fn()
            if resp.status_code in _BACKOFF_STATUSES:
                raise requests.HTTPError(response=resp)
            resp.raise_for_status()
            return resp
        except (*_RETRYABLE, requests.HTTPError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(exc, requests.HTTPError) and status not in _BACKOFF_STATUSES:
                raise
            if attempt == _attempts:
                raise
            wait = min(_base * 2 ** (attempt - 1), _cap) * random.uniform(0.5, 1.0)
            if status == 429:
                print("   [WARN] Coupa 429 under the self-imposed cap — another "
                      "consumer is draining this OAuth client's rate budget")
            what = f"HTTP {status}" if status else type(exc).__name__
            print(f"   [RETRY {attempt}/{_attempts}] Coupa {what} — backing off {wait:.0f}s")
            time.sleep(wait)


def ds_call_with_heal(call, ds_session: requests.Session,
                      username: str | None = None,
                      password: str | None = None):
    """Run a DS-touching callable; on HTTP 401 refresh the token once, retry.

    Prefers --username/--password login; otherwise re-reads rossum.token
    from the config file (a fresh token dropped into the config heals
    running jobs).  Shared by import flushes and every smoke DS call —
    a smoke run must not leave residue because its final delete 401'd.
    """
    try:
        return call()
    except requests.HTTPError as exc:
        # response can be None (adapter/middleware-constructed errors) —
        # re-raise as-is instead of AttributeError'ing inside the heal
        if exc.response is None or exc.response.status_code != 401:
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
        return call()  # retry once — insert_batch re-runs its existence
        #                check, so records persisted before the 401 dedupe


# ── Coupa pagination ─────────────────────────────────────────────────────────────

def ensure_id_field(fields: list) -> list:
    """Cursor pagination needs the id column even when id_key != 'id'."""
    return fields if "id" in fields else ["id", *fields]


def fetch_page(session: requests.Session, endpoint: str, fields: list,
               anchor_ts: str, *, before_id: int | None = None,
               id_gt: int | None = None, limit: int | None = None) -> list:
    """One keyset page: newest-by-id first, anchored, every page an indexed seek.

    before_id: moving upper bound (exclusive) — the cursor.
    id_gt:     static lower bound (exclusive) — partition floor (spec §4.3).
    """
    params = {
        "fields":               json.dumps(fields),
        "order_by":             "id",
        "dir":                  "desc",
        "offset":               0,
        "updated-at[lt_or_eq]": anchor_ts,
    }
    if before_id is not None:
        params["id[lt]"] = before_id
    if id_gt is not None:
        params["id[gt]"] = id_gt
    if limit is not None:
        params["limit"] = limit
    resp = coupa_call(lambda: session.get(f"{COUPA_BASE_URL}/{endpoint}",
                                          params=params, verify=False, timeout=120))
    return resp.json() or []


def fetch_at_rank(session: requests.Session, endpoint: str, anchor_ts: str,
                  rank: int) -> list:
    """The record at ascending-id rank N, or [] past the end.

    limit=1&offset=N — Coupa's only counting primitive (no count endpoint):
    a record exists at rank N iff count > N. Also the boundary-rank probe
    for partition planning (spec §4.3). Deep offsets are slow per call but
    each plan needs only ~2*log2(C) + W-1 of them.
    """
    params = {
        "fields":               json.dumps(["id"]),
        "order_by":             "id",
        "dir":                  "asc",
        "offset":               rank,
        "limit":                1,
        "updated-at[lt_or_eq]": anchor_ts,
    }
    resp = coupa_call(lambda: session.get(f"{COUPA_BASE_URL}/{endpoint}",
                                          params=params, verify=False, timeout=120))
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

class BatchResult(namedtuple("BatchResult",
                             "inserted duplicates failed inserted_values")):
    """One insert_batch outcome.

    inserted_values: id_key values of records THIS call actually landed —
    smoke cleanup deletes exactly these, never a concurrent writer's copy.
    Conservative on ambiguity (unattributable write_errors → empty): smoke
    residue is a better failure mode than deleting someone else's record.
    Always normalized to a FRESH list per instance — never a shared
    mutable default, never an alias of the caller's iterable.
    """
    __slots__ = ()

    def __new__(cls, inserted, duplicates, failed, inserted_values=None):
        return super().__new__(
            cls, inserted, duplicates, failed,
            [] if inserted_values is None else list(inserted_values))

_DUPLICATE_KEY_CODE = 11000


def _is_duplicate_error(err) -> bool:
    """Mongo duplicate-key: code 11000, or E11000 in the message (shape varies)."""
    if not isinstance(err, dict):
        return "E11000" in str(err)
    return err.get("code") == _DUPLICATE_KEY_CODE or "E11000" in str(err.get("errmsg", ""))


def _existing_ids(session: requests.Session, collection: str,
                  id_key: str, values: list) -> set:
    """Return the subset of `values` already present under the id_key field.

    Uses aggregate $match+$group (DISTINCT values) rather than find+limit:
    pre-existing duplicate copies of one id could exhaust a find limit and
    truncate the answer, which would let smoke cleanup delete records it
    never inserted.  (DS REST quirk: find/aggregate take "query"/"pipeline";
    the delete endpoints take "filter".)
    Callers must never pass falsy id values — a falsy id is treated as
    missing and its record always inserts (and is excluded from smoke-delete
    filters), because a shared falsy value would collapse distinct records.
    """
    resp = session.post(
        f"{ROSSUM_DS_URL}/data/aggregate",
        json={"collectionName": collection,
              "pipeline": [{"$match": {id_key: {"$in": values}}},
                           {"$group": {"_id": f"${id_key}"}}]},
        timeout=120,
    )
    resp.raise_for_status()
    return {doc["_id"] for doc in resp.json().get("result") or []}


def _index_status(index: dict, id_key: str) -> str | None:
    """Classify one index spec against the id_key guarantee.

    'ok'          — unique AND carries a partialFilterExpression covering
                    id_key (the exact {"$exists": true} shape or any
                    partial filter that mentions id_key);
    'non_partial' — unique on id_key but WITHOUT a qualifying partial
                    filter: the second id-less document would be rejected
                    as a duplicate null;
    None          — irrelevant to the guarantee.
    """
    keys = index.get("key") or index.get("keys") or {}
    options = index.get("options") or {}
    if not (index.get("unique") or options.get("unique")) or id_key not in keys:
        return None
    pfe = (index.get("partialFilterExpression")
           or options.get("partialFilterExpression") or {})
    return "ok" if id_key in pfe else "non_partial"


def verify_unique_index(session: requests.Session, collection: str,
                        id_key: str) -> str:
    """Check the collection for the unique partial index on id_key.

    The unique partial index is the ROOT duplicate guarantee (SKILL.md
    Phase 1; DS support live-verified): the per-batch existence check
    cannot see races — concurrent writers, records entering the frozen
    anchor window mid-run, backdated updates — but the DB layer rejects
    them all.  Never auto-creates: a collection loaded before this
    guidance may already hold duplicates that would fail the index build,
    so creation is a deliberate operator step.

    Returns 'ok', 'missing', 'non_partial' (unique but would poison-fail
    the second id-less document), or 'unknown' (listing failed — soft
    warn only, never treated as confirmed-absent).
    """
    try:
        resp = session.post(
            f"{ROSSUM_DS_URL}/indexes/list",
            json={"collectionName": collection, "nameOnly": False},
            timeout=120,
        )
        resp.raise_for_status()
        indexes = resp.json().get("result") or []
    except requests.RequestException as exc:
        print(f"   [WARN] could not verify indexes on '{collection}' "
              f"({type(exc).__name__}) — confirm the unique partial index "
              "on the id field manually (see SKILL.md Phase 1)")
        return "unknown"
    statuses = {_index_status(ix, id_key)
                for ix in indexes if isinstance(ix, dict)}
    if "ok" in statuses:
        return "ok"
    if "non_partial" in statuses:
        print(f"   [WARN] collection '{collection}' has a unique index on "
              f"'{id_key}' WITHOUT a partial filter. The second id-less "
              "document inserted would be rejected as a duplicate null and "
              "surface as a poison failure. Drop it and recreate with "
              f'options {{"unique": true, "partialFilterExpression": '
              f'{{"{id_key}": {{"$exists": true}}}}}} (see SKILL.md Phase 1).')
        return "non_partial"
    print(f"   [WARN] collection '{collection}' has NO unique index on "
          f"'{id_key}'. It makes duplicates impossible at the DB layer, "
          "even across races the pre-insert check cannot see (concurrent "
          "writers, mid-run anchor-window entries, backdated updates): "
          f'create __{id_key}_unique_idx with keys {{"{id_key}": 1}} and '
          f'options {{"unique": true, "partialFilterExpression": '
          f'{{"{id_key}": {{"$exists": true}}}}}} (see SKILL.md Phase 1). '
          "NOT auto-created: a collection loaded before this guidance may "
          "hold duplicates that would fail the index build — audit first "
          "(SKILL.md Phase 4).")
    return "missing"


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


def _insert_singly(session: requests.Session, collection: str, docs: list,
                   id_key: str) -> tuple[int, int, int, list]:
    """Fallback for an opaque batch 400: insert per record, classifying 400s.

    Returns (inserted, duplicates, failed, ok_values).  A single-record 400
    is not necessarily a poison document: the unique-index layer rejects a
    RACING duplicate (written by a concurrent writer after the batch's
    existence check) with the same opaque 400.  A post-hoc existence check
    on the record's id decides — present → duplicate (skipped, accounting
    stays correct), absent → poison (failed, warned).  ok_values carries
    the truthy id values of the singles that actually landed.
    """
    inserted = duplicates = failed = 0
    ok_values: list = []
    for doc in docs:
        resp = session.post(
            f"{ROSSUM_DS_URL}/data/insert_many",
            json={"collectionName": collection, "documents": [doc], "ordered": False},
            timeout=120,
        )
        if resp.status_code == 400:
            rid = doc.get(id_key)
            if rid and _existing_ids(session, collection, id_key, [rid]):
                duplicates += 1  # unique index beat us to it — racing writer
                continue
            failed += 1
            print(f"   [WARN] poison document skipped ({id_key}={doc.get(id_key, 'n/a')}): "
                  f"{resp.text[:200]}")
            continue
        resp.raise_for_status()
        landed = len((resp.json().get("result") or {}).get("inserted_ids") or [])
        inserted += landed
        if landed and doc.get(id_key):
            ok_values.append(doc[id_key])
    return inserted, duplicates, failed, ok_values


def insert_batch(session: requests.Session, collection: str, records: list,
                 id_key: str = "id", _retries: int = 5) -> BatchResult:
    """Check-then-insert — returns BatchResult(inserted, duplicates, failed,
    inserted_values).

    Records keep their auto-generated Mongo _id (structurally identical to
    records written by the Coupa import extension); dedup keys on the Coupa
    id FIELD (id_key).  The existence check runs before EVERY batch — a
    boundary-scoped variant (first flush only) was tried and rejected in
    review: records entering the frozen anchor window mid-run (same-second
    creations, backdated writes) shift the DESC stream into "provably
    fresh" batches, and a 401 escaping mid-batch re-enters unchecked.  The
    check costs ~one indexed query per 5k-record batch (~0.2% of wall
    time); it also keeps accounting exact and avoids opaque-400 churn from
    the unique-index layer (see SKILL.md Phase 1).
    The DS REST layer reports duplicate-key write errors as an opaque
    HTTP 400 ("batch op errors occurred") with no write_errors detail
    (live-verified), so the check is what filters duplicates BEFORE
    inserting; the 200-with-write_errors parsing is kept as a belt for
    servers/races that do return per-document errors.
    Retries the whole check+insert on transient SSL/connection errors;
    the re-run existence check absorbs partially-applied batches.  A record
    that shows up as "existing" only on a retry (not on the first attempt)
    was persisted by the interrupted earlier attempt itself — it is counted
    as "recovered" and added to inserted, not misclassified as a duplicate.
    Records whose id_key value is missing or falsy never enter dedup
    queries — they always insert (a shared falsy id would collapse distinct
    records).
    An opaque batch 400 on the (already-deduped) insert call means one or
    more poison documents, not a duplicate — it falls back to _insert_singly
    so the rest of the batch still lands instead of crash-looping the batch.
    """
    existing_first: set | None = None
    for attempt in range(1, _retries + 1):
        try:
            values = [r[id_key] for r in records if r.get(id_key)]
            existing = (_existing_ids(session, collection, id_key, values)
                        if values else set())
            if existing_first is None:
                existing_first = existing
            recovered = len(existing - existing_first)  # persisted by an interrupted earlier attempt
            seen: set = set()
            to_insert = []
            for r in records:
                rid = r.get(id_key)
                if not rid:
                    to_insert.append(r)  # falsy/missing id — always insert
                elif rid not in existing and rid not in seen:
                    seen.add(rid)
                    to_insert.append(r)
            duplicates = len(records) - len(to_insert) - recovered
            if not to_insert:
                if duplicates:
                    print(f"   {duplicates} duplicate(s) skipped "
                          "(expected after resume or smoke test)")
                return BatchResult(recovered, duplicates, 0, [])
            resp = session.post(
                f"{ROSSUM_DS_URL}/data/insert_many",
                json={"collectionName": collection, "documents": to_insert,
                      "ordered": False},
                timeout=120,
            )
            if resp.status_code == 400:
                # duplicates are pre-filtered — an opaque batch 400 means poison
                # doc(s) or a racing duplicate rejected by the unique index;
                # _insert_singly isolates and classifies per record
                ins, late_dups, failed, ok_values = _insert_singly(
                    session, collection, to_insert, id_key)
                duplicates += late_dups
                if duplicates:
                    print(f"   {duplicates} duplicate(s) skipped "
                          "(expected after resume or smoke test)")
                if failed:
                    print(f"   [WARN] {failed} document(s) failed in this batch (isolated per-record)")
                return BatchResult(ins + recovered, duplicates, failed, ok_values)
            resp.raise_for_status()
            body         = resp.json().get("result", {}) or {}
            inserted     = len(body.get("inserted_ids") or [])
            write_errors = body.get("write_errors") or []
            late_dups    = sum(1 for e in write_errors if _is_duplicate_error(e))
            failed       = max(len(to_insert) - inserted - late_dups, 0)
            duplicates  += late_dups
            if not write_errors:
                ok_values = [d[id_key] for d in to_insert if d.get(id_key)]
            else:
                # attribute errors to documents only when every error carries
                # a usable index; otherwise stay conservative (empty) — smoke
                # residue beats deleting a record this call never landed
                bad = {e.get("index") for e in write_errors
                       if isinstance(e, dict) and isinstance(e.get("index"), int)}
                if len(bad) == len(write_errors):
                    ok_values = [d[id_key] for i, d in enumerate(to_insert)
                                 if i not in bad and d.get(id_key)]
                else:
                    ok_values = []
            if duplicates:
                print(f"   {duplicates} duplicate(s) skipped "
                      "(expected after resume or smoke test)")
            if failed:
                non_dup = [e for e in write_errors if not _is_duplicate_error(e)]
                print(f"   [WARN] {failed} document(s) failed in this batch. "
                      f"First error: {non_dup[0] if non_dup else 'n/a'}")
            return BatchResult(inserted + recovered, duplicates, failed, ok_values)
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
                   password: str | None = None,
                   no_unique_index_ok: bool = False,
                   id_range: tuple[int, int] | None = None) -> None:
    cfg   = DATASETS[key]
    ds_st = state.get(key, {})

    if resume and ds_st.get("completed"):
        print(f"\n── {key}: already complete — skipping ──")
        return

    if resume and ds_st and "last_id" not in ds_st:
        raise SystemExit(
            f"Dataset '{key}': state file {state_path} predates keyset "
            "pagination (no last_id) and cannot be resumed by this version. "
            "Delete the state file to restart the dataset fresh — already-"
            "loaded records are skipped by the per-batch existence check — "
            "or finish the run with the previous script version.")

    # Anchor: set once at start of a fresh run; reused on every resume so the
    # run's target set stays frozen even as Coupa keeps changing.
    anchor_ts = ds_st.get("anchor_updated_at") if resume else None
    total     = ds_st.get("total_processed", 0) if resume else 0
    total_ins = ds_st.get("total_inserted", 0)  if resume else 0
    part      = dict(ds_st.get("partition") or {}) if resume else {}
    cursor    = ds_st.get("last_id") if resume else None

    if id_range is not None:
        wanted = {"id_gt": id_range[0] - 1, "id_lte": id_range[1]}
        if resume and part and (part.get("id_gt"), part.get("id_lte")) != \
                (wanted["id_gt"], wanted["id_lte"]):
            raise SystemExit(
                f"Dataset '{key}': --id-range {id_range[0]}:{id_range[1]} does "
                f"not match the range recorded in {state_path} "
                f"(id_gt={part.get('id_gt')}, id_lte={part.get('id_lte')}). "
                "Resume without --id-range, or use a different --state-file.")
        if not resume:
            part = wanted
            cursor = id_range[1] + 1

    id_gt = part.get("id_gt")

    if anchor_ts is None:
        anchor_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    id_key = cfg.get("id_key", "id")
    fields = ensure_id_field(cfg["fields"])

    print(f"\n── {key}  →  {cfg['collection']} ──")
    if part:
        print(f"   partition id ({part.get('id_gt')}, {part.get('id_lte')}]")
    if resume and cursor is not None:
        print(f"   resuming below id {cursor}  (anchor: {anchor_ts})")
    else:
        print(f"   fresh run, anchor: {anchor_ts}")

    index_status = verify_unique_index(ds_session, cfg["collection"], id_key)
    if index_status in ("missing", "non_partial") and not no_unique_index_ok:
        # abort only on a CONFIRMED bad index — 'unknown' (listing failed)
        # already soft-warned and proceeds
        raise SystemExit(
            f"Dataset '{key}': collection '{cfg['collection']}' lacks a "
            f"qualifying unique partial index on '{id_key}' (see the warning "
            "above). Create it first (SKILL.md Phase 1), or re-run with "
            "--no-unique-index-ok to proceed with the per-batch check only "
            "(concurrent-writer races unprotected).")

    if not resume:
        preexisting = _collection_count(ds_session, cfg["collection"])
        if 0 < preexisting < 100:
            # a handful of leftovers (e.g. a hard-killed smoke run) is not a
            # loaded collection — don't scare the operator into a wipe
            print(f"   [NOTE] collection '{cfg['collection']}' already holds {preexisting} "
                  "document(s) — a few leftovers (e.g. from a hard-killed smoke run) "
                  "are harmless: every batch is existence-checked, so they dedupe "
                  "automatically.")
        elif preexisting >= 100:
            print(f"   [WARN] collection '{cfg['collection']}' already holds {preexisting} "
                  "document(s). Existing records are SKIPPED, never updated — if you are "
                  "re-replicating (e.g. a changed field list), clear or blue-green swap "
                  "the collection first (see SKILL.md).")

    coupa_session = make_coupa_session(cfg["scope"])

    buffer: list = []
    last_ts = ds_st.get("last_updated_at", "n/a")
    first_fresh_flush = not resume  # gates the >=90%-duplicates NOTICE only
    first_page        = True        # gates the misconfigured-id_key fail-fast

    def flush(*, final: bool = False) -> None:
        """Insert buffered records into Data Storage and save state."""
        nonlocal total, total_ins, last_ts, buffer, first_fresh_flush
        if not buffer:
            return
        missing_ids = sum(1 for r in buffer if not r.get(id_key))
        if missing_ids:
            print(f"   [WARN] {missing_ids} record(s) missing/falsy '{id_key}' "
                  "in this batch — inserted without dedup protection")
        result = ds_call_with_heal(
            lambda: insert_batch(ds_session, cfg["collection"], buffer, id_key),
            ds_session, username, password)
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
            "anchor_updated_at": anchor_ts,
            "last_id":           cursor,
            **({"partition": part} if part else {}),
            "last_updated_at":   last_ts,
            "total_processed":   total,
            "total_inserted":    total_ins,
            **({"completed": True} if final else {}),
        }
        save_state(state, state_path)
        print(f"   flushed → total {total:>7}  last_id {cursor}  "
              f"last updated_at: {last_ts}")

    while True:
        # Fetch one keyset page; auto-refresh token on 401 (coupa_call already
        # absorbed 429/503/conn blips before a 401 can reach here)
        try:
            page = fetch_page(coupa_session, cfg["endpoint"], fields,
                              anchor_ts, before_id=cursor, id_gt=id_gt)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                print("   [Coupa token expired — refreshing]")
                token = get_coupa_token(cfg["scope"])
                coupa_session.headers["Authorization"] = f"Bearer {token}"
                page = fetch_page(coupa_session, cfg["endpoint"], fields,
                                  anchor_ts, before_id=cursor, id_gt=id_gt)
            else:
                raise

        if not page:
            flush(final=True)
            if not buffer:  # flush already saved; ensure completed flag is set
                state[key] = {**state.get(key, {}), "completed": True}
                save_state(state, state_path)
            print(f"   complete — {total} records total")
            break

        # Fail fast on a misconfigured id_key: a typo'd key would otherwise
        # blind-load the whole dataset with dedup never engaging.
        if first_page and not resume and all(not r.get(id_key) for r in page):
            raise SystemExit(
                f"Dataset '{key}': every record on the first page is missing a "
                f"usable '{id_key}' value — id_key is likely misconfigured for "
                "this dataset (check the config; dedup would never engage)")
        first_page = False

        # Honour --limit
        if limit is not None:
            remaining = limit - total - len(buffer)
            if remaining <= 0:
                flush()
                print(f"   limit {limit} reached — stopping")
                break
            page = page[:remaining]

        buffer.extend(page)
        cursor = page[-1]["id"]   # min id of the (possibly truncated) page

        if len(buffer) >= DS_BATCH_SIZE:
            flush()

        if limit is not None and (total + len(buffer)) >= limit:
            flush()
            print(f"   limit {limit} reached — stopping")
            break


def smoke_dataset(key: str, n: int, ds_session: requests.Session,
                  username: str | None = None,
                  password: str | None = None) -> bool:
    """Self-cleaning smoke test: insert the newest n records, verify, delete.

    Returns True only when nothing failed: no failed inserts, every record
    this run added was found on verification, and the cleanup deleted
    exactly that many — so `--smoke && full-run` is a real gate.

    Never reads or writes a state file.  The delete filter carries exactly
    the id values THIS run's insert landed (BatchResult.inserted_values) —
    never a concurrent writer's copy — with the pre-insert snapshot kept as
    an extra intersection guard.  insert_batch's existence check means a
    re-run after a hard-killed smoke does not double-insert.  Records with
    a missing/falsy id_key value are excluded from the delete filter — they
    stay behind as residue and are warned about.  Every DS call is wrapped
    in the shared 401 heal, so an expiring token cannot leave residue.
    """
    cfg        = DATASETS[key]
    id_key     = cfg.get("id_key", "id")
    collection = cfg["collection"]
    anchor_ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n── smoke {key}  →  {collection} ──")

    def heal(call):
        return ds_call_with_heal(call, ds_session, username, password)

    coupa_session = make_coupa_session(cfg["scope"])

    fields = ensure_id_field(cfg["fields"])
    records: list = []
    cursor = None
    while len(records) < n:
        page = fetch_page(coupa_session, cfg["endpoint"], fields,
                          anchor_ts, before_id=cursor, limit=n - len(records))
        if not page:
            break
        cursor = page[-1]["id"]
        records.extend(page)
    records = records[:n]
    if not records:
        print("   Coupa returned no records — nothing to smoke-test")
        return True

    values = [r[id_key] for r in records if r.get(id_key)]
    if len(values) < len(records):
        print(f"   [WARN] {len(records) - len(values)} record(s) have a "
              f"missing/falsy '{id_key}' — inserted but excluded from the "
              "smoke delete filter (residue stays behind)")

    # Snapshot BEFORE inserting — belt on top of inserted_values: nothing
    # that pre-existed this run may ever enter the delete filter.
    pre_existing = (heal(lambda: _existing_ids(ds_session, collection,
                                               id_key, values))
                    if values else set())
    result = heal(lambda: insert_batch(ds_session, collection, records, id_key))
    print(f"   inserted {result.inserted}, duplicates {result.duplicates}, "
          f"failed {result.failed}")
    ok = result.failed == 0
    if not ok:
        print(f"   [WARN] smoke insert had {result.failed} failure(s)")

    to_delete = [v for v in result.inserted_values if v not in pre_existing]
    if to_delete:
        found   = heal(lambda: _existing_ids(ds_session, collection,
                                             id_key, to_delete))
        missing = len(to_delete) - len(found)
        if missing:
            print(f"   [WARN] {missing} inserted record(s) not found on verification")
            ok = False

        def _delete():
            resp = ds_session.post(
                f"{ROSSUM_DS_URL}/data/delete_many",
                # DS delete endpoints take "filter" — find/aggregate take
                # "query"/"pipeline" (live-verified asymmetry; a 422 usually
                # means the wrong key).
                json={"collectionName": collection,
                      "filter": {id_key: {"$in": to_delete}}},
                timeout=120,
            )
            resp.raise_for_status()
            return resp

        resp = heal(_delete)
        deleted = (resp.json().get("result") or {}).get("deleted_count")
        shown = deleted if deleted is not None else "n/a"
        print(f"   smoke cleanup: deleted {shown} record(s)")
        if deleted != len(to_delete):
            # a missing/unparseable count lands here too, by design:
            # unknown is not success
            print(f"   [WARN] cleanup shortfall: expected to delete "
                  f"{len(to_delete)}, deleted {shown}")
            ok = False
    else:
        print("   smoke cleanup: nothing to delete "
              "(no records added by this run)")
    remaining = heal(lambda: _collection_count(ds_session, collection))
    print(f"   collection '{collection}' now holds {remaining} document(s)")
    return ok


def count_datasets(keys: list[str], state: dict) -> None:
    """Print exact per-dataset counts (offset bisection, anchored like the job)."""
    for key in keys:
        cfg = DATASETS[key]
        # Supervised runs keep per-dataset state files — fall back to them so
        # a --count over the shared state still reuses the run's anchor/progress.
        ds_st = state.get(key) or load_state(default_state_path(key, [key], None)).get(key, {})
        anchor = ds_st.get("anchor_updated_at") \
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        session = make_coupa_session(cfg["scope"])

        def probe(offset: int) -> bool:
            return bool(fetch_page(session, cfg["endpoint"], ["id"],
                                   offset, anchor, limit=1))

        n = _bisect_count(probe)
        done = ds_st.get("total_processed")
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
                    username: str | None, password: str | None,
                    no_unique_index_ok: bool = False) -> list[str]:
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
    if no_unique_index_ok:
        cmd.append("--no-unique-index-ok")
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
                              username=args.username, password=args.password,
                              no_unique_index_ok=args.no_unique_index_ok)
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

def _smoke_arg(value: str) -> int:
    """--smoke N parser with a friendly hint for the '--smoke users' footgun
    (nargs='?' would otherwise eat a dataset name as an invalid int)."""
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a record count, got {value!r} — to smoke-test a "
            f"dataset use '--smoke --dataset {value}'") from None


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
        "--smoke",
        nargs="?",
        const=1,
        type=_smoke_arg,
        default=None,
        metavar="N",
        help="Self-cleaning smoke test: insert the newest N records per "
             "dataset (default 1), verify them, then delete them again. "
             "Writes no state file. N must fit in one DS batch",
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
        "--no-unique-index-ok",
        action="store_true",
        help="Proceed with a full run even when the collection lacks the "
             "qualifying unique partial index on id_key (per-batch existence "
             "check only; concurrent-writer races unprotected)",
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
    if args.smoke is not None and args.supervise:
        raise SystemExit("--smoke cannot be combined with --supervise "
                         "(smoke runs are single-batch and self-cleaning; "
                         "run them unsupervised)")
    if args.smoke is not None and args.resume:
        raise SystemExit("--smoke cannot be combined with --resume "
                         "(smoke runs never read or write state files)")
    if args.smoke is not None and args.count:
        raise SystemExit("--smoke cannot be combined with --count "
                         "(pick one mode)")
    if args.smoke is not None and args.limit is not None:
        raise SystemExit("--smoke cannot be combined with --limit "
                         "(smoke carries its own record count: --smoke N)")

    load_config(Path(args.config))

    if not args.count and not ROSSUM_TOKEN and not (args.username and args.password):
        raise SystemExit(
            "rossum.token is empty in the config and no --username/--password "
            "given — DS writes need one of them (see SKILL.md Phase 0 token "
            "strategy; --count is Coupa-only and exempt)")

    if args.smoke is not None and args.smoke > DS_BATCH_SIZE:
        raise SystemExit(
            f"--smoke {args.smoke} exceeds ds_batch_size ({DS_BATCH_SIZE}) — "
            "a smoke run inserts a single batch (leftovers of a hard-killed "
            "smoke are harmless: every batch of any later run is "
            "existence-checked, so they dedupe automatically)")

    keys = resolve_dataset_keys(args.dataset, DATASETS)

    if args.supervise:
        raise SystemExit(supervise(keys, args))

    state_path = default_state_path(args.dataset, keys, args.state_file)

    if args.count:
        state = load_state(state_path)
        count_datasets(keys, state)
        return

    if args.smoke is not None:
        token = ROSSUM_TOKEN
        if not token:
            # credentials-only config: mint a token up front instead of
            # sending "Bearer " and dying on the first DS call
            token = refresh_rossum_token(args.username, args.password)
        ds_session = requests.Session()
        ds_session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        })
        results = {key: smoke_dataset(key, args.smoke, ds_session,
                                      username=args.username,
                                      password=args.password)
                   for key in keys}
        failed_keys = [k for k, ok in results.items() if not ok]
        if failed_keys:
            raise SystemExit(f"\nSmoke test FAILED for: {', '.join(failed_keys)} "
                             "— see [WARN] lines above. Exit 1.")
        print("\nSmoke test done.")
        return

    state = load_state(state_path) if args.resume else {}

    ds_session = requests.Session()
    ds_session.headers.update({
        "Authorization": f"Bearer {ROSSUM_TOKEN}",
        "Content-Type":  "application/json",
    })

    for key in keys:
        import_dataset(key, args.limit, args.resume, state, ds_session, state_path,
                       username=args.username, password=args.password,
                       no_unique_index_ok=args.no_unique_index_ok)

    print("\nDone.")


if __name__ == "__main__":
    main()
