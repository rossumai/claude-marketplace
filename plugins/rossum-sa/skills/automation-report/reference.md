# Automation analytics endpoints — empirical reference

Everything below was established by live calls (June 2026) against multiple
production/sandbox organizations (two live orgs — "org A" and "org B" below —
plus a reference payload from a third, "org C"), per the repo rule that API
behavior is characterized by calling it, not guessing. Items marked **Unverified** are
extrapolations.

## `GET /api/v1/queues/{id}/automation_insights`

Available on **every** queue, free of charge. Always responded HTTP 200 across
~200 queues in 2 organizations, including empty queues and queues with
automation already at 100%.

### Response schema (top level)

| Key | Type | Notes |
|-----|------|-------|
| `document_automation_rate` | float 0..1 | Share of documents exported without human touch in the reporting window |
| `document_touchless_rate` | float 0..1 | Empirical ceiling: documents that *would have needed* no correction |
| `is_aurora_queue` | bool \| null | `null` observed on queues with zero traffic in the window |
| `document_automation_timeseries` | list of per-day rows | `{date, automated_count, non_automated_count, touchless_count, touched_count}`; integers on insights |
| `document_blockers` | list | `{blocker, granularity, document_count, example_annotation_ids}` — up to **50** IDs per blocker |
| `datapoint_statistics` | list per schema field | `{schema_id, blocked_document_counts, estimated_error_rate, confidence_threshold, is_quality_estimate, blockers[]}` |
| `estimated_error_rate_timeseries` | list | `{date, error_rate_estimate, is_quality_estimate, window_document_count}`; empty when automation never ran |

`datapoint_statistics[].estimated_error_rate` is `null` when no automation has
run on the queue; populated floats appear in projections and on queues with
automation history.

### Blocker vocabulary (observed)

From a survey of 299 queues across 3 organizations (occurrence = number of
queues where the blocker appeared at least once):

| Granularity | Blocker | Queues | Notes |
|-------------|---------|--------|-------|
| `datapoint` | `low_score` | 118 | confidence below threshold — *tunable* |
| `datapoint` | `error_message` | 110 | validation/matching logic — *rules* |
| `datapoint` | `no_validation_sources` | 70 | field has no validation source configured — **not in the original task vocabulary** |
| `datapoint` | `extension` | 28 | field never extracted — *structural* |
| `datapoint` | `failed_checks` | 17 | Rossum "checks" feature — **not in the original task vocabulary** |
| `annotation` | `automation_disabled` | 78 | queue/annotation setting |
| `annotation` | `error_message` | 22 | annotation-level validation |
| `annotation` | `extension` | 1 | rare annotation-level extension blocker |
| `annotation` | `suggested_edit_present` | (reference payload) | seen on the org C reference queue |

Expect the vocabulary to grow; the analyzer buckets unknown blockers under
`other` (with the blocker names listed) instead of failing or silently
dropping them.

`blocked_document_counts` keys mirror the per-field `blockers[].blocker` values
(e.g. `{"low_score": 48, "extension": 10}`).

### Reporting window

- The window is **data-driven**, not "last N days": an org A queue returned
  2026-03-12 → 2026-04-14 (34 rows) in June 2026; the org C reference
  payload spanned 92 calendar days including zero-volume weekend rows.
- **Query parameters are ignored.** `?from/to`, `?begin_date/end_date`,
  `?date_from`, `?period` all returned the identical timeseries (HTTP 200, same
  row count). There is no accepted parameter we could find; the dashboard's
  window is server-side.

### Edge cases

| Case | Behavior |
|------|----------|
| Empty queue (no traffic) | 200 with zeroed rates, empty lists, `is_aurora_queue: null` |
| Automation already enabled | 200; `document_automation_rate` > 0; error-rate fields populate |
| Non-existent queue / no permission | 404 `{"detail": "Not found."}` |
| Wrong token | 401 |

## `POST /api/v1/queues/{id}/automation_projections`

**This is a POST endpoint.** The original task description assumed GET; live
calls return `405 {"detail": "Method \"GET\" not allowed."}` on GET for every
queue. The simulation can take seconds to tens of seconds — use a generous
timeout (the MCP tool uses 130 s).

