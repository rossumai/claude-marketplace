# export-ubl-to-vim

Send an invoice to **SAP / OpenText VIM** as **UBL 2.1 XML with the original document embedded
as base64**, POSTed as a multipart form part through an SAP API gateway (BTP Integration Suite
/ API Management) to VIM's inbound ingest endpoint. Archive the sent XML on the annotation.
Write VIM's ingest reference back so AP can find the document without reading a hook log.

This is the *direct* SAP export path: VIM sits next to SAP ECC / S/4 Private Cloud and exposes
an internet-facing ingest, so there is no IDOC and no customer middleware to build. The whole
mapping burden moves into one XML document — and into VIM's inbound mapping, which is why
element *presence* matters as much as element values.

## Where the difficulty actually is

Not the UBL. Three things cost days and are already handled:

**1. The gateway's SQL threat-protection policy.** It scans the *whole* request and rejects any
standalone SQL keyword with `403 {"code": "FORBIDDEN_PARAMETER, sql"}`. Three carriers were hit,
each once: the **base64 PDF** (`drop` twice in one PDF's base64, then `/or+`), an **attachment
filename** (`"... cost center or IO (1).pdf"`), and an **XML developer comment** (`"so nothing is
added or removed"`). The fragment neutralises base64 by inserting a newline mid-keyword
(whitespace inside `xsd:base64Binary` is legal and every decoder ignores it — the decoded bytes
are identical), cleans the filename stem, strips comments from the bytes, and runs a
**preflight that reports** anything left. It never rewrites invoice data to dodge a WAF.

**2. VIM's mapping reads paths.** An element VIM maps must *always* be present, so a withheld
value ships as an **empty element** (`<cbc:ID></cbc:ID>`, `<cbc:CompanyID></cbc:CompanyID>`) —
except `xsd:decimal` / `xsd:date` elements (`cbc:Percent`, `cbc:DueDate`), where empty is
schema-invalid and **omission** is the only option. A conditionally present node is exactly
what breaks a hard read.

**3. The transport.** Multipart form-data, UBL as a part named `document` with part type
`text/xml`; do **not** set a request `Content-Type` (the boundary comes from `requests`).
`sap-client` is mandatory on every SAP API and `apiKey` on the ingest route — both **query
parameters**. OAuth2 client credentials for the bearer.

## What VIM answers

```json
{"id":"<guid>","processId":"","status":"COMPLETED",
 "documents":[{"id":1,"filename":"invoice.xml","regid":445286,"status":"REGISTERED"}]}
```

`regid` is the quotable sequential ingest id; `id` the GUID. **The DP number AP works with is
not in this response** — VIM assigns it later in its own workflow. The write-back gets you to
the ingested document, not the DP; carrying the DP needs a VIM callback or a status poll.

## The UBL the fragment builds

Plain UBL 2.1 (`CustomizationID urn:oasis:names:specification:ubl:xsd:Invoice-2`, so
`mimeCode` is unrestricted and TIFF is legal). Header: `ID`, `IssueDate`, `InvoiceTypeCode`
(**380 invoice / 381 credit note — credit notes ship positive amounts and let the type code
carry the sign**; a negated total is not even self-consistent), `Note` = `ROSSUM ID - <annotation
id>` (AP's traceback), `BuyerReference` = company code, `OrderReference` = matched PO,
`AdditionalDocumentReference` with `DocumentTypeCode 130` and the embedded document, supplier
party with `schemeID="SAP-LIFNR"`, customer party with `schemeID="SAP-BUKRS"`, optional
`PaymentTerms` note `ZTERM=<code>`, `TaxTotal`, `LegalMonetaryTotal`. Lines: quantity with
`unitCode`, `LineExtensionAmount`, `OrderLineReference/LineID` = matched PO item, item
description, `ClassifiedTaxCategory/ID` = SAP tax code, `Price` derived so **price × quantity
equals the line net within 2 cents** (EN 16931 checks it; the billed line net is authoritative).

The MIME type is sniffed from the bytes (magic number → Rossum's `mime_type`, which can be the
literal string `"empty"` → extension → PDF): a TIF mislabelled as PDF was accepted by VIM and
rendered wrong downstream.

## Params

All seams are schema ids except `«relation_key»`. `«export_target_field»` is the routing gate —
the same field that should gate any other target's pipeline, so a document goes to exactly one
system. `«company_code_field»` is what lets one hook serve every SAP queue.

Settings: `VIM_URL`, `TOKEN_URL`, `SAP_CLIENT`, `FORM_FIELD_NAME` (`document`),
`LOG_FULL_PAYLOAD` (`true` logs the whole UBL including base64, for SAP-side replay), optional
`COMPANY_CODE` fallback. Secrets: `client_id`, `client_secret`, `vim_api_key`.

## Archive semantics

The sent XML is stored as a `document_relation` of type `export` under `«relation_key»`. Two
API facts shape that code: a relation with documents **cannot be deleted** and
`(type, annotation, key)` is **unique**, so a re-export must PATCH the existing relation; and
the lookup must filter on the **annotation id** server-side — filtering by key alone returns
every annotation's relation 20 to a page, and once more than a page of documents had exported
the create hit the uniqueness constraint with a 400.

## What was deliberately left out

The production hook carried a customer's field-level policy that changed several times in one
month — supplier VAT registration on/off, tax code vs tax rate, due date, bank details by
vendor region, one-time-vendor rules, a second export target with stub reply relations for the
other pipeline's extractors. None of it generalises. Add such rules as flags at the top of
`build_ubl`, and keep them flags: they will be reversed.

See `sap-reference` → *OpenText VIM: UBL 2.1 with an embedded document*.
