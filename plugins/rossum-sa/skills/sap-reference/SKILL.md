---
name: sap-reference
description: SAP integration guide for Rossum. Covers the SAP product landscape (S4 HANA Public/Private Cloud, ECC, Ariba, VIM, BTP), AP/AR terminology, master data scoping, and — measured on three real integrations — the three shapes a Rossum–SAP integration takes (OData through a BTP API gateway; customer HTTPS middleware wrapping RFCs; S/4 Cloud OData via a CPI passthrough), their API contracts as actually observed (skiptoken paging, gateway SQL threat protection, RESULT_FLAG-vs-BAPIRET2, EV_MSG prose over HTTP 200, one-time vendors, goods-receipt latency), the SAP-table-to-API supplier field map, and how an invoice is written back (UBL 2.1 to OpenText VIM, BAPI-shaped create-then-attach with a duplicate guard, A_SupplierInvoice). Use when scoping, building, or debugging any SAP integration, master-data feed from SAP, or SAP/VIM export.
user-invocable: false
---

# SAP Integration Reference

Landscape for scoping, plus what three live Rossum–SAP integrations taught us about building
one — the shapes, the measured API contracts, master data out, invoices in. For complete
details, see [reference.md](reference.md). Drop-in code for the measured mechanisms is in the
parts library (`parts-index`): the `master-data/` importers and the two SAP export parts.

Use this knowledge when:
- Scoping a Rossum-SAP integration (which SAP product, which hop — gateway, middleware, IDOC, VIM)
- Choosing an integration pattern and estimating it: the three measured shapes carry effort signals (volume per object, endpoint latency, who owns API fixes)
- Building or debugging a master-data feed from SAP: OData `$skiptoken` paging, creation-vs-change-date scoping, `$expand` flattening, the gateway's `or` rejection, the `RESULT_FLAG`/`EV_MSG` envelope, `"0"` boundaries, page-1-only totals, one-time vendors, goods-receipt latency
- Designing the supplier feed: which SAP tables and `API_BUSINESS_PARTNER` properties carry what Rossum matches on (STCEG not TXJCD, BVTYP for VIM's bank selection)
- Building or reviewing an export into SAP: BAPIRET2 as the verdict, timeout = unknown outcome, state before attachment, park vs post, posting date, credit-note sign, UBL 2.1 to VIM and the gateway SQL threat protection, `BUS2081` attachments
- Operating scheduled syncs: staggered crons against MDH's single ingest worker, runtime budgets, egress whitelisting, secrets in payload logs
- Understanding SAP terminology (FICO, MIRO, GRNIV, IDOC, BAPI, LIFNR, BUKRS, ZTERM, RBSTAT, M8 535)
- Writing SOWs that involve SAP integration
