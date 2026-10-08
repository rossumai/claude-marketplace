# coupa-import-delta

The `settings` of one Coupa master-data import: one Coupa endpoint (or a slice of it) kept in step
with one MDH dataset — upsert by `id`, ordered by `id`, incremental on `updated-at`. It runs as a
Rossum-hosted **job** hook (CIB 2.0, Store template 55); the same settings also run on the CIB 1.x
webhook ([variant](#variant-cib-1x-webhook)). No code — the Coupa import service does the paging,
auth and upserts.

## When to use it

- A **slice CIB does not ship**: GL accounts, cost objects or another segment out of
  `lookup_values`, a child level of an account tree, exchange rates for one currency, records
  created after a cutoff.
- **Coupa master data in a project not built on CIB.**

Not for recreating the CIB baseline's own imports — deploy those with the baseline
(`coupa-baseline-reference` §4).

## Params

| param | default | meaning |
|---|---|---|
| `«coupa_base_url»` | required | tenant URL with trailing slash |
| `«client_id»` | required | Coupa OAuth client id; the secret goes in hook secrets as `client_secret` |
| `«client_scope»` | required | scope for this endpoint (e.g. `core.common.read` for units of measure) — an ungranted scope fails the token call with 400 `invalid_scope` |
| `«endpoint»` | required | e.g. `api/lookup_values` |
| `«dataset_name»` | required | target dataset |
| `«delta_from»` | `${last_modified_date}` | lower bound of `updated-at` ([below](#delta_from)) |
| `«records_per_request»` | `50` | page size; 50 is the maximum |
| `«max_run_time_s»` | `36000` | job run limit (60–36,000 s) |

**Replace the literal `fields` list** with what matching needs (parameters cannot be lists). Keep
`id`, the status field (`active`) and `updated_at`. Nested objects go in as single-key objects:
`{"parent": ["id", "name", "active"]}`. Records keep Coupa's field names — `updated_at` is stored
as `updated-at`; write MDH queries against the stored names.

Three things in the fragment are fixed on purpose:

- **`order_by: id`.** The service pages by offset. A non-unique key (`created_at`) lets tied
  records reshuffle between pages, so some are fetched twice and others never — measured on a
  ~49k-row slice: the row count matched Coupa exactly while ~2.7% of ids were duplicates and as
  many were missing. `id` is unique, so offset paging is exact.
- **No `dir`.** `dir: desc` makes the 1.x service read one page and hang until killed.
- **No `offset` / `limit` in `query`.** The job pages with them itself and rejects them there.

## Install as a job hook

1. The organization must see **template 55** ("Coupa master data import"): only organization
   groups with the **`integrations_team`** visibility tag do (`GET /hook_templates/55` → 404
   otherwise). Ask Rossum to add the tag.
2. Create the hook: `POST /hooks/create` (`rossum_create_hook_from_template`) with `hook_template`,
   `name`, `events: ["invocation.scheduled", "invocation.manual"]` and a **`token_owner`** — a user
   of the organization. Without one every run fails at once with `Set the token owner of the
   extension.`
3. PATCH `settings` with the filled fragment, the cron (`config.schedule.cron`; stagger hooks so
   they do not start together) and the secret `{"client_secret": "…"}`. No queues.
4. Before the first run, create the dataset's collection with the **unique partial index** on
   `id` (`coupa-bulk-replication` → Phase 1): it blocks duplicates at the database whatever the
   import does.

Hooks created from template 55 are `private`: fields the template sets are hidden and read-only
afterwards — to change one, create a new hook.

## Running it

- Start it by hand with `invocation.manual` (`POST /hooks/{id}/invoke`).
- Watch it with `rossum_list_hook_logs` + `include_run_log=true`: `waiting` → `running`, one
  `Imported N records` line per page, then `completed` or `failed (reason: …)`. The hook-log row
  alone has an empty `message`.
- Expect minutes in `waiting` before a worker takes the job; a small slice spends most of its time
  queued.

## `delta_from`

- **`${last_modified_date}`** (default) — the service fills in the start of the dataset's last
  successful import. With **no history** (a new hook, a new dataset) it falls back to the epoch, so
  the **first run is a full import** (verified on the 1.x service; assume the same for the job).
- **A fixed ISO timestamp** — when the first run would be too big: after a bulk load
  (`coupa-bulk-replication`, Phase 5: use the bulk run's `anchor_updated_at`), or to break a
  failure loop where every run is killed before it succeeds and the window never moves
  (`coupa-bulk-replication` → the 60-minute kill). Restore `${last_modified_date}` only after a run
  you confirmed carried data.

## Filters

Add filter keys to `query`. Each needs a reason, and the reason decides whether it is safe on an
incremental import:

| slice | query keys | why it is safe |
|---|---|---|
| **lookup segment** (the common case: one segment of `lookup_values`, top level only) | `"lookup[name][in]": "<segment name>"`, `"parent[id][blank]": "true"` | the lookup a value belongs to and its parent do not change. `parent[blank]` (without `[id]`) returns a Coupa 400 |
| **child level** | `"parent[id][in]": "<parent ids>"` | a record does not move between parents; expect the filter to be slow on Coupa's side (a nested-association filter) |
| **value slice** | e.g. `"to-currency[code]": "EUR"` on exchange rates | the value never changes for a record |
| **date floor** | `"created-at[gt_or_eq]": "<cutoff>"` | creation dates never change; limit history by creation date, not by status |
| **status** | `"active": "true"` | **usually not safe.** A record deactivated after the first load no longer matches, is never fetched again, and stays `active: true` in the dataset. Import the field and filter in the matching query — `coupa-baseline-reference` → Status filters on incremental imports |

For a small, status-scoped population that must stay exact, a full replace (`method: replace`,
no `updated-at` key) rewrites the dataset every run instead.

## Verify

- **Distinct ids, not the row count:** count distinct `id` values in the dataset and compare with
  Coupa's count for the same query (`coupa-bulk-replication` `--probe` gives exact counts).
- **Duplicate audit:** `coupa-bulk-replication` → Phase 4, step 2.
- **Spot-check the delta:** after a run, query Coupa for records updated since `delta_from` and
  confirm those ids carry the new `updated-at` in the dataset.

## Variant: CIB 1.x webhook

The same `settings` run on the 1.x scheduled-imports service:

- a **webhook** hook, `config.url` = `https://<region host>/svc/scheduled-imports/api/coupa/v1/import`,
  events `invocation.scheduled` (+ `invocation.manual`), the cron in `config.schedule.cron`, no
  queues, a `token_owner`;
- no template needed; declare `secrets_schema` with the `client_secret` property, or the UI shows no
  secret field;
- drop `job_run_settings`; the service kills a run after 60 minutes, and a killed run does not
  count as a success;
- health lives in the service's **operation log**, not the hook log — see `coupa-bulk-replication`
  → "A hook that fails every run".
