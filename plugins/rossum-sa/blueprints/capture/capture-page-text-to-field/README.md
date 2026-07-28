# capture-page-text-to-field

Park the **first and last page's OCR text** into two schema fields on
`annotation_content.initialize`, so that something which cannot read the document — a reasoning
field, an LLM prompt, a rule — has the content available as ordinary field values.

## Why this exists

A reasoning field's `context` accepts **field references only** (`field.<id>`, `self.attr.label`,
`self.attr.description`). There is **no whole-document or page-text context.** So document content
has to be parked in a schema field first, and this is the parking step:

```
this hook  ──writes──>  «first_page_field» / «last_page_field»  ──named in──>  reasoning field context
```

## Parameters

| param | default | meaning |
|---|---|---|
| `«first_page_field»` | required | schema id for the first page's text |
| `«last_page_field»` | required | schema id for the last page's text |
| `«max_chars»` | `20000` | per-field truncation cap |

## Schema fields to create

Both are plain `string` datapoints carrying `"ui_configuration": {"type": "data"}` — the correct
type for a hook-populated field (`"captured"` produces warnings). Give them **operator-meaningful
labels**: context entries are presented to the model labelled by the field's `label`, so
`Page text — first page` earns its place and `hook_output_1` does not.

## Hook object requirements

| Setting | Value | Why |
|---|---|---|
| `events` | `["annotation_content.initialize"]` | text is available once import completes |
| `sideload` | `["schemas"]` | **required** — `TxScript.from_payload()` raises `PayloadError: Schema sideloading must be enabled!` without it, on every run |
| `token_owner` | a user id | supplies `payload["rossum_authorization_token"]` |

## Gotchas this encodes

- **`page_numbers` is always sent explicitly.** Omitting it silently returns only the first 20
  pages, so "the last page" of a 21+ page document would be page 20. Nothing errors.
- **Results are matched by `page_number`, never by position.** The API returns pages ascending
  regardless of the order you asked for, and collapses duplicates.
- **`granularity=texts`** gives one item per page holding the entire page text — no line joining,
  no reading-order assumptions. Every other granularity returns fragments with `position`.
- **1-page documents** are handled: `sorted({1, total})` asks once, and both fields get the same text.
- **It fails open.** A lookup failure logs and writes empty strings. An enrichment hook must never
  turn "couldn't add the extra data" into "this document can't be processed".

## Iterating on it

To re-run this hook against a document already imported, use the in-place re-extract
(`PATCH /v1/annotations/{id} {"status": "importing", "rir_poll_id": null, "messages": []}`, or
`rossum_refire_annotation mode="reextract"`) — it fires `initialize` without minting a new
annotation. Re-extraction **replaces** extracted content, so never point it at a document a human
has been working on.

`print()` output from this hook is retrievable: it is in the **`output`** field of a
`GET /hooks/logs` row (`message` is empty for successful runs).

## Composes with

A reasoning field naming both fields in its `context`. Note reasoning fields are **feature-gated
and separately billed** — confirm the organization has them enabled before designing around this.
Prefer the two narrow fields over concatenating into one blob with hand-rolled section markers.
