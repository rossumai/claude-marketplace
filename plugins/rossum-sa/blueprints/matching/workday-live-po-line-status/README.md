# workday-live-po-line-status

Use this hook when validation must compare an invoice against the purchase order's **current** consumption — how much has already been received and invoiced per line — rather than the values from the last master-data sync. The SOAP `Get_Purchase_Orders` export (the usual MDH sync source) does not carry per-line received/invoiced consumption, so this hook calls the Workday **procurement REST API** live each time the annotation is opened or edited, and writes the consumption onto each matched line. GRN / over-invoicing business rules then read those fields. (A full-SOAP status path exists as a separate extension and is out of this blueprint's scope.)

Goods vs service duality applies here too: Workday returns `goodsLines` (quantity-consumed) and `serviceLines` (amount-consumed) as separate arrays, so the hook writes `*_quantity_received/invoiced` for goods lines and `*_amount_received/invoiced` for service lines, keyed by the order type the PO-line match projected onto the row.

## Hook wiring

Function hook (python3.12), events `annotation_content.initialize` / `started` / `updated`, `run_after` the PO-matching hook, `sideload: ["schemas"]`, `token_owner` set (the hook PATCHes its own secrets to cache the bearer token). Secrets: `client_id`, `client_secret`, `refresh_token` — `bearer_token` is written back by the hook and reused until a 401 triggers re-login. The org must allow external HTTPS egress, or every call times out at the TCP layer.

## Params

- `token_url` — `https://<host>/ccx/oauth2/<tenant>/token`
- `po_endpoint_url` — `https://<host>/ccx/api/procurement/v5/<tenant>/purchaseOrders`
- `goods_order_type_id` / `service_order_type_id` — must equal the values `mdh-workday-po-line-type-match` stamps into `item_order_type_wd`

## Produces / Consumes

- Produces per line: `item_order_line_quantity_received`, `item_order_line_quantity_invoiced` (goods), `item_order_line_amount_received`, `item_order_line_amount_invoiced` (services) — all hidden `data` fields for rules to read.
- Consumes per line: `item_order_line_nr_wd`, `item_order_type_wd`, and `item_order_internal_id_wd` — the PO's **Workday internal id (WID)**, which is what the REST path takes (not the PO document number); project it onto lines during PO header matching.

## Adapt

- The line lookup parses the line number from the **trailing token of the REST line `descriptor`** — no bare line-number field was observed on line objects. Verify the descriptor format on the target tenant/API version before trusting it.
- Numeric fields arrive either bare or wrapped as `{"value": …}` depending on endpoint version; `_num()` handles both.
- The per-run `po_cache` fetches each PO once even when many lines share it; move settings (`TOKEN_URL`, order types) into hook `settings` if you prefer config over constants.
- On lookup failure the hook shows an error and stops; downgrade to a warning if live status should be advisory rather than blocking.

See `txscript-reference` for the hook API and `mdh-workday-po-line-type-match` for the fields this consumes.
