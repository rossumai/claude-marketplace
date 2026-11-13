---
name: einvoicing-reference
description: Use when handling e-invoices in Rossum — choosing a route (B2Brouter E-Invoicing extension, hosted e-invoice upload import, plain SFI, OCR fallback); setting up or debugging the B2Brouter importer (einvoice-dispatcher webhook, b2b_router_* settings, Peppol polling, API/email uploads of XRechnung/ZUGFeRD/Factur-X/UBL/CII); field mapping from B2Brouter's canonical JSON or xml_mapping; EN 16931 validation results, "E-invoice is not valid.", e_invoice_validation_json, e_invoice_id; Re-extract on e-invoices; wrong or stale IBAN on e-invoice annotations; the French B2Brouter Store jobs (cross-border reporting, report polling, rejection reporting, e_report_step, e_invoice_rejection_*); the coupa-einvoicing upload import with file-role metadata (compliant_data, visualization, attachment); Sproom / NemHandel / OIOUBL intake — even when the user only says "e-invoice" or "Peppol".
user-invocable: false
---

# E-Invoicing Reference

Rossum handles e-invoices — invoices whose data already arrives as structured XML (UBL, CII, Peppol BIS, XRechnung, ZUGFeRD/Factur-X, OIOUBL, …) — through a few different mechanisms. This pack is the umbrella: it helps choose the route, then documents each Rossum-provided route end to end. Mapping syntax (XPath/JSONPath selectors, `mapping`, date formats) lives in `sfi-reference`, which all routes share.

See [reference.md](reference.md). Consult it when:

- Choosing how a customer's e-invoices should enter Rossum ([Choosing a route](reference.md#0-choosing-a-route)).
- Setting up the **B2Brouter E-Invoicing extension** — network polling and Rossum uploads, every setting, the canonical JSON shape for both B2Brouter API generations, `xml_mapping`, validation and how to show it to users, Re-extract.
- Setting up or debugging the **French B2Brouter Store jobs** (cross-border reporting, report polling, rejection reporting) and the schema fields they read and write.
- Setting up the **hosted e-invoice upload import** (`coupa-einvoicing` endpoint) and its upload metadata contract.
- Bringing in e-invoices from **Sproom** (Danish NemHandel/OIOUBL network).
- Troubleshooting any of the above (one table, symptom first).

> **Read this first**
> - The B2Brouter importer is **not a Store tile**: you create a plain webhook pointing at the Rossum-hosted dispatcher. Verified URLs exist for **EU1 and EU2 only**; for other regions ask Rossum.
> - The org group needs the **`einvoicing` feature** (Rossum enables it), or every e-invoice fails.
> - **Re-extract on an e-invoice annotation purges it** and creates a new annotation (new ID). Edits on the old one are lost.
> - Do **not** take bank details from B2Brouter's `contact_iban`: it can come from B2Brouter's stored supplier contact rather than from the invoice. Map the account from the XML.
> - The French Store jobs are **private templates** (visible only to orgs Rossum enables); the Sproom intake is a serverless function you get from Rossum's connectors team, not a Store extension.

Cross-references:

- `sfi-reference` — selector syntax and plain Structured Formats Import (also for structured files that are not e-invoices).
- `coupa-baseline-reference` §8 — CIB 2.0, where Coupa (not Rossum) receives e-invoices and uploads them.
- `b2brouter-reconciliation` — prove every e-invoice B2Brouter received landed in Rossum; also its TLS-interception notes for calling B2Brouter from a corporate network.
- `txscript-reference` — formula fields (the validation-message recipe uses one).
- `rossum-reference` — hooks, sideloads, job hooks and their run logs.
- `iterate` — re-fire primitives for testing a mapping change on a real annotation.
