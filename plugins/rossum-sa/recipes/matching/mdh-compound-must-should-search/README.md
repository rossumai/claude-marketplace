# mdh-compound-must-should-search

Use this recipe when a single Atlas Search query needs to combine a required boosted field (the `must` clause) with an optional ranking field (the `should` clause) and a hard non-scoring constraint (the `filter` clause). A typical use case is PO matching: the supplier name must be a fuzzy hit, a matching order reference lifts the score, and only open records are considered. The compound query runs as a single `$search` stage, making it efficient for collections that already have an Atlas Search index covering the relevant fields.

## Params

- `dataset` — the MDH collection name (e.g. `purchase_orders`)
- `must_field` — the primary field that every result must match; boosted by a score multiplier (e.g. `supplier_name`)
- `should_field` — the secondary field whose match is optional but lifts rank (e.g. `order_reference`)
- `filter_field` — the field used as a hard non-scoring constraint; typically a status or category field (e.g. `status`)
- `threshold` — raw Atlas Search score floor; default `0.8`. The compound score combines `must` and `should` contributions, so calibrate against real data.

## Produces / Consumes

- Produces: `matched_id` — the winner record key written to `mapping.target_schema_id`.
- Consumes: runtime placeholder values injected into the `must_field` query and `should_field` query paths via MDH `{placeholder}` syntax.

## Adapt

The `must` clause uses a fuzzy `text` operator with `maxEdits: 1` and a score boost of 2. The `should` clause uses `phrase` with `slop: 1`. If your `must_field` is a structured reference (e.g. a PO number) rather than free text, switch the `must` operator from `text` (fuzzy) to `phrase` (exact word order) or `equals` (exact value). The filter value (`"open"` in the source example) is a hard-coded literal — replace it with the correct filter value for your collection, or convert it to a placeholder if it varies per document.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
