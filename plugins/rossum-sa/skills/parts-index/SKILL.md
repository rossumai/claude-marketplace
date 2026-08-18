---
name: parts-index
description: Index of the rossum-sa parts library — vetted, parameterized, composable building blocks (capture, matching, export) lifted from the reference packs. Consult when assembling a Rossum implementation to reuse a known-good pattern instead of writing one from scratch; tells you which parts exist, what they produce/consume, and where they live.
user-invocable: false
---

# Parts Index

The parts library lives at `${CLAUDE_PLUGIN_ROOT}/parts/<axis>/<name>/` — each
part is a `part.json` contract + a `fragment.*` config body (with `«param»` seams)
+ a `README.md`. Parts are *drop-ins* built on top of the reference packs. Only
`standard`-maturity parts are safe to compose automatically.

**Always read fragments through `${CLAUDE_PLUGIN_ROOT}`, never a repo-relative path.** You
are normally `cd`'d into a customer's prd2 project, not this marketplace repo, so
`plugins/rossum-sa/parts/…` resolves to nothing there: you would see the index below,
fail to read any fragment, and improvise from a one-line summary. `${CLAUDE_PLUGIN_ROOT}`
points at the installed plugin wherever it lives.

To use a part: read its `part.json` for `params`/`produces`/`consumes`, copy the
fragment, fill the `«param»` seams, and confirm its `consumes` fields exist in the
schema. See `${CLAUDE_PLUGIN_ROOT}/parts/README.md` for the full contract — including
what `produces`/`consumes` mean (post-fill schema field ids) and the `type` a param declares.

## Capture (`parts/capture/`)

| part | summary | produces |
|--------|---------|----------|
| `capture-page-text-to-field` | park first/last page OCR text into schema fields on `initialize`, so a reasoning field or LLM prompt has document content to read | first_page_field, last_page_field |

## Matching (`parts/matching/`)

| part | summary | produces |
|--------|---------|----------|
| `mdh-exact-to-fuzzy-cascade` | exact-ID → fuzzy-name, score-gated | matched_id, matched_name |
| `mdh-compound-must-should-search` | boosted must + optional should + filter Atlas Search | matched_id |
| `mdh-fuzzy-score-normalization` | fuzzy name search with length-ratio normalization | matched_id, match_score |
| `mdh-picker-with-exact-preselect` | auto-select exact; else placeholder + pick-list | selected_key, selected_label |
| `mdh-lookup-join-then-match` | match parent, $lookup to sub-collection, match child | matched_child_id |

## Export (`parts/export/`)

| part | summary |
|--------|---------|
| `export-oauth-token-cache` | OAuth client_credentials token, cached, auto-refresh on 401 |
| `export-create-upload-submit` | create → upload → submit cascade with guards |
| `export-sftp-via-file-storage` | SFTP export via the file-storage-export service |
| `export-evaluate-guard` | pre-call guard on required fields + prior-stage success |
| `export-iterate-line-items` | per-line-item POST with sequence index |

Export parts are mechanism-level. A target system (Coupa, SAP, Workday) is a
composition of parts plus filled params — never one monolithic per-ERP part.
A part may be ERP-specific when the mechanism itself is (e.g. a connector's mapping
dialect) — but it must stay a composable part (if seams alone could make it ERP-neutral,
generalize instead), carry the ERP token in its name, and link its grammar down to a
reference pack once one exists.

## Candidates (`candidate` maturity — NOT for auto-composition)

None in the library today. When a `candidate` lands it belongs in this section, and only
here: it is believed-good but **unproven**, so offer it to a human for review and never wire
it in automatically. Promotion to `standard` requires live validation.

Seven candidates (one MDH fan-out picker + six Workday-specific export/matching parts)
are staged on the `feat/parts-library-candidates` branch, held back until each has been
run against a live target rather than only structurally validated.
