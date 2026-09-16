"""OData v4 (SAP behind an API gateway) -> MDH dataset sync: full load by CREATION date,
then incremental by LAST-CHANGE date, paged by a numeric $skiptoken.

Hook object requirements (JSON, not code):
  events      : ["invocation.scheduled", "invocation.manual"]
  schedule    : cron, STAGGERED against sibling importers (MDH has one ingest worker)
  token_owner : <user>       <- supplies rossum_authorization_token for the MDH writes
  timeout_s   : 60 (platform cap); RUNTIME_BUDGET_S stays well below it
  payload_logging_enabled : false   <- the payload carries `secrets`
Settings : TOKEN_URL (OAuth2 token endpoint), API_URL (entity set incl. sap-client)
Secrets  : client_id, client_secret

Two phases, self-switching, state in its own Data Storage collection:

  FULL LOAD    $filter=<CREATED_FIELD> ge <FULL_LOAD_START>  &$orderby=<ORDER_BY>
               A full load answers "every record that EXISTS in this scope", and the field
               that defines existence is the creation date. Filtering a full load on the
               change date silently redefines it as "records touched since X" - it misses old
               untouched records and pulls in old ones that were merely edited.
               Ordered by the ENTITY KEY, never by the date: creation dates are day-granular,
               thousands of rows tie on every page boundary, and the server may order ties
               differently per request - offset paging then skips and duplicates rows.
  INCREMENTAL  $filter=<CHANGED_FIELD> gt <watermark>  &$orderby=<CHANGED_FIELD> asc
               watermark = max(<CHANGED_FIELD>) already in the dataset (Data Storage $max),
               never below the handoff captured before the full load started.

Paging: $top=<PAGE_TOP> and an incrementing NUMERIC $skiptoken offset. The gateway's relative
@odata.nextLink is not reliably reconstructible. ONLY AN EMPTY PAGE ENDS A WALK - a short page
may just be the gateway's undocumented per-entity page cap, and treating it as the end would
silently truncate a full load to one page. The offset advances by rows actually returned.

Every page is PATCH-merged on ID_KEYS before the next is fetched and the offset is
checkpointed, so a window of any size drains across as many runs as it takes (bounded per run
by PAGE_LIMIT and RUNTIME_BUDGET_S). A hard kill loses at most one page of redo.

Optional $expand: set EXPAND_NAV to a navigation property (e.g. the header's line-item
collection) and the walk flattens each header's children into standalone rows, stamping the
header's CREATED/CHANGED dates - and the header key, when the child omits it - onto every
child. A child entity that carries NO date of its own can only be windowed this way.
Keep the header's date fields in any $select, or the watermark is stripped and every
incremental restarts from the beginning.

Gateway facts this encodes (measured 2026-08 on an SAP BTP gateway, see sap-reference):
  * a standalone `or` in $filter -> HTTP 403 FORBIDDEN_PARAMETER (SQL threat protection);
    `in (...)` -> HTTP 500. Only `and` survives: a filter can express a RANGE, never a set;
  * OData GET never surfaces deletes - a removed row simply stops appearing. For drift
    detection run a nightly full-replace companion (see mdh-odata-filtered-full-refresh);
  * SAP__Messages and any container value are dropped so MDH columns stay scalar.
"""

import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

# ---- part parameters --------------------------------------------------------------------
DATASET = "«dataset»"
STATE_DATASET = "«state_dataset»"
STATE_KEY = "«state_key»"
ID_KEYS = "«id_keys»"                    # comma-separated; composite keys allowed
CREATED_FIELD = "«created_field»"        # e.g. CreationDate (Edm.Date: bare ISO date literal)
CHANGED_FIELD = "«changed_field»"        # e.g. LastChangeDateTime
ORDER_BY = "«order_by»"                  # the ENTITY KEY, e.g. "PurchaseOrder asc"
FULL_LOAD_START = "«full_load_start»"    # ISO date; "" = skip the full load, incremental only
START_DATE = "«start_date»"              # incremental floor when the dataset is empty
EXPAND_NAV = "«expand_nav»"              # "" = flat entity; else navigation property to flatten
PAGE_TOP = «page_top»
PAGE_LIMIT = «page_limit»                # pages per run
RUNTIME_BUDGET_S = «runtime_budget_s»

PHASE_FULL, PHASE_INCREMENTAL = "full", "incremental"
_DROP_KEYS = ("SAP__Messages",)


