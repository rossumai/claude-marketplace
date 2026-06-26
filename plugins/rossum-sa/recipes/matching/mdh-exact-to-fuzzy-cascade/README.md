# mdh-exact-to-fuzzy-cascade

Use this recipe when you need to match an entity (typically a vendor or supplier) by a strong exact identifier first and fall back to fuzzy name search only when the exact key is absent or yields no result. The cascade runs two queries in order: an exact `$match` on the ID field (e.g. VAT number, tax ID) and, if that returns nothing, an Atlas Search phrase query on the name field gated by a raw score threshold. This pattern maximises precision on the first query while still recovering partial data via the fuzzy fallback.

## Params

- `dataset` — the MDH collection name to match against (e.g. `vendors_master_list`)
- `id_field` — the document field used for exact matching (e.g. `vatin`, `tax_id`, `supplier_code`)
- `name_field` — the document field used for fuzzy phrase matching (e.g. `name`, `supplier_name`)
- `threshold` — raw Atlas Search score floor for the fuzzy query; default `0.8`. Raise it to tighten recall, lower it cautiously to recover more results.

## Produces / Consumes

- Produces: `matched_id`, `matched_name` — the winner record's key and display name, written to the configured `mapping.target_schema_id` and an `additional_mapping`.
- Consumes: the field referenced by `«id_field»` (for the exact query) and the field referenced by `«name_field»` (for the fuzzy query), both injected as MDH placeholders at runtime.

## Adapt

Cascade order is fixed and significant: the exact query must come first. If the first query returns results, MDH stops and never runs the fuzzy fallback. If you add a third stage (e.g. IBAN), insert it between the exact and fuzzy queries, not after. The score threshold (`threshold`) in the fuzzy stage is a raw `searchScore`, not a normalised value — typical useful ranges are 5–15 depending on field length; run the Atlas Search pre-flight described in `mdh-reference` to calibrate.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
