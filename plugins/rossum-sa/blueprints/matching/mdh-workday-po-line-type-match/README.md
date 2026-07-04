# mdh-workday-po-line-type-match

Use this MDH config to match each invoice line item to a single Workday purchase-order line **and classify it as a goods or a service line in the same pass**. Workday POs carry two separate line arrays (`Goods_Line_Data`, `Service_Line_Data`) with different consumption semantics; downstream export and live-status checks need to know which array the matched line came from. The query stamps each candidate with its order type (`_type`) while unioning the two arrays, so the classification is a by-product of the match — no separate formula needed.

Cascade:

1. **Exact amount** — match the PO by number + supplier, union + tag both line arrays, keep candidates whose `Extended_Amount` equals the invoice line total (`$toDecimal` on both sides).
2. **Whole-PO fallback** — same union without the amount filter: every line of the PO becomes a pick-list option for the operator when no amount matches.

## Params

- `po_dataset` — MDH collection with the synchronized Workday POs
- `goods_order_type_id` / `service_order_type_id` — the tenant's order-type reference ids stamped onto candidates (must equal the values the export/live-status blueprints switch on)

## Produces / Consumes

- Produces per line: `item_order_line_nr_wd` (match target), `item_order_type_wd`, `item_po_quantity`, `item_po_unit_cost`, `item_order_line_amount`, `item_document_number_po_wd`.
- Consumes: `item_order_id` (line-level PO number — normalize into a formula field first if suppliers decorate it), `supplier_wd` (from supplier matching), `item_total_base`.

## Adapt

- **All mapping targets must be enum-type fields** (`ui_configuration.type: "data"`) — a string target fails the entire hook.
- **Dataset shape varies by sync**: some syncs nest the PO under a wrapper array (e.g. `Purchase_Order_Data`); `$unwind` it first and prefix every path. One source also excludes technical lines by spend category (`$not`/`$elemMatch` on `Resource_Category_Reference.ID`) between the match and the union.
- **Duplicate amounts on one PO** produce multiple matches by design; `best_match` preselects and the operator can switch. Add a stricter tie-breaker (description similarity, remaining-quantity) only if duplicates are common.
- The PO **header** match (PO number + status + currency + Workday internal id) is a separate, simpler config that should also project the PO's internal id (WID) onto lines — `workday-live-po-line-status` needs it for the REST lookup.

See `mdh-reference` (Example 5: PO Line Item Matching with Amount Comparison) for the grammar this generalizes, `workday-reference` (§ Goods vs Service Lines, § Import Configuration) for the line model and how the dataset shape is produced, and `export-workday-po-line-type-projection` / `workday-live-po-line-status` for the consumers of `item_order_type_wd`.
