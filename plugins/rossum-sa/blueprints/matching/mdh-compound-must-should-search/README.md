# mdh-compound-must-should-search

Use this blueprint when a single Atlas Search query needs to combine a required boosted field (the `must` clause) with an optional ranking field (the `should` clause) and a hard non-scoring constraint (the `filter` clause). A typical use case is PO matching: the supplier name must be a fuzzy hit, a matching order reference lifts the score, and only open records are considered. The compound query runs as a single `$search` stage, making it efficient for collections that already have an Atlas Search index covering the relevant fields.

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

The `$project` field names (`po_internal_id`, `order_id_normalized`, `supplier_id`) are example-specific literals carried over from the PO source. Replace them with the actual key and display fields of your collection — they are not parameterized and will return nothing if they do not exist in your dataset.

**Atlas Search index is a prerequisite.** The `$search` runs against an index named `po_search_idx` (rename it in the fragment to match your collection's index). If the index does not exist the query *errors* — create it before shipping; see the Atlas Search pre-flight in `mdh-reference`.

**`filter: equals` only matches `token`-mapped fields.** The `filter` clause uses `equals`, which works only on fields indexed as `token` (booleans, numbers, dates, objectIds, or strings *explicitly* mapped as `token`). On a **dynamic** index, `equals` on a string field returns **nothing** — verified live: the `must`+`should` ranking worked, but adding an `equals` filter on a string field silently emptied the result. Map the filter field as `token` in the index, or use a `phrase`/`text` clause inside `filter` instead of `equals`.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
