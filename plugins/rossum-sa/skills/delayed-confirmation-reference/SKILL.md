---
name: delayed-confirmation-reference
description: Delayed Document Confirmation reference — the pair of Rossum Store function hooks (Delayed Document Confirmation, template 51, and Delayed Document Confirmation Runner, template 52) that re-run initialize + automation on to_review annotations after the master data they failed to match arrives later. Covers the prerequisites (reautomate org feature flag, MDH _last_modified_date stamping, template visibility, dedicated admin token owner, the manual index-creation run), both settings blocks including keys the Store guide omits, the {last_successful_run} / {updates.array.<attr>} / {updates.string.<attr>} placeholders, the _delayed_document_confirmation service collection and its TTLs, the internal POST /internal/annotations/reautomate endpoint and its if_modified modes, and defects in older copies of the template code. Use whenever the user asks about documents stuck in to_review because a vendor/PO/master-data record was missing at import, re-matching or re-automating annotations after a master-data update, "delayed confirmation", "reautomate", or a deployed hook with either template name — even if they only say "re-run matching when the vendor list updates".
user-invocable: false
---

# Delayed Document Confirmation Reference

A document that fails matching because its master-data record does not exist yet stays in `to_review` until someone opens it again. **Delayed Document Confirmation** fixes that with two scheduled function hooks: a **detector** (template 51) finds master-data records changed since its last run and queues the annotations they affect; a **runner** (template 52) sends the queue to the internal `reautomate` endpoint, which re-runs initialize + automation on each `to_review` annotation. Both hooks are required.

See [reference.md](reference.md) for the full reference. Consult it when:

- Checking whether an org can run it — feature flag, `_last_modified_date`, template visibility.
- Writing the detector's `updates_lookup` aggregation and `annotations_lookup` search with the placeholders.
- Choosing the runner's `if_modified` mode and schedule.
- Debugging: nothing queued, nothing reprocessed, hook errors, the service collection's contents.
- Reviewing an existing deployment that may run an older copy of the template code.

> **Read this first:** the code lives in the `elis-serverless-functions` repo under `generic-functions/experimental/delayed-document-confirmation/` and is marked **experimental**. Rossum's product team is building a native replacement — check its status before starting a new build. The Store template code can lag behind the repo, so compare a deployed hook's code against the repo before you debug it.

Cross-references:

- `mdh-reference` — the matching hook whose result the re-run changes, and the MDH dataset `PATCH` (merge) endpoint that stamps `_last_modified_date`.
- `mongodb-reference` — syntax for the `updates_lookup.aggregate_query` pipeline.
- `rossum-reference` — annotation search syntax (`POST /v1/annotations/search`) used by `annotations_lookup.search`, and Store-template visibility.
- `iterate` — re-firing one annotation by hand; `reautomate` is for batches and can confirm documents.
