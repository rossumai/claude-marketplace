---
name: blueprint-index
description: Index of the rossum-sa blueprint library — vetted, parameterized, composable building blocks (matching, export) lifted from the reference packs. Consult when assembling a Rossum implementation to reuse a known-good pattern instead of writing one from scratch; tells you which blueprints exist, what they produce/consume, and where they live.
user-invocable: false
---

# Blueprint Index

The blueprint library lives at `plugins/rossum-sa/blueprints/<axis>/<name>/` — each blueprint
is a `blueprint.json` contract + a `fragment.*` config body (with `«param»` seams) + a
`README.md`. Blueprints are *drop-ins* built on top of the reference packs. Only
`standard`-maturity blueprints are safe to compose automatically.

To use a blueprint: read its `blueprint.json` for `params`/`produces`/`consumes`, copy the
fragment, fill the `«param»` seams, and confirm its `consumes` fields exist in the
schema. See `blueprints/README.md` for the full contract.

## Matching (`blueprints/matching/`)

| blueprint | summary | produces |
|--------|---------|----------|
| `mdh-exact-to-fuzzy-cascade` | exact-ID → fuzzy-name, score-gated | matched_id, matched_name |
| `mdh-compound-must-should-search` | boosted must + optional should + filter Atlas Search | matched_id |
| `mdh-fuzzy-score-normalization` | fuzzy name search with length-ratio normalization | matched_id, match_score |
| `mdh-picker-with-exact-preselect` | auto-select exact; else placeholder + pick-list | selected_key, selected_label |
| `mdh-lookup-join-then-match` | match parent, $lookup to sub-collection, match child | matched_child_id |

## Export (`blueprints/export/`)

| blueprint | summary |
|--------|---------|
| `export-oauth-token-cache` | OAuth client_credentials token, cached, auto-refresh on 401 |
| `export-create-upload-submit` | create → upload → submit cascade with guards |
| `export-sftp-via-file-storage` | SFTP export via the file-storage-export service |
| `export-evaluate-guard` | pre-call guard on required fields + prior-stage success |
| `export-iterate-line-items` | per-line-item POST with sequence index |

Export blueprints are mechanism-level. A target system (Coupa, SAP, Workday) is a
composition of blueprints plus filled params — never one monolithic per-ERP blueprint.
A blueprint may be ERP-specific when the mechanism itself is (e.g. a connector's mapping
dialect) — but it must stay a composable part (if seams alone could make it ERP-neutral,
generalize instead), carry the ERP token in its name, and link its grammar down to a
reference pack once one exists.

## Candidates (`candidate` maturity — NOT for auto-composition)

These are believed-good but **unproven** — offer them to a human for review, never wire them in automatically. Promote to `standard` only after live validation.

| blueprint | axis | summary |
|--------|------|---------|
| `mdh-fanout-matches-to-selectable-table` | matching | fan all MDH match options into a multivalue table with a per-row select flag (hash-guarded); maintain a filtered export list — the multi-select counterpart to `mdh-picker-with-exact-preselect`. Composes with `export-iterate-line-items`. |
| `export-workday-soap-invoice-mapping` | export | Workday `Submit_Supplier_Invoice` SOAP-connector mapping skeleton — reference-ID header projections, `$IF_SCHEMA_ID$`-guarded optional refs, amount-coded lines + worktags. Composition root for the other Workday candidates. |
| `export-workday-po-line-type-projection` | export | per-line goods (`Quantity`×`Unit_Cost`) vs service (`Extended_Amount`) projection switched on the matched PO line's order type; swaps into the mapping skeleton's line block. |
| `export-workday-soap-attachment-data` | export | `Attachment_Data` block iterating a related-documents table and base64-embedding each file; attachments **>50 MB fail through Rossum infra**. |
| `export-related-document-attachment` | export | Request Processor stage uploading every regex-matched document relation via multipart — the REST counterpart of the SOAP attachment block. |
| `mdh-workday-po-line-type-match` | matching | invoice-line → Workday PO-line match by PO number + supplier + exact `$toDecimal` amount; unions goods+service line arrays tagging each candidate with its order type; whole-PO pick-list fallback. |
| `workday-live-po-line-status` | matching | on-open hook refreshing live received/invoiced consumption per matched PO line from the Workday procurement REST API (the SOAP sync can't carry it); feeds GRN / over-invoicing rules. |
