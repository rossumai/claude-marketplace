# mdh-exact-to-fuzzy-cascade

Use this blueprint when you need to match an entity (typically a vendor or supplier) by a strong exact identifier first and fall back to fuzzy name search only when the exact key is absent or yields no result. The cascade runs two queries in order: an exact `$match` on the ID field (e.g. VAT number, tax ID) and, if that returns nothing, an Atlas Search phrase query on the name field gated by a raw score threshold. This pattern maximises precision on the first query while still recovering partial data via the fuzzy fallback.

## Params

- `dataset` — the MDH collection name to match against (e.g. `vendors_master_list`)
- `id_field` — the document field used for exact matching (e.g. `vatin`, `tax_id`, `supplier_code`)
- `name_field` — the document field used for fuzzy phrase matching (e.g. `name`, `supplier_name`)
- `threshold` — raw Atlas Search score floor for the fuzzy query; default `0.8`. Raise it to tighten recall, lower it cautiously to recover more results.

## Produces / Consumes

- Produces: `matched_id`, `matched_name` — the winner record's key and display name, written to the configured `mapping.target_schema_id` and an `additional_mapping`.
- Consumes: the field referenced by `«id_field»` (for the exact query) and the field referenced by `«name_field»` (for the fuzzy query), both injected as MDH placeholders at runtime.

## Adapt

**Prerequisite — Atlas Search index.** The fuzzy query runs against an Atlas Search index named `vendor_name_idx` mapping your name field. If that index does not exist the fuzzy stage *errors* — it does not fall back silently. Create it (or rename it in the fragment to match an existing index) before shipping; see the Atlas Search pre-flight in `mdh-reference`.

**Record-status filter is not assumed.** This generic fragment does **not** filter by record status. If your collection has an active/inactive flag, add it (e.g. `"<status_field>": "active"`) to the exact `$match` *and* as an `equals` clause under the `$search` `compound.filter` — otherwise inactive records can match. Do not hardcode a `status: "active"` filter blindly: against a collection that has no such field the exact query returns nothing, silently (verified against a real collection).

**Threshold is collection-specific.** `threshold` gates a raw `searchScore`, which depends heavily on the index, analyzer, and field length — there is no universal range. Calibrate it against your own collection via the `mdh-reference` pre-flight. For scale: a clean name-phrase match on a short name field can score ~2–3, so a floor of 5 would reject perfect matches; the default `0.8` is a deliberately permissive starting floor.

**Cascade order is fixed.** The exact query must come first — if it returns results, MDH stops and never runs the fuzzy fallback. Insert any extra stage (e.g. IBAN) *between* the exact and fuzzy queries, not after.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
