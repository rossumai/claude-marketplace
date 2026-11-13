# Delayed Document Confirmation — Full Reference

**Problem.** A document is imported before its vendor, PO or other master-data record exists. Matching fails, automation is blocked, and the annotation stays in `to_review`. When the record arrives later, nothing re-runs matching until a user opens the document.

**Solution.** Two scheduled function hooks from the Rossum Store:

| | Detector | Runner |
|---|---|---|
| Store template | 51 — *Delayed Document Confirmation* | 52 — *Delayed Document Confirmation Runner* |
| Repo file | `delayed_document_confirmation.py` | `delayed_document_confirmation_run.py` |
| Events | `invocation.scheduled`, `invocation.manual` | `invocation.scheduled` |
| Template cron | `*/10 * * * *` (run every 10–60 min) | `0 3 * * *` (run off-peak) |
| Job | Finds changed master-data records, searches for affected annotations, queues their URLs | Sends the queued URLs to `reautomate`, removes the ones it accepted |

Both hooks share a Data Storage **service collection** (default `_delayed_document_confirmation`). Several detectors (for example one per dataset) can feed one runner when they use the same collection.

**Sourcing.** Read from the repo code (`origin/master`, last change 2026-02-25) and the live template objects 51 and 52 (November 2026). Not yet verified end to end on a live org. Points marked *unverified* come from the repo docs only.

