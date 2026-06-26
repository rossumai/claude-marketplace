---
name: recipe-index
description: Index of the rossum-sa recipe library — vetted, parameterized, composable building blocks (matching, export) lifted from the reference packs. Consult when assembling a Rossum implementation to reuse a known-good pattern instead of writing one from scratch; tells you which recipes exist, what they produce/consume, and where they live.
user-invocable: false
---

# Recipe Index

The recipe library lives at `plugins/rossum-sa/recipes/<axis>/<name>/` — each recipe
is a `recipe.json` contract + a `fragment.*` config body (with `«param»` seams) + a
`README.md`. Recipes are *drop-ins* built on top of the reference packs. Only
`standard`-maturity recipes are safe to compose automatically.

To use a recipe: read its `recipe.json` for `params`/`produces`/`consumes`, copy the
fragment, fill the `«param»` seams, and confirm its `consumes` fields exist in the
schema. See `recipes/README.md` for the full contract.

## Matching (`recipes/matching/`)

| recipe | summary | produces |
|--------|---------|----------|
| `mdh-exact-to-fuzzy-cascade` | exact-ID → IBAN → fuzzy-name, score-gated, all-records fallback | matched_id, matched_name |
| `mdh-compound-must-should-search` | boosted must + optional should + filter Atlas Search | matched_id |
| `mdh-fuzzy-score-normalization` | fuzzy name search with length-ratio normalization | matched_id, match_score |
| `mdh-picker-with-exact-preselect` | auto-select exact; else placeholder + pick-list | selected_key, selected_label |
| `mdh-lookup-join-then-match` | match parent, $lookup to sub-collection, match child | matched_child_id |

## Export (`recipes/export/`)

| recipe | summary |
|--------|---------|
| `export-oauth-token-cache` | OAuth client_credentials token, cached, auto-refresh on 401 |
| `export-create-upload-submit` | create → upload → submit cascade with guards |
| `export-sftp-via-file-storage` | SFTP export via the file-storage-export service |
| `export-evaluate-guard` | pre-call guard on required fields + prior-stage success |
| `export-iterate-line-items` | per-line-item POST with sequence index |

Export recipes are mechanism-level: a target system (Coupa, SAP, Workday) is a
*composition* of these plus filled params — never a single per-ERP recipe.
