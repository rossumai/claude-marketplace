# route-on-confirm-by-document-type

A native rule on the **intake queue** (the inbox) of a classic intake: when a document is confirmed
as one of the listed document types, `change_queue` moves it to the destination queue for those
types and re-imports it there. Create **one rule per destination**.

```
inbox ──confirm (automated or human)──> rule: type in [...] ──change_queue, reimport: true──> destination
```

This part is one of three. Take all three, or the type decided on the inbox is lost on arrival:

| part | role |
|---|---|
| `routing/route-on-confirm-by-document-type` | moves the document (this part) |
| `routing/document-type-record-in-metadata` | records the inbox decision in `annotation.metadata` |
| `routing/document-type-restore-from-metadata` | writes it back into the field on the destination |

The spine recipe's `2_routing` phase holds the decision this part answers (Option B: type decided
on the inbox, then routed) and its alternative (Option A: a constant formula on each target queue).

## Parameters

| param | default | meaning |
|---|---|---|
| `«rule_name»` | required | rule name, max 255 characters |
| `«inbox_queue_url»` | required | URL of the intake queue the rule is attached to |
| `«document_type_field»` | `document_type` | schema id of the document-type enum; same value in all three parts |
| `«document_types»` | required | the types this destination takes: `'tax_invoice', 'credit_note'` |
| `«destination_queue_url»` | required | URL of the destination queue |

`«document_types»` is spliced into the `trigger_condition` string, so use single-quoted Python
literals separated by commas. Each value must be an option of the enum.

**Every enum option needs exactly one rule.** A type that no rule lists is confirmed and then stays
on the inbox, and nothing reports it. List each option of the type field in one rule's
`«document_types»`, including a catch-all type such as "other" (route it to an other-documents
queue).

## Why `annotation_confirmed`

It fires on the **automated** confirm and on a **human** confirm alike. So one mechanism covers both
"routed automatically" and "stopped for review, then routed". Both paths were verified live.

## Why always `reimport: true`

- `reimport: false` skips the destination's `initialize` chain. Measured: an owner allocator that
  runs only on `initialize` assigned no owner.
- `reimport: false` also carries the source queue's automation blockers onto the destination.
- A second, independent SA reported the same: always re-import into the destination. With a sorting
  extension, the target status must be `importing`.

**Exception:** a return trip whose content must survive (for example, back to the originating queue
after a master-data fix) moves without re-import. This is safe only while the target runs
automation `never`.

**History:** `reimport: true` once skipped the re-import in 6 of 19 moves when the **source** queue
had `use_confirmed_state: false` (the move raced the confirm → export transition). Rossum reported
this fixed in 2026-11. That exact path was not measured again, so keep the inbox on
`use_confirmed_state: true` regardless.

## The intake queue

- **Schema:** `«document_type_field»` as an enum, required, with a `score_threshold`. Add any field
  a kept hook writes on the inbox (for example an owner or priority field).
- **Validation screen:** a queue duplicated from a sorting inbox inherits the split screen. For the
  standard validation screen set `ui_validation_screen_enabled: true`,
  `ui_edit_always_split_document: false`, `ui_on_edit_confirm: "validate_first"`, no
  `ui_edit_values`, and `suggested_edit: "disable"`.
- **Automation:** `automation_level: "confident"` plus the field's `score_threshold` decides
  auto-route versus review. For cutover, switch with automation `never` first (a human confirm
  routes), then enable it.
- **Engine:** a new custom **extractor** engine with one custom enum field for the type, and the
  downstream queues as its training queues. Observed once, on one production org: it predicted with scores of
  0.91–0.99 within minutes of its creation, with no cold start. That is one observation. A splitter engine
  used as the classifier scored whole files "other" at most 0.836 in 800 files (invoices and credit
  notes: 0.95–0.99). The minority class is weak there, so the threshold choice matters.

## E-invoices

Structured formats that arrive by email and are mapped at `upload.created`:

- A mapped type (from `InvoiceTypeCode` 380/381) **passes** `confident` automation.
- The re-import into the destination **kept** the `einvoice` flag, the XML `document_relations`
  record and every mapped value. Measured: 53 of 53 non-empty fields were identical to the same
  invoice moved by an in-place path. Only the day's FX rate and a time-based priority differed.
  Seen for XRechnung in both UBL and CII form.

## Gaps and side facts

- A **plain manual move** (no re-extraction) fires no `initialize`, so the Restore part does not
  run. Accepted: the person who moves the document sets the type.
- A manual split in the UI still creates `edit` relations. An export stage keyed on them is not
  dead code after you remove automatic splitting.
- `POST /queues/{id}/duplicate` copies UI settings, including the split screen.
- The queue-level `training_enabled` PATCH is ignored. An engine learns from its `training_queues`
  list.

## Verify

- Every option of the type enum appears in exactly one rule's `«document_types»`.
- Per type: one real document lands on its destination with the right type, and the
  destination's `initialize` hooks ran.
- One document below the threshold stops on the inbox for review.
- One document corrected on the inbox and then confirmed keeps the corrected type downstream.
- Zero documents arrive on a destination in `exporting` or `failed_export`.
