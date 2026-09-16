# mdh-import-watermark-sync

A scheduled function hook that keeps a Master Data Hub dataset in step with a paged HTTPS
API: one **full load**, then **incremental windows** on a persisted watermark. It is the
engine behind every "pull vendors / purchase orders / goods receipts from the ERP every 30
minutes" feed, independent of which API dialect the source speaks.

Use it when the source can answer *"what changed between T1 and T2"*. If it cannot, or the
population is small and filtered, use `master-data/mdh-odata-filtered-full-refresh` (full
replace every run) instead. If the source is OData v4 behind an API gateway, use
`master-data/mdh-odata-import-skiptoken-sync`, which is this engine with the OData dialect
already adapted.

## What the engine guarantees

| Concern | Mechanism |
|---|---|
| A scope larger than one run | full load PATCH-merges each page and checkpoints the cursor; the next run resumes (`full_operation: update`) |
| Deletions in the source | `full_operation: replace` buffers the scope and PUTs once; a truncated or empty fetch is **refused**, because a partial replace deletes the rest |
| Nothing lost between phases | the handoff watermark is captured *before* the first full page; the first incremental re-pulls anything changed during the load, and PATCH-merge makes that idempotent |
| No gap, no overlap between runs | windows are semi-open `[from, to)` and the next `from` is the previous `to` |
| Source clock ahead/behind ours | each window closes at `now - safety_lag_s` |
| A failed run | records `failed`, increments `consecutive_failures`, **leaves the watermark**, re-raises so the run is marked failed |
| A backfill | `changed_from`/`changed_to` in settings run that window and never move the watermark |

The state record (`«state_dataset»`, `key = «state_key»`) is also the health check:

```
{ key, phase, cursor, total_expected, handoff_watermark, watermark,
  last_status: success | partial | no_changes | skipped | failed,
  last_message, last_record_count, last_run_started, last_run_finished, consecutive_failures }
```

Read it with `data_storage_find(collectionName="«state_dataset»", query={})`.

## Params

| param | default | meaning |
|---|---|---|
| `«dataset»` | required | MDH dataset receiving the rows |
| `«state_dataset»` | required | Data Storage collection for the state record; one per feed |
| `«state_key»` | required | `key` of the state record |
| `«id_keys»` | required | comma-separated natural key. **Measure uniqueness first** — on one feed supplier numbers repeated across company codes, so the key had to be `SUPPLIER,COMPANY_CODE` |
| `«page_size»` | `2000` | rows per page |
| `«safety_lag_s»` | `120` | window closes this far behind now |
| `«runtime_budget_s»` | `40` | must leave headroom under the hook timeout (60 s cap; a **manual invoke is capped at 30 s** and a run that exceeds it completes server-side but loses its report) |
| `«full_operation»` | `update` | `update` (resumable) or `replace` (single run, propagates deletions) |

## Hook settings and secrets (not seams — per environment)

```json
{ "api_base_url": "https://<middleware-host>", "token_path": "/API/GetToken",
  "data_path": "/API/V1/<endpoint>", "filters": {"Company code": ""},
  "request_timeout_s": 30, "changed_from": null, "changed_to": null }
```

Secrets: `api_key`, `user_id`, `user_pw` (or whatever your `SourceApi.authenticate` reads).
Set `payload_logging_enabled: false` on the hook — the payload carries the secrets.

## Adapt: `SourceApi`

Everything API-specific lives in one class. The default speaks the shape a customer
middleware wrapping SAP RFCs exposes: a token endpoint, JSON POST paging by `Skip`/`Top`, a
`RESULT_FLAG` + `DATA` envelope, and rows / total / message under RFC-style `ET_*` / `EV_*`
keys. Things the default already handles because they were measured:

- **Validation errors arrive as HTTP 200 with the success flag set** and the reason as
  localized prose in the message key. The message is the only reliable error channel.
- **The total is reported only when requested, on the first page** (`"Request Total Lines": "X"`);
  later pages report 0. Capture it on page 1 and store it — `total_expected` in the state.
- **An empty delta window may answer with a "no records match" message** rather than an empty
  page. That single message is a terminator; every other message is a failure.
- **Inactive date boundaries must be `"0"`, not `""`** — the empty string produced HTTP 500 with
  no message on one gateway.
- **Copy the window key names verbatim from a working call.** One deployed API misspells its
  lower boundary (`"Chage From Date"`); the corrected spelling is silently ignored and every
  delta becomes a full load.

Change the class constants first; change `authenticate` / `fetch_page` only if the request or
envelope shape differs. Keep the signatures — the engine calls exactly those two.

## Operating it

- **Stagger schedules** across feeds (`*/30`, `15,45`, `5,35`). MDH has one ingest worker; two
  loads landing together contend, and a row count taken right after a load is a queue snapshot.
- **The state record is not a lock.** MDH writes are asynchronous; two invocations seconds apart
  both read "no state" and both run a full load. Harmless (idempotent), but never schedule
  below the ingest settle time (~1 minute observed).
- **A `ConnectTimeout` is not the far end.** Serverless networking is applied when the function
  is deployed; after enabling outbound internet, patch the hook config to force a redeploy.
  A rejection *with* a response (an error code) is the far end — IP allowlist or account.
- The delta window selects *parent* records changed in the window and returns *all* their
  children (measured on a PO feed: 5 orders, 113 items). Delta volume scales with children per
  parent, not with the number of changes, and the change stamp is parent-level.

See `sap-reference` → *Shape B* for the full measured contract this was lifted from.
