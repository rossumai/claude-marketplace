# ap-invoice-to-coupa

AP invoice into a procurement platform over synchronous HTTPS, with master data imported from the
target and validation gating automation.

**Status: `supported`** — the shape the corpus actually ships. Not a deprecation: see
`../README.md`. The export packaging inside it has a preferred form, but the recipe does not fork on
packaging, because a per-call chain and a single request-processor hook are the same integration
shape wearing different clothes.

**Provenance: mined from 7 deduplicated implementations + 1 hand-authored baseline.** Sibling shapes
in the same corpus: 2 do both HTTPS and file, 1 is file-only.

## What it covers

9 phases, 54 enumerated intents. The phases are the delivery chunks; each carries `decide[]`
(what an SA resolves per customer) and `verify` (how the phase is proven).

The layer most people underestimate is **master data**: import hooks outnumber export hooks in 20 of
24 trees in the corpus. A recipe that describes only the export leg describes the minority of the
work, which is why `1_master_data` sits ahead of matching with its own verify.

## Read `platform_contract` first

25 facts, each of which cost a failed API call. The five that bite hardest:

- A multi-line rule `trigger_condition` **must be parenthesised** — Python cannot continue an
  expression across lines, and the error does not name the failing rule.
- **Every field an MDH config writes must be `type: enum`**, `additional_mappings` included. One bad
  target fails the whole config, which then writes nothing at all.
- **Formula fields cap at 2000 characters** — which is *why* the export payload belongs in a
  template hook, not in formulas.
- The export pipeline's `.@` auto-fetch returns a **parsed dict** for a JSON document, so the request
  needs `content_type: "json"`; omitting it form-encodes the body under a JSON header and the target
  answers 500.
- An extraction engine created without **`settings.use_case`** is header-only: line-item columns are
  created, look correct, and silently extract nothing.

## Known gaps

- `5_coding` assumes PO-backed or segment-coded invoices. Non-PO coding from operator memory exists
  in the corpus but has no part yet.
- Function-code intents are not extracted. One implementation in the corpus keeps ~97% of its logic
  in Python; this recipe would describe almost none of it.
- No automation-rate claim. Outcome data was never joined, so the recipe carries structure only.
