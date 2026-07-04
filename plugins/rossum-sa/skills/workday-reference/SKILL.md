---
name: workday-reference
description: Rossum Workday connector reference. Covers the Rossum-hosted SOAP connector (svc/workday) — export and import hook wiring and secrets, the wsdl block and impl-vs-prod tenant domains, the mapping template language (IF_SCHEMA_ID, FOR_EACH_SCHEMA_ID, DATAPOINT_MAPPING operations and the @{schema_id} shorthand), Submit_Supplier_Invoice conventions (reference IDs, worktags, attachments, goods-vs-service lines), and master-data import into MDH datasets with differential sync. Use when building, debugging, or explaining a Rossum-Workday integration.
user-invocable: false
---

# Workday Integration Reference

This skill documents the **Rossum-hosted Workday connector** — the webhook service that
submits AP invoices to Workday via its SOAP API (`Submit_Supplier_Invoice`) and imports
Workday reference data (suppliers, POs, cost centers, tax objects, …) into Master Data
Hub datasets. Configuration is pure JSON in the hook's `settings`; no code is required.

For the full guide — wiring, the mapping DSL primitive by primitive, payload conventions,
import operations, scheduling, and gotchas — see [reference.md](reference.md).

Use this knowledge when:

- Wiring or reviewing a Workday export or import hook
  (`https://<org base>/svc/workday/api/v1/…`, secrets, queue-based config selection)
- Writing the export `mapping` — field references, conditional blocks, line iteration,
  value switches, attachments
- Projecting invoice lines onto Workday's goods vs service line model
  (`Quantity` × `Unit_Cost` vs `Extended_Amount`)
- Setting up master-data import — `Get_*` operations, MDH datasets, `replace` vs
  `update`, differential sync, cron cadences
- Debugging tenant connectivity — implementation vs production domains, ISU
  permissions, IP allowlists

Note: this pack covers the dedicated Workday SOAP connector. For REST/JSON export
targets (including Workday's REST procurement API called from a custom hook), see
`export-pipeline-reference`; for the datasets the import fills and the matching that
reads them, see `mdh-reference`.
