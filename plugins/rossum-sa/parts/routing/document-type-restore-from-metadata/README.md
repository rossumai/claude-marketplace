# document-type-restore-from-metadata

A TxScript function hook on every **destination** queue of a classic intake. On
`annotation_content.initialize` it sets the document type from the queue's fixed type or from
`annotation.metadata`, so the type decided on the intake queue beats the destination engine's
re-prediction.

**Requires `routing/document-type-record-in-metadata`**, which writes the metadata key this hook
reads. The pair talks through `annotation.metadata`, which `produces`/`consumes` cannot show.

## Parameters

| param | default | meaning |
|---|---|---|
| `«document_type_field»` | `document_type` | schema id of the document-type enum |
| `«metadata_key»` | `intake_document_type` | metadata key that carries the type |

**Both values must match the Record part exactly.** A mismatch fails silently.

## Resolution (first match wins)

1. **Fixed type** — the queue is listed in `settings.fixed_document_types`. Arriving on that queue
   is the decision, so it beats any older type in metadata, and the metadata is rewritten so the next
   hop restores this type. A person can still retype the field later; the Record part captures that.
   Verified live: an arrival with no metadata got the fixed type, and an arrival with older metadata
   got the fixed type and its metadata was rewritten.
2. **Metadata** — `metadata[«metadata_key»]`, the type recorded on the inbox or a later human
   correction.
3. **Nothing resolves** — the field keeps its current value: the engine's prediction, or a value
   mapped from a structured e-invoice. The hook never blanks the field, because a blank destroys a
   usable value. It shows an error only when the field is empty too.

Every value the hook writes must be an option of the destination's enum.

## Ordering

The hook runs at `initialize`, **before** rules on `annotation_imported` evaluate. Verified: a
document whose metadata said "other" was moved by an `annotation_imported` rule keyed on the type
being "other", after this hook had set the field.

A **plain manual move** (no re-extraction) fires no `initialize`, so this hook does not run and a
fixed type is not applied. Accepted gap: the person who moves the document sets the type.

## Hook object requirements

| Setting | Value | Why |
|---|---|---|
| `events` | `["annotation_content.initialize"]` | runs on arrival, after the re-import |
| `queues` | every destination queue, **not** the intake queue | on the inbox the engine's prediction is the decision |
| `sideload` | `["schemas"]` | **required** — `TxScript.from_payload()` raises `PayloadError: Schema sideloading must be enabled!` without it |
| `settings` | `{"fixed_document_types": [{"queue_id": <int>, "document_type": "<value>"}]}` | `[]` when no queue has a fixed type |
| `token_owner` | a user id | supplies `rossum_authorization_token` for the metadata GET/PATCH |
| `config.third_party_library_pack` | `"default"` | provides `rossum_api` |

**`fixed_document_types` is a list of objects on purpose.** `rdc migrate` and `prd2 deploy` remap
ids in settings *values*, never in *keys*. Measured trap: a hook whose settings used queue ids as
dict keys kept the source environment's ids after a migrate.

The production hook also carried `"queues"` in `sideload`. The generalized code reads the queue id
from the annotation URL, so `"queues"` is no longer needed.

The hook uses the `rossum_api` client, proven in production. Most other parts use `urllib`; that
difference is deliberate, not a defect to rewrite.

## What was tested

Production: one deployment (test 2026-11-12/13, production 2026-11-16). The generalized fragment,
with the default seams filled, passed the 4 production test cases on the real `txscript` 1.2.0
runtime with a stubbed `rossum_api` client: a fixed type beats older metadata (other metadata keys
kept), a fixed type with no metadata, a fixed type whose metadata already matches (no PATCH), and a
destination restoring from metadata. Two more cases passed: no metadata keeps the current value
without an error, and no metadata with an empty field shows an error. The repo has no harness for
running part fragments, so the cases are not committed.
