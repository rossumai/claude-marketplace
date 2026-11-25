# document-type-record-in-metadata

A function hook that records the document type decided on the intake queue in
`annotation.metadata`, so the type survives the move to its destination queue.

A `change_queue` move with `reimport: true` re-extracts the document on the destination, and the
destination's engine re-predicts every captured field, the type included (measured). Without a
carrier, a person's choice on the inbox is lost. Metadata travels with the annotation; extracted
content does not.

```
inbox initialize ──> this hook writes metadata[«metadata_key»]
person corrects the type (any queue) ──> this hook overwrites it
destination initialize ──> routing/document-type-restore-from-metadata writes it back into the field
```

**Requires `routing/document-type-restore-from-metadata`.** The pair talks through
`annotation.metadata`, which `produces`/`consumes` cannot show, so `requires` declares it. This hook
alone writes a key that nothing reads.

## Parameters

| param | default | meaning |
|---|---|---|
| `«document_type_field»` | `document_type` | schema id of the document-type enum |
| `«metadata_key»` | `intake_document_type` | metadata key that carries the type |

**Both values must match the Restore part exactly.** A mismatch fails silently: Record writes one
key, Restore reads another, and the engine's re-prediction stays.

## The two paths

1. **Intake** — `annotation_content.initialize` on a queue in `settings.intake_queue_ids`. The inbox
   is where the type is decided, so its current value is recorded. The engine's prediction is in the
   `initialize` payload (verified from a logged payload: `rir_confidence` present, no `human`
   source). On any other queue an `initialize` value is a re-prediction and is never recorded.
2. **Human correction** — `annotation_content.updated` on any attached queue. Both conditions must
   hold: the field's datapoint is in `updated_datapoints`, **and** `"human"` is in its
   `validation_sources`. `"human"` alone only means a person visited the field. A write by a hook
   (Restore included) or an engine re-prediction never carries `"human"`, so neither loops back.

**Limit:** `"human"` stays in `validation_sources` once a person has visited the field. If another
hook later rewrites the type on the same annotation, that write also passes both checks and is
recorded as a correction. Record and Restore ran together in production without this happening.
Check any other hook that writes the type field before you add this part.

The public API describes `updated_datapoints` as datapoint (content-node) ids. The hook also accepts
the schema id, so the check holds under either reading.

Every write reads the annotation fresh, then PATCHes the whole metadata object. A metadata PATCH
replaces the object, and other hooks keep their own keys in it.

## Hook object requirements

| Setting | Value | Why |
|---|---|---|
| `events` | `["annotation_content.initialize", "annotation_content.updated"]` | the two paths |
| `queues` | every intake queue **and** every destination queue | a person can correct the type on any of them |
| `settings` | `{"intake_queue_ids": [<int>, ...]}` | which queues decide the type |
| `token_owner` | a user id | supplies `rossum_authorization_token` for the metadata GET/PATCH |
| `config.third_party_library_pack` | `"default"` | provides `rossum_api` |
| `sideload` | not needed | the hook reads the raw content tree, not TxScript |

**Per-environment ids stay in settings, as list values.** `rdc migrate` and `prd2 deploy` remap ids
in settings *values*, never in *keys*. Measured trap: a hook whose settings used queue ids as dict
keys kept the source environment's ids after a migrate.

The hook uses the `rossum_api` client, proven in production. Most other parts use `urllib`; that
difference is deliberate, not a defect to rewrite.

## Cost

An `annotation_content.updated` hook blocks the validation screen while it runs, and it fires on
**every** field edit, not only this field. Measured: about **0.2 s warm, 4.3–5.0 s on a cold start**
(the first run after several idle minutes). The alternative is to capture the correction on
`annotation_content.confirm`: once per confirm, still blocking. This part keeps `updated`.

## What was tested

Production: the intake path and the human-correction path ran in one production deployment
(test 2026-11-12/13, production 2026-11-16). The generalized fragment, with the default seams
filled, passed 6 cases on the real `txscript` 1.2.0 runtime with a stubbed `rossum_api` client:
intake record with a fresh read that keeps other metadata keys, no record on a destination
`initialize`, a human correction (by datapoint id and by schema id), a non-human change ignored, and
a human edit of another field ignored. The repo has no harness for running part fragments, so the
cases are not committed.
