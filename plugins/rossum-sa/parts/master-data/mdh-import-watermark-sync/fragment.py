"""Master-data sync from a paged HTTPS API into a Master Data Hub dataset, with a
watermark so scheduled runs pull only what changed.

Hook object requirements (JSON, not code):
  events      : ["invocation.scheduled", "invocation.manual"]
  schedule    : cron, STAGGERED against sibling importers - MDH has one ingest worker
  token_owner : <user>       <- supplies rossum_authorization_token for the MDH writes
  timeout_s   : 50 (platform cap 60); RUNTIME_BUDGET_S must leave headroom below it
  payload_logging_enabled : false   <- the payload carries `secrets`

Two phases, self-switching, state kept in its own Data Storage collection:

  FULL LOAD    the whole scope, paged.
               FULL_OPERATION "update"  : every page is PATCH-merged on ID_KEYS and the cursor
                                          checkpointed before the next fetch, so a scope too
                                          large for one run drains across as many runs as needed.
               FULL_OPERATION "replace" : pages are buffered and written ONCE as a PUT, so
                                          source-side deletions propagate. Only for a scope that
                                          fits one run; a truncated or empty fetch is REFUSED,
                                          never written - a partial replace deletes the rest.
  INCREMENTAL  a semi-open window [watermark, now - SAFETY_LAG_S). Windows tile exactly: the
               next FROM is the previous TO, no gap and no overlap. Always PATCH-merge - a
               replace here would delete every record outside the window.

The handoff watermark is captured BEFORE the first full-load page, so anything changed while
the full load drained falls into the first incremental window; PATCH-merge makes that overlap
idempotent. The watermark advances only after every page AND the MDH write succeeded. A failed
run records itself and leaves the watermark alone, so the next run retries the same window.

The safety lag closes each window at now - SAFETY_LAG_S: the source's clock and Rossum's are
not synchronised, and a record stamped slightly behind our clock would otherwise land in a
window that had already closed and be lost silently.

Invocation payload flags:
  {"force_full": true}   ignore the stored state and start a full load from page 0
Settings changed_from + changed_to (both, UTC YYYYMMDDhhmmss) run a manual backfill of that
window and deliberately do NOT move the stored watermark.

ADAPT: `SourceApi` is the only class that knows your API. The default speaks the shape a
customer middleware wrapping SAP RFCs typically exposes - a token endpoint returning an API
token, a JSON POST body paged by Skip/Top, a RESULT_FLAG + DATA envelope, and the rows, total
and message under RFC-style ET_*/EV_* keys. Change its constants or its two public methods
and leave the engine alone.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

# ---- part parameters --------------------------------------------------------------------
DATASET = "«dataset»"
STATE_DATASET = "«state_dataset»"
STATE_KEY = "«state_key»"
ID_KEYS = [k.strip() for k in "«id_keys»".split(",") if k.strip()]
PAGE_SIZE = «page_size»
SAFETY_LAG_S = «safety_lag_s»
RUNTIME_BUDGET_S = «runtime_budget_s»
FULL_OPERATION = "«full_operation»"          # "update" (resumable) | "replace" (single run)

MAX_PAGES_PER_RUN = 200
TS_FORMAT = "%Y%m%d%H%M%S"

PHASE_FULL = "full"
PHASE_INCREMENTAL = "incremental"
STATUS_SUCCESS, STATUS_PARTIAL, STATUS_NO_CHANGES = "success", "partial", "no_changes"
STATUS_SKIPPED, STATUS_FAILED = "skipped", "failed"


class SourceApiError(RuntimeError):
    """The source API returned a failure the hook cannot recover from."""


# ---- ADAPT: the source API ------------------------------------------------------------------
class SourceApi:
    """Token-authenticated, POST-paged source. Replace the constants or the two public
    methods (`authenticate`, `fetch_page`) to match your API; keep their signatures.

    Measured behaviour this default encodes (middleware wrapping SAP RFCs, 2026-08):
      * validation failures arrive as HTTP 200 with a success flag and the reason as
        localized PROSE in the message key - the message is the only reliable error channel;
      * the total row count is reported only when asked for on the FIRST page;
      * a delta window that is empty may answer with a "no records match" message rather
        than an empty page - that one message is a terminator, not a failure.
    """

    FLAG_KEY, FLAG_SUCCESS, FLAG_TOKEN_INVALID = "RESULT_FLAG", "S", "E"
    ENVELOPE_KEY = "DATA"
    ROWS_KEY, TOTAL_KEY, MESSAGE_KEY = "ET_DATA", "EV_TOTAL_LINES", "EV_MSG"
    TOKEN_KEYS = ("API_TOKEN", "TOKEN", "ACCESS_TOKEN", "token")
    BENIGN_MESSAGE_FRAGMENT = "no records match"
    # Delta window keys as the deployed API spells them. Copy them VERBATIM from a working
    # call, not from the guide - one production API misspells its lower boundary and
    # silently ignores the corrected spelling, turning every delta into a full load.
    WINDOW_FROM_KEY, WINDOW_TO_KEY = "Changed From", "Changed To"
    INACTIVE = "0"            # inactive date boundary; "" caused HTTP 500 on one gateway

    def __init__(self, settings: dict, secrets: dict) -> None:
        self.base_url = str(settings["api_base_url"]).rstrip("/")
        self.token_path = settings.get("token_path", "/API/GetToken")
        self.data_path = settings["data_path"]
        self.filters = dict(settings.get("filters") or {})
        self.timeout_s = int(settings.get("request_timeout_s", 30))
        self.secrets = secrets
        self.session = requests.Session()
        self.token: Optional[str] = None

    def authenticate(self) -> str:
        response = self.session.post(
            f"{self.base_url}/{self.token_path.lstrip('/')}",
            headers={"API_KEY": self.secrets["api_key"], "USER_ID": self.secrets["user_id"],
                     "USER_PW": self.secrets["user_pw"], "Content-Type": "application/json"},
            data="{}",                      # an empty POST got HTTP 411 Length Required
            timeout=self.timeout_s,
        )
        body = _safe_json(response)
        token = next((str(body[k]).strip() for k in self.TOKEN_KEYS
                      if isinstance(body.get(k), str) and body[k].strip()), "")
        if response.status_code != 200 or not token:
            raise SourceApiError(
                f"authentication failed: HTTP {response.status_code} {response.text[:200]}. "
                "A ConnectTimeout instead means the hook has no outbound internet - patch its "
                "config to force a redeploy; a rejection is the far end (IP allowlist, account)."
            )
        self.token = token
        return token

    def fetch_page(self, skip: int, top: int, window: Optional[tuple[str, str]],
                   first_page: bool) -> tuple[list[dict], Optional[int]]:
        """One page -> (rows, total). `total` is meaningful on the first full-load page
        only (None otherwise)."""
        body: dict[str, Any] = {"Skip": skip, "Top": top, **self.filters}
        if window:
            body[self.WINDOW_FROM_KEY], body[self.WINDOW_TO_KEY] = window
        else:
            body[self.WINDOW_FROM_KEY] = body[self.WINDOW_TO_KEY] = self.INACTIVE
        if first_page and not window:
            body["Request Total Lines"] = "X"

        for attempt in (1, 2):
            if self.token is None:
                self.authenticate()
            response = self.session.post(
                f"{self.base_url}/{self.data_path.lstrip('/')}",
                headers={"API_KEY": self.secrets["api_key"], "API_TOKEN": self.token or "",
                         "Content-Type": "application/json"},
                json=body, timeout=self.timeout_s,
            )
            parsed = _safe_json(response)
            flag = str(parsed.get(self.FLAG_KEY) or "").strip().upper()
            if (response.status_code == 401 or flag == self.FLAG_TOKEN_INVALID) and attempt == 1:
                self.token = None          # token expired or rejected: re-issue once, retry
                continue
            data = parsed.get(self.ENVELOPE_KEY) if isinstance(parsed.get(self.ENVELOPE_KEY), dict) else parsed
            message = str(data.get(self.MESSAGE_KEY) or parsed.get(self.MESSAGE_KEY) or "").strip()
            if response.status_code != 200 or (flag and flag != self.FLAG_SUCCESS):
                raise SourceApiError(f"fetch failed at Skip={skip}: HTTP {response.status_code} "
                                     f"{message or response.text[:200]}")
            if message and self.BENIGN_MESSAGE_FRAGMENT not in message.lower():
                # HTTP 200 + success flag + a message == a rejected request.
                raise SourceApiError(f"fetch rejected at Skip={skip}: {message}")
            rows = data.get(self.ROWS_KEY) or []
            total = int(data.get(self.TOTAL_KEY) or 0) if (first_page and not window) else None
            return rows, total
        raise SourceApiError(f"fetch failed at Skip={skip} after token refresh")


# ---- Rossum side: MDH dataset writes + Data Storage state --------------------------------
class RossumStore:
    def __init__(self, payload: dict) -> None:
        self.base_url = payload["base_url"].rstrip("/")
        self.headers = {"Authorization": f"Bearer {payload['rossum_authorization_token']}"}

    def write_dataset(self, dataset: str, records: list[dict], operation: str,
                      id_keys: list[str], dynamic: str = "false") -> None:
        """PUT (replace) or PATCH (merge on id_keys). A PATCH against a dataset that does
        not exist yet is answered 404 and has to be a POST (create) instead."""
        data = [("dynamic", dynamic), ("encoding", "UTF-8"), ("replace_or_new", "true")]
        method = "PUT"
        if operation == "update":
            method = "PATCH"
            data += [("id_keys", k) for k in id_keys]
        files = [("file", ("file", json.dumps(records), "application/json"))]
        url = f"{self.base_url}/svc/master-data-hub/api/v1/dataset/{dataset}"
        response = requests.request(method, url, headers=self.headers, data=data, files=files,
                                    timeout=60)
        if response.status_code == 404 and method == "PATCH":
            response = requests.request("POST", url, headers=self.headers, data=data,
                                        files=files, timeout=60)
        print(f"[mdh] {method} {dataset} n={len(records)} -> {response.status_code}")
        response.raise_for_status()

    def load_state(self) -> dict:
        response = requests.post(
            f"{self.base_url}/svc/data-storage/api/v1/data/aggregate",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"collectionName": STATE_DATASET, "pipeline": [{"$match": {"key": STATE_KEY}}]},
            timeout=15,
        )
        if response.status_code == 404:
            return {}                        # collection not created yet: first run
        response.raise_for_status()
        result = (response.json() or {}).get("result") or []
        return result[0] if result else {}

    def save_state(self, record: dict) -> None:
        # Upsert by id_keys=key. MDH writes are ASYNCHRONOUS: a record saved by one run is
        # not visible to a run seconds later, so this is a checkpoint, never a lock -
        # schedule above the ingest settle time (~1 min observed).
        self.write_dataset(STATE_DATASET, [record], "update", ["key"], dynamic="true")
        print(f"[state] phase={record['phase']} watermark={record['watermark']} "
              f"status={record['last_status']} n={record['last_record_count']}")


# ---- engine ----------------------------------------------------------------------------
def _safe_json(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _utc_stamp(lag_s: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=lag_s)).strftime(TS_FORMAT)


def _state_record(**kw) -> dict:
    record = {
        "key": STATE_KEY, "phase": PHASE_INCREMENTAL, "cursor": 0, "total_expected": 0,
        "handoff_watermark": "", "watermark": "", "last_status": "", "last_message": "",
        "last_record_count": 0, "last_run_started": "", "last_run_finished": _utc_stamp(),
        "consecutive_failures": 0,
    }
    record.update(kw)
    record["last_message"] = str(record["last_message"])[:500]
    return record


def _drain(api: SourceApi, store: RossumStore, window: Optional[tuple[str, str]],
           start_skip: int, known_total: int, deadline: float,
           buffer: Optional[list]) -> tuple[int, int, int, bool]:
    """Page a frozen scope. With `buffer` None each page is merged as it arrives (one page in
    memory, everything fetched is committed); with a list, pages are collected for a single
    write. Returns (rows_seen, next_skip, total, drained)."""
    seen, skip, total = 0, start_skip, known_total
    for _ in range(MAX_PAGES_PER_RUN):
        if time.monotonic() > deadline:
            return seen, skip, total, False           # out of budget, not out of data
        rows, reported = api.fetch_page(skip, PAGE_SIZE, window, first_page=(skip == 0))
        if reported is not None:
            total = reported
        if rows:
            if buffer is None:
                store.write_dataset(DATASET, rows, "update", ID_KEYS)
            else:
                buffer.extend(rows)
            seen += len(rows)
        skip += PAGE_SIZE
        if not rows:
            return seen, skip, total, True
        if window and len(rows) < PAGE_SIZE:
            return seen, skip, total, True           # deltas report no total: short page ends
        if total and skip >= total:
            return seen, skip, total, True
    return seen, skip, total, False


def rossum_hook_request_handler(payload: dict) -> dict:
    settings = payload["settings"]
    api = SourceApi(settings, payload["secrets"])
    store = RossumStore(payload)
    deadline = time.monotonic() + RUNTIME_BUDGET_S

    state = store.load_state()
    failures = int(state.get("consecutive_failures") or 0)
    run_started = _utc_stamp()
    watermark = str(state.get("watermark") or "")
    resuming_full = str(state.get("phase") or "") == PHASE_FULL
    manual_window = ((str(settings["changed_from"]), str(settings["changed_to"]))
                     if settings.get("changed_from") and settings.get("changed_to") else None)
    do_full = bool(payload.get("force_full")) or resuming_full or (not watermark and not manual_window)

    try:
        if manual_window:
            seen, _, total, drained = _drain(api, store, manual_window, 0, 0, deadline, None)
            message = (f"Manual backfill [{manual_window[0]}, {manual_window[1]}): {seen} records"
                       f"{'' if drained else ' (incomplete - budget exhausted)'}; "
                       "stored watermark left unchanged")
            print(message)
            return {"messages": [{"type": "info", "content": message}], "operations": []}

        if do_full:
            handoff = str(state.get("handoff_watermark") or "")
            start_skip, known_total = int(state.get("cursor") or 0), int(state.get("total_expected") or 0)
            if payload.get("force_full") or not resuming_full:
                handoff = _utc_stamp(SAFETY_LAG_S)     # captured BEFORE the first page
                start_skip, known_total = 0, 0
            buffer: Optional[list] = [] if FULL_OPERATION == "replace" else None
            seen, next_skip, total, drained = _drain(api, store, None, start_skip, known_total,
                                                     deadline, buffer)
            if buffer is not None:
                # Replace is all-or-nothing: a truncated or empty fetch must never be written.
                if not drained or (total and len(buffer) < total):
                    raise SourceApiError(f"refusing to replace: fetched {len(buffer)} of {total} "
                                         "records in one run; use FULL_OPERATION update")
                if not buffer:
                    raise SourceApiError("refusing to replace: source returned zero records")
                store.write_dataset(DATASET, buffer, "replace", ID_KEYS)
            if not drained:
                message = f"Full load in progress: {seen} records this run, resuming at Skip {next_skip} of {total}"
                store.save_state(_state_record(
                    phase=PHASE_FULL, cursor=next_skip, total_expected=total,
                    handoff_watermark=handoff, last_status=STATUS_PARTIAL, last_message=message,
                    last_record_count=seen, last_run_started=run_started))
            else:
                message = f"Full load complete: {seen} records this run, {total} reported; watermark set to {handoff}"
                store.save_state(_state_record(
                    phase=PHASE_INCREMENTAL, total_expected=total, handoff_watermark=handoff,
                    watermark=handoff, last_status=STATUS_SUCCESS, last_message=message,
                    last_record_count=seen, last_run_started=run_started))
        else:
            window_to = _utc_stamp(SAFETY_LAG_S)
            if watermark >= window_to:
                message = (f"Nothing to do: watermark {watermark} is not behind {window_to} "
                           f"(safety lag {SAFETY_LAG_S}s)")
                store.save_state(_state_record(
                    watermark=watermark, handoff_watermark=state.get("handoff_watermark") or "",
                    last_status=STATUS_SKIPPED, last_message=message,
                    last_run_started=run_started, consecutive_failures=failures))
                return {"messages": [{"type": "info", "content": message}], "operations": []}
            seen, _, total, drained = _drain(api, store, (watermark, window_to), 0, 0, deadline, None)
            if not drained:
                # The window did not finish, so it must be retried whole: keep the watermark.
                raise SourceApiError(f"delta window [{watermark}, {window_to}) exhausted the "
                                     f"runtime budget after {seen} records; lower PAGE_SIZE or "
                                     "shorten the schedule interval")
            message = f"Incremental [{watermark}, {window_to}): {seen} changed records"
            store.save_state(_state_record(
                watermark=window_to, handoff_watermark=state.get("handoff_watermark") or "",
                last_status=STATUS_SUCCESS if seen else STATUS_NO_CHANGES, last_message=message,
                last_record_count=seen, last_run_started=run_started))

    except Exception as exc:
        # Record the failure WITHOUT advancing anything, then re-raise so the run is marked
        # failed and failure notifications fire. Recording is itself guarded so a state-write
        # problem can never mask the original error.
        try:
            store.save_state(_state_record(
                phase=PHASE_FULL if do_full else PHASE_INCREMENTAL,
                cursor=state.get("cursor") or 0, total_expected=state.get("total_expected") or 0,
                handoff_watermark=state.get("handoff_watermark") or "", watermark=watermark,
                last_status=STATUS_FAILED, last_message=f"{type(exc).__name__}: {exc}",
                last_run_started=run_started, consecutive_failures=failures + 1))
        except Exception as state_exc:          # noqa: BLE001
            print(f"[state] could not record failure: {state_exc}")
        raise

    print(message)
    return {"messages": [{"type": "info", "content": message}], "operations": []}