### Request body

```json
{"fields": []}
```

- `fields` is **required** (missing → `400 {"fields": ["This field is required."]}`).
- `[]` is valid and lets the server pick scenarios.
- Each entry must be an object with required `error_rate_limit` (float, max
  acceptable estimated error rate) and optional `schema_id`:
  `{"schema_id": "account_num", "error_rate_limit": 0.05}`.
  Omitting `error_rate_limit` → `400 {"fields": [{"error_rate_limit": ["This field is required."]}]}`.
  A bare string entry → `400 Invalid data. Expected a dictionary, but got str`.

### Response schema

`{total_document_count, used_document_count, baseline, projections[]}` where
`baseline` and each projection mirror the insights shape, with differences:

- counts in `document_automation_timeseries` are **floats**;
- `example_annotation_ids` lists are empty;
- `estimated_error_rate` is populated (float) per field and per scenario;
- `estimated_error_rate_timeseries` is empty.

### Scenario count

Server-determined, not client-controllable: the same `{"fields": []}` body
returned 1 scenario on an org A queue (73 docs), 2 on the org C reference
queue (7,977 docs), and 8 on two org B queues (113 and 1,370 docs). Passing
multiple `fields` entries did not change the count. **Unverified**: what drives
the count (data volume? confidence spread?).

### Sampling / dilution — two distinct effects

1. `used_document_count` can equal `total_document_count` while the projection
   **timeseries** covers only a fraction of the insights window: org C
   reference: timeseries total 2,055 of 7,977 insights-window documents
   (25.8%) and blocker counts ~25.7% of insights counts. Simulated automation
   applies only from a recent cut-over date — early days show
   `automated_count: 0` even in scenarios.
2. Consequently the headline `document_automation_rate` is **diluted** by
   pre-simulation history: 10.8%/14.5% headline vs ~41.9% for both scenarios
   recomputed over the post-cutover window (from the first day with
   `automated_count > 0` onward) on the reference queue. Always report the
   corrected rate.

### Unavailability modes (live-captured)

| Mode | Response |
|------|----------|
| Queue lacks reviewed data / simulation has nothing to use | **HTTP 200** with `"projections": []` and `used_document_count: 0` — not an HTTP error |
| Non-existent queue / no permission | 404 `{"detail": "Not found."}` |
| GET instead of POST | 405 `method_not_allowed` |
| Missing/invalid `fields` | 400 (see request body above) |

No 402/403/409/425 was observed. The MCP tool `rossum_get_automation_projections`
maps all of these to `{available: false, status_code, reason}` and treats
200-with-empty-projections as unavailable.

## Enrichment sources (existing MCP tools)

- `rossum_get_queue` → automation configuration is in **top-level queue
  fields**, not `settings.automation` (live-verified on org A queues):
  `automation_enabled` (bool), `automation_level` (`never`/`confident`/
  `always`), `default_score_threshold`, `quality_spot_check_percentage`, and
  `settings.suggested_edit` (`disable`/...). A queue with
  `automation_level: never` shows the `automation_disabled` blocker on every
  reviewed document.
- `rossum_get_schema` → field inventory; formula/manual workflow fields (e.g.
  `approval_name`, `sender_id_match`) carry `error_message` volume but are
  absent from projections (`unsimulated_fields` in `analyze.py` output).
- `rossum_get_annotation` on `example_annotation_ids` → resolved
  `automation_blocker` items for root-cause sampling.

## Known method caveats

- The active-window correction recomputes `automated / (automated + non_automated)`
  over the post-cutover window: all days from the first day with
  `automated_count > 0` onward, **including** later days with volume but zero
  automation (excluding them would bias the rate upward). The task's reference
  value for scenario 1 (56.1%) differs from this method's result — the exact
  dashboard denominator is **Unverified**; this method is deterministic and
  documented.
- Rule of three: a measured 0% error rate only bounds the true rate below ~3/N
  at 95% confidence, where N is the number of *automated* documents in the
  scenario (trials for a per-automated-document error rate), not the whole
  sample.