# ---- auth ------------------------------------------------------------------------------
def _oauth_token(token_url, client_id, client_secret):
    """client_credentials: Basic auth on the token endpoint first, credentials in the form
    body as the fallback - gateways differ and both were met in the field."""
    resp = requests.post(token_url, data={"grant_type": "client_credentials"},
                         auth=(client_id, client_secret),
                         headers={"Accept": "application/json"}, timeout=30)
    if resp.status_code in (400, 401):
        resp = requests.post(token_url, data={"grant_type": "client_credentials",
                                              "client_id": client_id,
                                              "client_secret": client_secret},
                             headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---- URL helpers ------------------------------------------------------------------------
def _with_params(url, extra):
    """Set/replace query params (list of (key, value)); urlencode-based so a $filter with
    spaces and quotes is encoded exactly once."""
    parts = urlsplit(url)
    keys = {k for k, _ in extra}
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in keys]
    params.extend(extra)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


def _with_param(url, key, val):
    """Replace one param, keeping the already-encoded rest verbatim so $filter is not
    double-encoded. Only for values needing no encoding ($top, $skiptoken)."""
    base = re.sub(r"[&?]" + re.escape(key) + r"=[^&]*", "", url)
    return f"{base}{'&' if '?' in base else '?'}{key}={val}"


# ---- Rossum side -----------------------------------------------------------------------
def _aggregate(base_url, rossum_token, collection, pipeline):
    resp = requests.post(
        f"{base_url}/svc/data-storage/api/v1/data/aggregate",
        headers={"Authorization": f"Bearer {rossum_token}", "Content-Type": "application/json"},
        json={"collectionName": collection, "pipeline": pipeline}, timeout=15,
    )
    if resp.status_code == 404:
        return None                              # collection not created yet
    resp.raise_for_status()
    return resp.json().get("result", [])


def _dataset_max(base_url, rossum_token, fallback):
    """max(CHANGED_FIELD) in the dataset, or fallback when missing/empty."""
    result = _aggregate(base_url, rossum_token, DATASET,
                        [{"$group": {"_id": None, "max": {"$max": f"${CHANGED_FIELD}"}}}])
    if not result or result[0].get("max") in (None, ""):
        return fallback, False
    return result[0]["max"], True


def _load_state(base_url, rossum_token):
    result = _aggregate(base_url, rossum_token, STATE_DATASET, [{"$match": {"key": STATE_KEY}}])
    return result[0] if result else None


def _send_to_mdh(base_url, rossum_token, dataset, records, id_keys, dataset_exists=True):
    """PATCH-merge on id_keys; a 404 means the dataset is not created yet -> POST."""
    keys = [k.strip() for k in id_keys.split(",") if k.strip()]
    data = [("dynamic", "true"), ("encoding", "UTF-8"), ("replace_or_new", "true")]
    data += [("id_keys", k) for k in keys]
    files = [("file", ("file", json.dumps(records), "application/json"))]
    method = "PATCH" if dataset_exists else "POST"
    resp = requests.request(method, f"{base_url}/svc/master-data-hub/api/v1/dataset/{dataset}",
                            headers={"Authorization": f"Bearer {rossum_token}"},
                            data=data, files=files, timeout=60)
    print(f"[mdh] {method} {dataset} id_keys={keys} n={len(records)} -> {resp.status_code}")
    if resp.status_code == 404 and method == "PATCH":
        return _send_to_mdh(base_url, rossum_token, dataset, records, id_keys, dataset_exists=False)
    resp.raise_for_status()


def _save_state(base_url, rossum_token, state):
    record = {"key": STATE_KEY, "phase": state.get("phase", PHASE_FULL),
              "cursor": str(state.get("cursor", "") or ""),
              "handoff_watermark": state.get("handoff_watermark", "") or ""}
    _send_to_mdh(base_url, rossum_token, STATE_DATASET, [record], "key")
    print(f"[state] phase={record['phase']} cursor={record['cursor'] or '<empty>'} "
          f"handoff={record['handoff_watermark']}")


# ---- shaping ---------------------------------------------------------------------------
def _scalar(row):
    return {k: v for k, v in row.items() if k not in _DROP_KEYS and not isinstance(v, (list, dict))}


def _flatten(records):
    """Flat entity -> scalar rows. With EXPAND_NAV, explode each header's children and stamp
    the header's dates (and key, when the child omits it) onto every child row."""
    if not EXPAND_NAV:
        return [_scalar(r) for r in records]
    rows = []
    for header in records:
        stamps = {f: header.get(f) for f in (CREATED_FIELD, CHANGED_FIELD) if header.get(f)}
        for child in header.get(EXPAND_NAV) or []:
            row = _scalar(child)
            for field, value in stamps.items():
                row.setdefault(field, value)
            for key in ID_KEYS.split(","):
                key = key.strip()
                if key in header and not row.get(key):
                    row[key] = header[key]
            rows.append(row)
    return rows


