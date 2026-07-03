# export-workday-po-line-type-projection

Use this block as the `Invoice_Line_Replacement_Data` of a Workday `Submit_Supplier_Invoice` mapping (see `export-workday-soap-invoice-mapping`) when invoices are matched to purchase orders. Workday's PO data model splits lines into **goods lines** (consumed by quantity — `Quantity` × `Unit_Cost`) and **service lines** (consumed by amount — `Extended_Amount`). Sending the wrong projection mis-books consumption, so each line switches on the order type projected onto it by the PO-line match.

`$DATAPOINT_MAPPING$` emits its key only when the line's `item_order_type_wd` equals a mapped value — a goods line gets `Quantity`/`Unit_Cost` and no `Extended_Amount`, a service line the reverse. `Purchase_Order_Line_Reference` anchors each line to the PO line by `Line_Number` with the PO `Document_Number` as parent.

## Params

- `goods_order_type_id` — the tenant's order-type reference id for goods lines (a tenant WID, shaped like `ORDER_TYPE-…`)
- `service_order_type_id` — the tenant's order-type reference id for service lines

## Produces / Consumes

- Produces: nothing.
- Consumes per line: `item_order_type_wd`, `item_order_line_nr_wd`, `item_document_number_po_wd` (all projected by `mdh-workday-po-line-type-match`), plus captured `item_quantity`, `item_amount_base` (unit price), `item_total_base`, `item_description`.

## Adapt

- **Line grouping:** Workday accepts one invoice line per PO line here; when several invoice lines match the same PO line, aggregate them (sum totals, concatenate descriptions) into a shadow multivalue and point `$FOR_EACH_SCHEMA_ID$` at it.
- **Flat alternative:** if the tenant does not require quantity-level matching, every line can be projected as `Quantity` ±1 × `Unit_Cost` = abs(line total), the sign encoding credit notes — one source runs this in production.
- Add `$IF_SCHEMA_ID$`-guarded `Tax_Applicability_Reference` / `Withholding_Tax_Code_Reference` / worktag entries per line as tenant coding requires.

See `export-workday-soap-invoice-mapping` for the mapping DSL and hook wiring, `mdh-workday-po-line-type-match` for where the order type comes from, and `workday-live-po-line-status` for validating against live PO consumption before export.
