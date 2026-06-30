# export-iterate-line-items

Use this blueprint when an external API expects line items to be sent one at a time rather than as a batch. The Request Processor's `iterate_over` mechanism loops over each entry in a multivalue (table) field and fires a separate POST request per item, passing the item's field values and a zero-based sequence index in the request body.

## Params

- `line_field` — the schema ID of the multivalue (table) field to iterate (e.g. `line_items`, `invoice_lines`); becomes `iterate_over: "field.«line_field»"`
- `item_fields` — the schema ID of the per-line field whose value is sent in each request body (e.g. `sku`, `item_code`); expand the `content` block to include additional per-line fields as needed
- `target_url` — the API endpoint to POST each item to; include `{sequence}` in the URL if the API requires a positional path segment (e.g. `https://api.example.com/orders/lines/{sequence}`) — the engine substitutes the 0-based iteration index at runtime

## Produces / Consumes

- Produces: nothing by default — add `response_handlers` to the stage to capture per-item responses; if multiple handlers target the same schema field, the engine auto-collects the values into a list.
- Consumes: the multivalue field referenced by `«line_field»`; each iteration exposes the current item as `{line_item}` with its child fields accessible as `{line_item.«item_fields».value}`.

## Adapt

To send multiple fields per line item, add additional keys to `request.content`, each referencing `{line_item.<field_id>.value}`. The `iteration_item_name` defaults to `line_item` — change it if you prefer a different variable name in templates. The `{sequence}` engine token is always available regardless of whether `target_url` uses it; you do not declare it as a param.

See `export-pipeline-reference` for the Request Processor stage model.
