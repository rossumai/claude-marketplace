# export-workday-soap-invoice-mapping

Use this blueprint to submit an AP invoice to Workday via the Rossum-hosted Workday SOAP connector. The fragment is the webhook hook's `settings` object: a `wsdl` block naming the tenant, and a declarative `mapping` that the connector renders into a `Resource_Management` / `Submit_Supplier_Invoice` SOAP call. This skeleton carries the header projections plus a **non-PO (amount-coded) line block with worktags**; it is the composition root the other Workday export blueprints plug into.

## Hook wiring

Not a function hook — create a **webhook** on `annotation_content.export` with `config.url` pointing at the org's Workday connector service (`https://<org-domain>/svc/workday/api/v1/export`), `token_owner` set, and the tenant credentials in hook **secrets** (never in settings). The fragment is the hook's `settings`.

## Mapping DSL (as observed; no reference pack documents it yet)

- `@{schema_id}` — value of a schema field; inside `$FOR_EACH_SCHEMA_ID$` it reads the current row's column.
- `{document_content}` — connector-supplied base64 of the annotation's source document (single braces — not a schema field).
- `$IF_SCHEMA_ID$` `{mapping, schema_id, fallback_mapping}` — emit `mapping` only when `schema_id` has a value; `fallback_mapping: {}` drops the key entirely, which is how optional references must be omitted (an empty reference is rejected by the tenant).
- `$FOR_EACH_SCHEMA_ID$` `{mapping, schema_id}` — repeat `mapping` for each row of a multivalue.
- `$DATAPOINT_MAPPING$` `{mapping: {value: projection}, schema_id}` — switch the projection on a field's value (see `export-workday-po-line-type-projection`).
- `$FETCH_DOCUMENT_CONTENT$` `{datapoint}` — fetch + base64 a Rossum document content URL stored in a field (see `export-workday-soap-attachment-data`).

## Params

- `tenant_domain` — Workday services host (e.g. `wd3-services1.myworkday.com`)
- `tenant` — Workday tenant id
- `api_version` — SOAP API version the WSDL is pinned to (e.g. `v42.2`)

## Produces / Consumes

- Produces: nothing — the connector owns the round-trip; export failure surfaces on the annotation.
- Consumes: `company_wd`, `supplier_wd` (matched Workday reference ids), `currency`, `document_id`, `date_issue`, `date_received`, `memo`, `file_name` (populate from `document.original_file_name` in a supplemental hook), and per line `item_description`, `item_total_base`, plus optional `item_tax_applicability_wd`, `item_cost_center_wd`, `item_project_wd`.

## Adapt

- **Reference-ID types are tenant configuration.** `Organization_Reference_ID`, `Supplier_ID`, `Tax_Applicability_ID`, worktag types (`Cost_Center_Reference_ID`, `Project_ID`, …) must match the tenant's reference-ID scheme; extend `Worktags_Reference` with further `$IF_SCHEMA_ID$`-guarded entries (fund, region, affiliate company) as coding requires.
- **PO-based invoices**: replace `Invoice_Line_Replacement_Data` with the block from `export-workday-po-line-type-projection`.
- **More than one attachment**: replace `Attachment_Data` with the block from `export-workday-soap-attachment-data`. Either way, attachments **>50 MB fail through Rossum infra**.
- **Approval routing**: `Business_Process_Parameters` is where auto-complete and the approver reference for the business process go; `Submit` can also read a field (`"@{submit_flag}"`) to hold given invoices in draft.
- Header-level free-text extras map to `Additional_Fields_Data_Reference` entries (`Attribute_Value` + `Configurable_Attribute_Reference`) — attribute names are tenant configuration.
- Credit notes: one observed source maps `Payment_Terms_Reference` per `document_type` via `$DATAPOINT_MAPPING$` and flips line sign via a quantity of −1.

See `rossum-reference` (Integrations) — and note the connector's mapping DSL above is documented only here so far.
