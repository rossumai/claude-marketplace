# mdh-fanout-matches-to-selectable-table

> **Maturity: `candidate` (unproven).** Authored from the handbook *Multiple data matching results* pattern + the Winning Group `2026-06-30-callup-multi-order-update` design. It has **not** been live-validated yet. Verify against `txscript-reference` and a soft re-fire before promoting to `standard`.

Use this blueprint when **one** captured reference legitimately matches **several** master-data records and the operator must act on a **subset** of them (not just pick one). MDH collapses multiple matches into a single enum selection; this serverless hook works around that by fanning every match option into a multivalue table, one row per match, each with a yes/no select flag. The operator ticks the subset to act on, and the hook maintains a hidden, pre-filtered list of the selected keys ready to hand to an export `iterate_over`.

It is the multi-select counterpart to `mdh-picker-with-exact-preselect` (which is single-select).

## Params

- `source_match_field` — the MDH match enum field that already holds **all** matched options (value + label).
- `hash_source_field` — the captured field whose value gates recompute. While it is unchanged, the table is **not** re-fanned, so operator selections survive recomputes. Extend the hash string if your match keys on several fields.
- `hash_field` — hidden field storing the recalculate hash.
- `target_table` — the multivalue table to populate (one row per match).
- `row_key_field` — hidden per-row column set to `option.value` — the key the export/PATCH targets.
- `row_reference_field` — operator-facing display column set to `option.label`, so candidates are distinguishable.
- `row_select_field` — per-row `yes`/`no` enum the operator ticks. Defaults to `no` for ambiguous (>1) matches and `yes` for a lone match (so single-match documents stay touchless).
- `export_list_field` — hidden list of `row_key_field` values where the select flag is `yes`.

## Produces / Consumes

- Produces: `target_table` (rows) and `export_list_field` (the filtered key list).
- Consumes: `source_match_field` (the match options) and `hash_source_field` (the recompute gate).

## Adapt

**Compose, don't reinvent the export.** Point `export-iterate-line-items` (or your export stage's `iterate_over`) at `export_list_field`; the body is the same per kept record, only the key varies. `iterate_over` has **no inline filter** — that is exactly why this hook maintains the already-filtered `export_list_field` rather than letting the export filter rows itself.

**Keep all, flag the subset — never prune.** This deliberately keeps every matched row and selects via a flag, rather than deleting non-target rows. Selections stay auditable and the choice is non-destructive; a recompute never silently drops a candidate. The hash guard is what protects the operator's ticks from being reset.

**Block ambiguity separately.** With `row_select_field` defaulting to `no`, a multi-match document already exports nothing until the operator acts — but pair this with a business rule that fires `add_automation_blocker` when the match count is >1, to force manual review.

**Verify before trusting (candidate).** Confirm the TxScript option attributes (`.value` / `.label`), the `.attr.options` accessor, and the multivalue write shape (`x.field.<table> = [ {col: val}, … ]`) against `txscript-reference`, then soft re-fire against a real multi-match document and assert the right rows fan out, selections persist across a recompute, and the export list reflects the ticks.

See `txscript-reference` (serverless hook API) and `mdh-reference` (match results / enum options) for the underlying grammar; the handbook *Multiple data matching results* page is the origin pattern.
