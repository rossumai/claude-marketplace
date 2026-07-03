---
name: workday-reference
description: Rossum Workday connector reference. Covers the Rossum-hosted SOAP connector's export/import endpoints (svc/workday), hook wiring (annotation_content.export webhook, queue-based config selection, token_owner, workday_username/workday_password secrets + secrets_schema prefill), the wsdl block and impl-vs-prod tenant domains, the full mapping DSL (@{schema_id}, {document_content}, $DATAPOINT_VALUE$ with value_type, $IF_SCHEMA_ID$/fallback_mapping, $IF_DATAPOINT_VALUE$, $FOR_EACH_SCHEMA_ID$ with schema_loop, $DATAPOINT_MAPPING$ value switches, $FETCH_DOCUMENT_CONTENT$), Submit_Supplier_Invoice conventions (reference IDs, worktags, PO line references, attachments), the goods-vs-service line model, export error messages, and master-data import into MDH datasets (Get_* operations, replace/update + id_keys, ${current_datetime}/${last_modified_date} differential sync, job_run_settings, cron scheduling). Use when building, debugging, or explaining a Rossum-Workday integration.
user-invocable: false
---

# Workday Integration Reference

This skill documents the **Rossum-hosted Workday connector** — the webhook service that
submits AP invoices to Workday via its SOAP web services (`Submit_Supplier_Invoice`) and
imports Workday reference data (suppliers, POs, cost centers, tax objects, …) into Master
Data Hub datasets. Configuration is pure JSON in the hook's `settings`; no code is required.

For the full configuration guide — wiring, the mapping DSL primitive by primitive, payload
conventions, import operations, scheduling, and gotchas — see [reference.md](reference.md).

Use this knowledge when:

- Wiring a Workday invoice export (webhook on `annotation_content.export` pointing at
  `https://<org base>/svc/workday/api/v1/export`, per-queue configuration selection)
- Writing or reviewing the export `mapping` — `@{schema_id}` field references,
  `$IF_SCHEMA_ID$` optional blocks with `fallback_mapping`, `$FOR_EACH_SCHEMA_ID$` line
  iteration, `$DATAPOINT_MAPPING$` value switches, `$IF_DATAPOINT_VALUE$`,
  `$FETCH_DOCUMENT_CONTENT$` attachments, `$CHILD_COUNT$`, `value_type` conversions
- Deciding how invoice lines project onto Workday's goods vs service line model
  (`Quantity` × `Unit_Cost` vs `Extended_Amount`) and switching the projection per line
- Building Workday reference wrappers (`ID` / `type` / `_value_1`), worktags, PO line
  references (`Line_Number` with `parent_id`/`parent_type`), or configurable attributes
- Setting up master-data import: `Get_*` operations, `dataset_name` targets in MDH,
  `method: replace` vs `update` with `id_keys`, `${current_datetime}` /
  `${last_modified_date}` differential-sync placeholders, cron schedules
- Debugging tenant connectivity: implementation vs production Workday domains
  (`wd3-impl-services1.workday.com` vs `wd3-services1.myworkday.com`), ISU permissions,
  IP allowlists
- Explaining what the connector does **not** do (notably live PO received/invoiced
  consumption — and the generic REST workaround)

Note: this pack covers the dedicated Workday SOAP connector. For REST/JSON export targets
(including Workday's REST procurement API called from a custom hook), see
`export-pipeline-reference`; for the datasets the import side fills and how matching reads
them, see `mdh-reference`.
