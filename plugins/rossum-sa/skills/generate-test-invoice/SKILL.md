---
name: generate-test-invoice
description: Build a test invoice PDF that hits one exact case — a vendor name variant, a missing or wrong VAT ID, a PO line with a different quantity, a deliberately wrong total — using real Master Data Hub records, then — only after the operator approves — upload it to a sandbox queue so a fix or feature can be shown working end to end. A stdlib-only script renders the PDF and an expected-values file to assert against. Use when no real document covers the case being delivered, when the user asks to "build a test invoice", "make a fake invoice", "generate a test document", "show that the fix works", "demo this on a document", or when iterate needs a document for an edge case.
argument-hint: "[the case to test, e.g. 'vendor VAT ID differs from master data'] [--queue=<sandbox queue id>]"
allowed-tools: Read, Write, Bash, Grep, Glob
---

# Generate a Test Invoice for One Case

> Case and target: $ARGUMENTS

A real document rarely contains exactly the case you just fixed. This skill makes one: a PDF built
from the customer's own master data, changed in only the way the case needs, uploaded to a sandbox
queue. The result is then checked with `iterate`, which owns the re-fire loop and the PASS/FAIL table.

## Safety

- **Generate only with the operator's approval — never on your own initiative.** Before rendering
  anything, show the operator the case, the master-data records you will use, and the values you
  will change, and wait for an explicit yes. This holds in autonomous (`/goal`) runs too: there you
  stop and ask, you do not generate. A second, separate yes is needed for the upload (below).
- **Sandbox/UAT queues only.** Never upload a test document to a production queue: it would be
  processed, maybe exported, as if it were real. If you cannot tell which environment a queue is
  in, ask.
- **The upload is a write** — show the queue and file, and wait for the user's yes before calling
  `rossum_upload_document`.
- **Master-data values are customer data.** Keep specs and PDFs in the customer project (e.g.
  `.test-runs/`, gitignored), never in this plugin's public repo or any shared place.

## Workflow

Copy this checklist and tick it off:

```
- [ ] 1. State the case and the fields it lives in
- [ ] 2. Read the real records from Master Data Hub
- [ ] 3. Write the spec — real values, changed only where the case needs it
- [ ] 4. Show the operator the case, records and changed values — wait for their yes
- [ ] 5. Render the PDF and check it
- [ ] 6. Upload to the sandbox queue (second yes first)
- [ ] 7. Hand over to iterate with assertions from expected.json
- [ ] 8. Offer to delete the test annotation
```

**1. State the case.** One sentence, plus the fields that carry it: "supplier VAT ID on the
document differs from the vendor record → the vendor must still match on name + IBAN". Name the
expected outcome too — it becomes the assertion list in step 6.

**2. Read real records.** Find the dataset the matching config queries (`source.dataset` of
that config in the MDH hook's settings — see `mdh-reference`), then read a record with `data_storage_find`
(read-only): `collectionName` = the dataset, a `query` on the key you need, `limit` 1–3. Take the
values the matching uses (name, VAT ID, IBAN, address, PO number, PO lines). A case that needs a
PO needs a PO that exists — and, for a line-level match, its lines.

**3. Write the spec.** JSON, format in [spec-format.md](spec-format.md); start from
[example-spec.json](example-spec.json). Rules:
- Use the real values everywhere the case does not touch, so matching behaves as on a real document.
- Change only what the case needs, and only on the document — never edit master data for a test.
- Invoice number `TEST-<date>-<n>`, new for every upload, so Duplicate Handling does not flag a
  re-run and the document is recognisable.
- Use labels the extraction engine knows ("Invoice number", "Issue date", "Due date",
  "PO number", "VAT ID") — the PDF is read like any other document.
- Do not print the case description on the document; text on the page can end up extracted.
- For a wrong-total case, use `totals_override` (the printed value changes, the computed one stays
  in `expected.json`).

**4. Get the operator's approval.** In one message: the case, the dataset and record(s) used, each
value you changed (`VAT ID: DE111… → DE999…`), and the target sandbox queue. Render only after an
explicit yes; on anything else, stop.

**5. Render.**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/generate-test-invoice/make_test_invoice.py <spec.json> <out.pdf>
```

(`${CLAUDE_PLUGIN_ROOT}` is this plugin's install folder; from this skill's own folder the script
is `make_test_invoice.py`.) Exit 2 means the spec is wrong — the message names the key. Open the
PDF with Read and check that the case is visible as intended.

**6. Upload** (after a second, separate yes): `rossum_upload_document(file_path=<absolute path>,
queue_id=<sandbox queue>)`, then poll the returned task with `rossum_get_task` until it carries
the annotation, and wait for the annotation to leave `importing`.

**7. Verify with `iterate`.** Hand over the annotation ID and an assertion list built from
`expected.json` plus the expected outcome from step 1. If a field the case depends on was not
extracted, that is an extraction gap, not a result about the fix — set the field with `iterate`'s
direct edit + re-fire to test the fix in isolation, and report the extraction gap separately.

**8. Clean up.** Offer to delete the test annotation (`rossum_delete_annotation`, a write — ask).
Keep the spec in the project's `.test-runs/` so the case can be re-run after the next change.

## Gotchas

- **Characters outside Western European (cp1252) print as `?`.** Czech `ř`, Polish `ł`, Cyrillic
  etc. are not covered by the built-in PDF fonts. For a name-matching case that depends on such a
  character, say so and use a real document instead.
- **The PDF has a plain layout.** Extraction reads it well, but it is not the customer's real
  layout; a case that depends on layout (a table the engine misreads, a stamp) needs a real document.
- **One case per document.** Two changes at once make a failure impossible to attribute.