# ---- the walk --------------------------------------------------------------------------
def _walk(ctx, state, start_url, label):
    """Page `start_url` by an incrementing numeric $skiptoken, flushing and checkpointing
    each page. Returns (rows_written, pages, max_changed_seen, drained, cursor, stopped)."""
    base = _with_param(start_url, "$top", PAGE_TOP)
    skip = int(str(state.get("cursor") or "0") or "0")
    pages = rows_written = 0
    max_seen, drained = "", False
    stopped = f"page limit {PAGE_LIMIT} reached; more pending"
    print(f"[{label}] resume skiptoken={skip} top={PAGE_TOP}")
    while True:
        if pages >= PAGE_LIMIT:
            break
        if time.monotonic() >= ctx["deadline"]:
            stopped = "runtime budget reached; more pending"
            break
        resp = requests.get(_with_param(base, "$skiptoken", skip),
                            headers={"Authorization": f"Bearer {ctx['access_token']}",
                                     "Accept": "application/json"}, timeout=60)
        print(f"[{label} page {pages + 1}] skiptoken={skip} -> {resp.status_code}")
        resp.raise_for_status()
        records = resp.json().get("value") or []
        if not records:
            drained, stopped = True, "slice exhausted"
            break
        if len(records) < PAGE_TOP:
            print(f"[{label}] {len(records)} of {PAGE_TOP} requested - gateway page cap, "
                  "continuing (only an empty page ends the walk)")
        rows = _flatten(records)
        if rows:
            _send_to_mdh(ctx["base_url"], ctx["rossum_token"], DATASET, rows, ID_KEYS)
            rows_written += len(rows)
            page_max = max((r.get(CHANGED_FIELD) for r in rows if r.get(CHANGED_FIELD)), default="")
            max_seen = max(max_seen, page_max)
        skip += len(records)
        pages += 1
        state["cursor"] = str(skip)            # checkpoint: a hard kill redoes one page
        _save_state(ctx["base_url"], ctx["rossum_token"], state)
    return rows_written, pages, max_seen, drained, skip, stopped


def _run_full_load(ctx, state):
    flt = f"{CREATED_FIELD} ge {FULL_LOAD_START}"      # Edm.Date literal: bare, unquoted
    url = _with_params(ctx["api_url"], [("$filter", flt), ("$orderby", ORDER_BY)])
    rows, pages, _, drained, cursor, stopped = _walk(ctx, state, url, "full")
    if drained:
        state["phase"], state["cursor"] = PHASE_INCREMENTAL, ""
        stopped = "full load complete -> switching to incremental"
    else:
        state["cursor"] = str(cursor)
    _save_state(ctx["base_url"], ctx["rossum_token"], state)
    return f"full load ({CREATED_FIELD} >= {FULL_LOAD_START}): {rows} row(s) in {pages} page(s). {stopped}."


def _run_incremental(ctx, state):
    handoff = state.get("handoff_watermark") or START_DATE
    ds_max, has = _dataset_max(ctx["base_url"], ctx["rossum_token"], handoff)
    watermark = ds_max if (has and ds_max > handoff) else handoff
    url = _with_params(ctx["api_url"], [("$filter", f"{CHANGED_FIELD} gt {watermark}"),
                                        ("$orderby", f"{CHANGED_FIELD} asc")])
    # The slice is re-derived from the advancing dataset max every run: always walk from 0.
    state["cursor"] = ""
    rows, pages, max_seen, _, _, stopped = _walk(ctx, state, url, "incr")
    state["cursor"] = ""
    _save_state(ctx["base_url"], ctx["rossum_token"], state)
    return (f"incremental ({CHANGED_FIELD} > {watermark}): {rows} row(s) in {pages} page(s); "
            f"watermark -> {max_seen or watermark}. {stopped}.")


def rossum_hook_request_handler(payload: dict) -> dict:
    settings, secrets = payload["settings"], payload["secrets"]
    base_url, rossum_token = payload["base_url"], payload["rossum_authorization_token"]
    ctx = {
        "base_url": base_url, "rossum_token": rossum_token, "api_url": settings["API_URL"],
        "access_token": _oauth_token(settings["TOKEN_URL"], secrets["client_id"],
                                     secrets["client_secret"]),
        "deadline": time.monotonic() + RUNTIME_BUDGET_S,
    }
    state = _load_state(base_url, rossum_token)
    if state is None:
        # First run: pin the handoff BEFORE the full load starts, so the incremental that
        # follows a multi-run full load never restarts from START_DATE.
        handoff, _ = _dataset_max(base_url, rossum_token, START_DATE)
        state = {"phase": PHASE_FULL if FULL_LOAD_START else PHASE_INCREMENTAL,
                 "cursor": "", "handoff_watermark": handoff}
        _save_state(base_url, rossum_token, state)
    if state.get("phase", PHASE_FULL) == PHASE_FULL and FULL_LOAD_START:
        summary = _run_full_load(ctx, state)
    else:
        summary = _run_incremental(ctx, state)
    print(f"[summary] {summary}")
    return {"messages": [{"type": "info", "content": summary}], "operations": []}
