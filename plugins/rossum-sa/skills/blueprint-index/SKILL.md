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

Export blueprints are mechanism-level: a target system (Coupa, SAP, Workday) is a
*composition* of these plus filled params — never a single per-ERP blueprint.

## Candidates (`candidate` maturity — NOT for auto-composition)

These are believed-good but **unproven** — offer them to a human for review, never wire them in automatically. Promote to `standard` only after live validation.

| blueprint | axis | summary |
|--------|------|---------|
| `mdh-fanout-matches-to-selectable-table` | matching | fan all MDH match options into a multivalue table with a per-row select flag (hash-guarded); maintain a filtered export list — the multi-select counterpart to `mdh-picker-with-exact-preselect`. Composes with `export-iterate-line-items`. |
