# export-workday-soap-invoice-mapping

Use this blueprint to submit an AP invoice to Workday via the Rossum-hosted Workday SOAP connector. The fragment is the webhook hook's `settings` object: a `wsdl` block naming the tenant, and a declarative `mapping` that the connector renders into a `Resource_Management` / `Submit_Supplier_Invoice` SOAP call. This skeleton carries the header projections plus a **non-PO (amount-coded) line block with worktags**; it is the composition root the other Workday export blueprints plug into.

## Hook wiring

Not a function hook — create a **webhook** on `annotation_content.export` with `config.url` pointing at the org's Workday connector service (`https://<org-domain>/svc/workday/api/v1/export`), and the tenant credentials in hook **secrets** (never in settings): `workday_username` + `workday_password`, WS-Security as `<username>@<tenant>`. The connector never writes secrets back, so declare a closed `secrets_schema` (`additionalProperties: false`). The fragment is the hook's `settings`. Queue targeting is two-layered: the hook's own `queues` list, then the **first** `configurations[]` entry matching the annotation's queue — and **no matching entry is a silent no-op** (HTTP 200, no message).

## Mapping DSL

The full grammar lives in `workday-reference` (§ The Mapping DSL) — including primitives this skeleton doesn't use (`$DATAPOINT_VALUE$`, `$IF_DATAPOINT_VALUE$`, `$CHILD_COUNT$`). What the skeleton relies on: `@{schema_id}` field substitution (row-scoped inside loops; **missing field = export fails** with a message naming the schema id), `{document_content}` (resource placeholder — the source document, base64), `$IF_SCHEMA_ID$` with `fallback_mapping: {}` to *omit* optional references (missing-or-empty → fallback; more than one match → error), and `$FOR_EACH_SCHEMA_ID$` multivalue iteration.

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
- **More than one attachment**: replace `Attachment_Data` with the block from `export-workday-soap-attachment-data`. Either way, mind attachment sizing: no connector-side cap exists, but base64 inflates ~33 % and a slow submit can time out platform-side *after* Workday created the invoice (`Add_Only` guards the retry) — see `workday-reference` § Attachments.
- **Approval routing**: `Business_Process_Parameters` is where auto-complete and the approver reference for the business process go; `Submit` can also read a field (`"@{submit_flag}"`) to hold given invoices in draft.
- Header-level free-text extras map to `Additional_Fields_Data_Reference` entries (`Attribute_Value` + `Configurable_Attribute_Reference`) — attribute names are tenant configuration.
- Credit notes: one observed source maps `Payment_Terms_Reference` per `document_type` via `$DATAPOINT_MAPPING$` and flips line sign via a quantity of −1.

See `workday-reference` for the connector contract this drops into — hook wiring, credentials, the full mapping-DSL grammar, error surface, and regions.