## Contents
- [Prerequisites](#prerequisites)
- [Detector settings](#detector-settings)
- [Placeholders](#placeholders)
- [Runner settings](#runner-settings)
- [The reautomate endpoint](#the-reautomate-endpoint)
- [Service collection](#service-collection)
- [Design notes](#design-notes)
- [Defects in older copies](#defects-in-older-copies)

## Prerequisites

1. **`reautomate` feature flag on the org:** `{"reautomate": {"enabled": true}}`. Rossum sets it; customers cannot. An `organization_group_admin` token got **403** from the endpoint on an org where the flag state was not checked (see `iterate`). *Unverified:* that turning the flag on is what makes the same call succeed. Test the runner once on UAT. It ships with only `invocation.scheduled`, but its code accepts any invocation action. For the test, add `invocation.manual` to its events and call `POST /v1/hooks/{id}/invoke`.
2. **A date attribute that marks changed records.** The detector finds changes with a date comparison, so the attribute must be a real Data Storage **date**, not a string. The usual choice is `_last_modified_date`, an MDH feature that Rossum enables on request. The repo docs say it stamps records written through the MDH dataset `PATCH` (merge) endpoint. *Unverified:* whether a full replace (`PUT`), a new record inserted by the import, or a direct Data Storage write also stamps it. Check one freshly imported record before you rely on it, or have the import job write its own date attribute.
3. **Template visibility.** Templates 51 and 52 are shown only to organization groups that Rossum has tagged. Without the tag they are missing from `GET /hook_templates`; ask Rossum.
4. **Token owner.** Create a dedicated admin user (the template guide suggests `delayed_document_confirmation`) and set it as token owner on both hooks. Confirmations and exports done by the runner then show this user, which makes them easy to audit.
5. **One manual run of the detector after you create it.** A manual invocation (`POST /v1/hooks/{id}/invoke`) creates the service-collection indexes, including the two TTL indexes. It does not look for changes; only scheduled runs do. Skip the manual run and the collection never expires anything.

## Detector settings

```json
{
  "updates_lookup": {
    "dataset": "vendors",
    "aggregate_query": [
      {"$match": {"_last_modified_date": {"$gte": "{last_successful_run}"},
                  "vat_id": {"$nin": [null, ""]}}},
      {"$project": {"_id": 0, "vat_id": 1}}
    ],
    "lookback_window_days": 1
  },
  "annotations_lookup": {
    "search": {
      "query": {"$and": [
        {"queue": {"$in": ["https://<org>.rossum.app/api/v1/queues/<queue_id>"]}},
        {"status": {"$in": ["to_review"]}},
        {"field.sender_vat_id.string": {"$in": "{updates.array.vat_id}"}}
      ]}
    }
  },
  "service_collection_name": "_delayed_document_confirmation"
}
```

| Key | Default | Meaning |
|---|---|---|
| `updates_lookup.dataset` | required | Data Storage collection to watch. |
| `updates_lookup.aggregate_query` | required | Aggregation pipeline. Must use `{last_successful_run}`. Each result record must carry the attribute the search placeholder names. |
| `updates_lookup.lookback_window_days` | `1` | Not in the Store guide. Limits how far back the window reaches: on the first run, and after a gap longer than this, the window starts at *now − N days*. |
| `annotations_lookup.search` | required | The complete `POST /v1/annotations/search` body. It needs the top-level `$and` (see `rossum-reference` → *Annotation search*). The hook refuses to run unless the body contains a `queue` key and a `status` key, and fails if it contains no `{updates.…}` placeholder. |
| `service_collection_name` | `_delayed_document_confirmation` | Must match the runner's value. |
| `batch_size` | `100` | Not in the Store guide. Number of changed records per annotation search. |
| `max_workers` | `5` | Not in the Store guide. Parallel search and write requests. |
| `request_timeout` | `30` | Not in the Store guide. Seconds per request. |

## Placeholders

A placeholder replaces the **whole string value** that starts with it, not text inside a longer string.

| Placeholder | Where | Replaced with |
|---|---|---|
| `{last_successful_run}` | `aggregate_query` | `{"$date": "<ISO timestamp>"}`: the `started_at` of this hook's last successful run, limited by `lookback_window_days`. |
| `{updates.array.<attr>}` | `search` | A list of `<attr>` values from one batch of changed records. Use it with `$in`. |
| `{updates.string.<attr>}` | `search` | The same values joined by spaces into one string. |

- **Only one attribute per placeholder type.** If you use two `{updates.array.…}` placeholders with different attributes, the last one wins and both get its values.
- **Every changed record must carry `<attr>`.** A record without it fails the run with a `KeyError`. Filter missing values out in the `$match`, as in the example.

## Runner settings

| Key | Default | Meaning |
|---|---|---|
| `if_modified` | `try_to_confirm` | Passed to `reautomate`; see below. The code default is `try_to_confirm`. The Store guide wrongly says that leaving the default reprocesses only unmodified documents. The other accepted value is not documented — ask Rossum before you change it. |
| `batch_size` | `300` | Annotations per `reautomate` call. Every run processes the **whole** queue, in batches of this size; the setting is not a limit per run. |
| `request_timeout` | `30` | Seconds per request. |
| `service_collection_name` | `_delayed_document_confirmation` | Must match the detector's value. |

## The reautomate endpoint

`POST /api/v1/internal/annotations/reautomate`, body `{"annotations": [<annotation URLs>], "if_modified": "try_to_confirm"}`. The response lists the annotations it accepted, and the runner deletes those from the queue.

- It processes only annotations in **`to_review`**. It skips all others without an error.
- **Annotations a user has not modified:** they go back through `importing`. `annotation_content.initialize` fires, so the matching hooks run again, then the automation decision is made again: `to_review`, or `confirmed`/`exporting`. The engine does **not** extract again, and the content is kept.
- **Annotations a user has modified, with `try_to_confirm`:** it fires `annotation_content.started` and then `confirm`, as if a user opened the document and pressed Confirm. An annotation confirmed this way is **excluded from engine training**.
- It is an internal endpoint, and the flag in [Prerequisites](#prerequisites) gates it. It is not wrapped as an MCP tool. Because it can confirm and export documents, never call it by hand on production.

## Service collection

| Record | Fields | Expires |
|---|---|---|
| Queued annotation (detector upserts it; the runner deletes it once accepted) | `annotation_url`, `hook_id`, `created_at` | **24 h** after `created_at` |
| Control record (detector, one per successful run) | `request_id`, `hook_id`, `started_at`, `finished_at`, `master_data_updates_count`, `annotations_candidates_count` | 14 days after `started_at` |

Index names: `_dynamic_index` (wildcard), `_ttl_created_at`, `_ttl_started_at`. They are created only by the manual run.

For debugging, read the control records (sort by `started_at` descending) to see what each detector run found. Read the queued records to see what the runner will send next.

## Design notes

- **Search on the extracted value, not on the matched one.** The annotation failed to match, so the matched field (for example the vendor id) is empty. Search on the extracted input that matching uses (VAT id, IBAN, PO number), and make it compare equal to the master-data attribute: same type, same normalization. A `field.<id>.string` clause compares strings, so a numeric master-data attribute needs a `$toString` in the `$project` stage, or a `field.<id>.number` clause.
- **Queued annotations expire after 24 hours.** If the runner fails or is off for a day, annotations queued only once are lost. The window does not go back to them, because the detector's window moves forward on each successful run.
- **The window advances only on success.** A failing detector keeps searching from the same `started_at` until the gap passes `lookback_window_days`. After that it jumps forward, and the changes in the skipped period are never searched.
- **The status filter matters.** The detector refuses a search without `queue` and `status`. Keep `status` at `to_review`, because `reautomate` skips everything else anyway.
- **Matching must be able to succeed on re-run.** Re-running only helps if the matching hook runs on `annotation_content.initialize` and its query now finds the new record.

## Defects in older copies

Some deployed hooks run code older than the repo, or than a later fix of the template. When you review an existing deployment, compare its code against the repo and look for these:

- **Detector timestamp parsing (fixed in the repo on 2026-02-25).** Older code parsed the last run's `started_at` only in the format with fractional seconds. A timestamp without them made the run fail. Because the window advances only on success, the failure repeated on every later run. Store template 51 still had the old code in November 2026. If a new hook is created from it, replace its code with the repo version.
- **Runner crash when nothing is accepted.** In the runner's `post_for_processing`, `deleted_count` is assigned only when `reautomate` returns accepted annotations. A batch where none were accepted (for example, all of them already left `to_review`) raises `UnboundLocalError`, and later batches in that run are not sent. A fix of the template is planned; check the deployed code.
