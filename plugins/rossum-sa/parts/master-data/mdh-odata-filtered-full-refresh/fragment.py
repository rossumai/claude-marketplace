"""OData v4 (SAP behind an API gateway) -> MDH dataset: a FILTERED population, refreshed
in full on every run with one PUT (replace).

Hook object requirements (JSON, not code):
  events      : ["invocation.scheduled", "invocation.manual"]
  schedule    : daily is typical; stagger against sibling importers
  token_owner : <user>       <- supplies rossum_authorization_token for the MDH write
  timeout_s   : 60; RUNTIME_BUDGET_S below it
  payload_logging_enabled : false
Settings : TOKEN_URL, API_URL (entity set incl. sap-client)
Secrets  : client_id, client_secret

When to prefer a full refresh over a watermark sync (measured on a one-time-vendor feed):
  * the server-side filter cuts the population by orders of magnitude (5k of 2.3M rows),
    so the whole sweep fits one run and a cursor would add state and failure modes to save
    nothing;
  * some rows carry NO change stamp at all, so a watermark keyed on it would skip them
    permanently;
  * a PUT also REMOVES rows that left the population, which a PATCH-merge never does - this
    is the only shape that propagates deletions through an OData GET.

Atomicity: every page is accumulated in memory and written ONCE at the end. A PUT mid-sweep
could truncate the dataset if a later page failed; buffering means a failed sweep writes
nothing and yesterday's data stands. An EMPTY result is never written either - that would
wipe a working dataset because the source hiccuped.

Paging: numeric $skiptoken offset, and ONLY AN EMPTY PAGE ENDS THE WALK (a short page may be
the gateway's page cap). Optional $expand of one navigation property is flattened onto the
row with a prefix, so an address or company-code extension becomes scalar columns.
"""

import json
import time

import requests

# ---- part parameters --------------------------------------------------------------------
DATASET = "«dataset»"
ID_KEYS = "«id_keys»"                    # comma-separated
ODATA_FILTER = "«odata_filter»"          # e.g. startswith(Supplier,'<prefix>'); "" = whole entity
EXPAND_NAV = "«expand_nav»"              # single navigation property to flatten; "" = none
SELECT_FIELDS = "«select_fields»"        # comma-separated header fields; "" = no $select
ORDER_BY = "«order_by»"                  # the entity key
PAGE_TOP = «page_top»
RUNTIME_BUDGET_S = «runtime_budget_s»

_DROP_KEYS = ("SAP__Messages",)


def _oauth_token(token_url, client_id, client_secret):
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


def _url(base, params):
    """Append params to an already-encoded base URL. Values pass through verbatim because
    `requests` encodes the query; a $filter's spaces and quotes must survive."""
    sep = "&" if "?" in base else "?"
    return base + sep + "&".join(f"{k}={v}" for k, v in params if v != "")


def _flatten(record):
    row = {k: v for k, v in record.items()
           if k not in _DROP_KEYS and not isinstance(v, (list, dict))}
    if EXPAND_NAV:
        child = record.get(EXPAND_NAV) or {}
        if isinstance(child, list):            # a to-many nav: keep the first, note the rest
            child = child[0] if child else {}
        for k, v in child.items():
            if k not in _DROP_KEYS and not isinstance(v, (list, dict)):
                row[f"{EXPAND_NAV.strip('_')}_{k}"] = v
    return row


def _sweep(api_url, access_token, deadline):
    rows, skip, pages = [], 0, 0
    while True:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"runtime budget exhausted after {pages} page(s)/{len(rows)} "
                               "row(s); nothing written. Raise RUNTIME_BUDGET_S or PAGE_TOP.")
        url = _url(api_url, [
            ("$filter", ODATA_FILTER), ("$expand", EXPAND_NAV), ("$select", SELECT_FIELDS),
            ("$orderby", ORDER_BY), ("$top", str(PAGE_TOP)), ("$skiptoken", str(skip)),
        ])
        resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}",
                                          "Accept": "application/json"}, timeout=90)
        print(f"[sweep page {pages + 1}] skiptoken={skip} -> {resp.status_code}")
        resp.raise_for_status()
        page = resp.json().get("value") or []
        if not page:
            break                               # only an empty page ends the walk
        rows.extend(_flatten(r) for r in page)
        skip += len(page)
        pages += 1
    return rows, pages


def _put_dataset(base_url, rossum_token, records):
    """Full replace. PUT is the point: it also removes rows that left the population."""
    url = f"{base_url}/svc/master-data-hub/api/v1/dataset/{DATASET}"
    data = [("dynamic", "true"), ("encoding", "UTF-8"), ("replace_or_new", "true")]
    data += [("id_keys", k.strip()) for k in ID_KEYS.split(",") if k.strip()]
    files = [("file", ("file", json.dumps(records), "application/json"))]
    headers = {"Authorization": f"Bearer {rossum_token}"}
    resp = requests.request("PUT", url, headers=headers, data=data, files=files, timeout=120)
    if resp.status_code == 404:                 # not created yet
        resp = requests.request("POST", url, headers=headers, data=data, files=files, timeout=120)
    print(f"[mdh] {resp.request.method} {DATASET} n={len(records)} -> {resp.status_code}")
    resp.raise_for_status()


def rossum_hook_request_handler(payload):
    settings, secrets = payload["settings"], payload["secrets"]
    deadline = time.monotonic() + RUNTIME_BUDGET_S
    token = _oauth_token(settings["TOKEN_URL"], secrets["client_id"], secrets["client_secret"])
    rows, pages = _sweep(settings["API_URL"], token, deadline)

    if not rows:
        msg = f"sweep returned 0 rows across {pages} page(s); dataset left untouched."
        print("[summary] " + msg)
        return {"messages": [{"type": "warning", "content": msg}], "operations": []}

    _put_dataset(payload["base_url"], payload["rossum_authorization_token"], rows)
    msg = (f"full refresh: {len(rows)} row(s) across {pages} page(s) -> {DATASET} (replace). "
           "MDH ingestion is asynchronous, so the row count settles shortly after this run.")
    print("[summary] " + msg)
    return {"messages": [{"type": "info", "content": msg}], "operations": []}
