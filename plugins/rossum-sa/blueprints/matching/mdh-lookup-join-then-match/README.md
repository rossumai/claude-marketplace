# mdh-lookup-join-then-match

Use this blueprint when the record you need to match lives in a sub-collection that must be joined to a parent entity before the child match can be evaluated. A typical use case is delivery address resolution: first locate the supplier by exact ID in the parent collection, then `$lookup`-join to the delivery addresses sub-collection and match the specific address code within that supplier's locations. This two-level hierarchy cannot be collapsed into a single `$match` because the child records exist in a separate collection linked by a foreign key.

## Params

- `parent_dataset` — the parent collection to match first (e.g. `suppliers`)
- `parent_field` — the field on the parent used for the initial exact match (e.g. `supplier_id`)
- `join_collection` — the sub-collection to join via `$lookup` (e.g. `delivery_addresses`)
- `local_key` — the join key on the parent document (e.g. `supplier_id`); used as `$lookup.localField`
- `foreign_key` — the join key on the child documents (e.g. `supplier_id`); used as `$lookup.foreignField`
- `child_field` — the field on the child record to match against (e.g. `address_code`); referenced after `$unwind` as `delivery_locations.«child_field»`

## Produces / Consumes

- Produces: `matched_child_id` — the child record's key, written to `mapping.target_schema_id`. Additional child fields (name, city) are available via `additional_mappings`.
- Consumes: the parent identifier and the child code, injected as MDH placeholders `{supplier_id}` and `{delivery_address_code}` respectively.

## Adapt

The `$unwind` after `$lookup` expands each matched parent into one row per child record, so a parent with many child records will produce many rows before the child `$match` filters them down. For large sub-collections this can be expensive — add a `$limit` on the parent result before the `$lookup`, or push a filter into `$lookup.pipeline` (the `let`/`pipeline` form of `$lookup`) to filter the sub-collection server-side. The `status: "active"` guard on the parent match is example-specific; adapt it to your collection's equivalent active-record flag.

See `mdh-reference` (matching queries) for the underlying query grammar and scoring.
