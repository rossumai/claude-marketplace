# SAP Integration Guide for Rossum

Two halves. The first is the landscape an SA needs to scope an SAP deal. The second is what
three real Rossum–SAP integrations taught us about *building* one: the three shapes they take,
the API contracts as measured (not as documented), how master data comes out of SAP and how an
invoice goes back in. Everything marked **measured** was observed on a live system in 2026;
everything else is documented SAP behaviour or scoping judgement.

Drop-in code for the measured mechanisms lives in the parts library — see *Parts* at the end.

## SAP Product Landscape

### SAP HANA
In-memory, columnar, relational database that runs SAP products.

### SAP ECC 6
Legacy ERP system running on various RDBMS (mainly Oracle). SAP is ending support in 2027 (exceptions to 2030). All prospects/customers are in transition to S4 HANA.

### SAP S4 HANA
Successor of ECC 6. Improved database (HANA). Current and future standard.

### SAP S4 HANA Public Cloud
- Cloud version of S4 HANA with standard APIs (OData, SOAP, REST)
- Standardised version common for all tenants
- Easy to integrate — all APIs available on the internet
- Rossum has generic master data "import" extension and export function (MEGA)
- Almost no customers use this yet — SAP pushes "Clean Core" but most customers have heavy customisations
- **Scoping**: Easy to integrate, APIs are fairly robust. Always review the necessary API(s) before committing. The standard OData services (`API_BUSINESS_PARTNER`, `API_PURCHASEORDER_PROCESS_SRV`, `API_SUPPLIERINVOICE_PROCESS_SRV`, `API_CV_ATTACHMENT_SRV`) are the ones Rossum's own SAP demo integrates — see *Shape C*.

### SAP S4 HANA Private Cloud
- On-premise version of S4 HANA. Backend is very different from Public Cloud (full DB access, ABAP customisations)
- Migration from ECC is simpler to this version (only option when ECC was customised)
- **Most customers and prospects** want integration with this version
- Integration always requires a hop — either middleware the customer owns (Shape B), an API gateway such as BTP Integration Suite in front of the on-prem system (Shape A), or IDOC generation with MEGA
- Traditional integration: generate IDOC (SAP XML structure per transaction, e.g. ORDERS05, INVOIC02)
- **Scoping**: Critical questions:
  - What is their IT landscape?
  - Do they already integrate with SAP and how?
  - What middleware or gateway tool are they using, and who owns changes to it?
  - As long as they provide a pathway through middleware or a gateway, Rossum can integrate

### SAP Ariba
Spend management system (procurement, sourcing, supplier management). Not an ERP. Rossum focuses on Coupa instead. APIs differ from native SAP modules.

### SAP Ariba Network
Supplier/Buyer cloud platform for B2B transactions.

### OpenText VIM (Vendor Invoice Management)
- Plugin installed on-prem next to SAP ECC/S4 Private Cloud but exposes internet-facing APIs
- Allows Rossum to integrate directly without middleware — one production integration sends UBL 2.1 XML with the embedded PDF straight to VIM's inbound ingest (see *Writing invoices into SAP*)
- Competitor in a way (offers poor OCR but superior UI/Workflow to vanilla SAP)
- VIM assigns its DP number in its own workflow *after* ingest; the ingest response carries only a registration id

### SAP CIM
SAP's answer to VIM. Early stage, limited capabilities, inferior to VIM.

### SAP BTP (Business Technology Platform)
- Cloud platform with many components; most important: Integration Suite (formerly CPI) and API Management
- Integration platform like Mulesoft, Azure Logic Apps, UiPath
- **Increasingly the gateway in front of an on-prem SAP**: two of the three integrations measured below reach SAP through it — once as an API-Management proxy publishing OData entity sets with OAuth2, once as a CPI iFlow that forwards Rossum's HTTP call to whatever OData URL a header names. Expect its **SQL threat-protection policy** and its **undocumented page caps** (Shape A).

### SAP Cloud Connector
Reverse proxy installed on-prem allowing BTP to connect to on-prem SAP. Whether it sits behind a customer's gateway is invisible to Rossum and irrelevant to the contract.

### SAP Fiori Apps
SAP Web framework for business applications. Used by both Private and Public Cloud. Private cloud also runs SAP GUI and SAP Web GUI (via NetWeaver).

## Master Data

**Biggest pain point** for most customers (aside from Public Cloud where APIs are robust). Without deep SAP/ABAP knowledge, it's difficult to customise master data export or implement deltas.

**Scoping checklist:**
- How will master data exchange happen?
- Do they already produce master data for other systems? If so, get samples.
- Ask about deltas — especially for AR Material Master (100s of thousands of records)
- Ask about **volume per object**: one measured PO-item feed was **3.7 M rows**, 213× the vendor master of the same customer. That decides full-load strategy (see *Operating a sync*).
- Ask who owns the API layer. Every measured gotcha below needed a change *on the SAP side* at least once (a filter that 500s, a missing entity, a whitelist).

## SAP Customisations

- **Public Cloud**: No customisations (standard workflows, forms, dashboards only)
- **Private Cloud**: Full customisation via ABAP (Advanced Business Application Programming)
  - New data models, RFC-enabled functions, BAPIs
  - **Scoping**: Ask if they have custom tables and can provide them as master data. A customer-built REST facade over a **Z-RFC** (Shape B) is a customisation too — its field names, envelope and quirks are theirs, not SAP's.

## AP/AR Terminology

| Term | Description |
|------|-------------|
| FI | Financial Accounting — financial accounting, reporting |
| MM | Material Management — procurement & inventory management |
| GRNIV | Goods Receipt Invoice Validation — validates sufficient received amount for invoice posting. Rossum can do this with GR master data |
| FICO invoice | Non-PO backed invoice (typically INVOIC02 IDOC) |
| MIRO invoice | PO-backed invoice (typically INVOIC02 IDOC) |
| Sales Order | AR — typically ORDERS05 IDOC |
| LIFNR / BUKRS / ZTERM | vendor number / company code / payment-terms key — the three codes every export needs and every supplier feed must carry |
| BAPIRET2 | SAP's standard return table (`TYPE`, `ID`, `NUMBER`, `MESSAGE`); `TYPE` in `A`/`E`/`X` means failure |
| RBSTAT | invoice document status: `A` parked, `5` posted |
| BUS2081 | the supplier-invoice business object; attachments link to `SupplierInvoice + FiscalYear` |
| M8 535 | "Posting only possible in periods …" — the closed-period rejection |

---

## Three integration shapes, measured

| | **Shape A — OData through an API gateway** | **Shape B — customer HTTPS middleware wrapping RFCs** | **Shape C — S/4 Cloud OData via a CPI passthrough** |
|---|---|---|---|
| SAP side | S/4 Private Cloud behind BTP API Management | ECC / S/4 behind a customer-built REST facade over Z-RFCs | S/4 (Public Cloud-style standard OData services) |
| Auth | OAuth2 client credentials → bearer | token endpoint (API key + user + password headers) → API token header | HTTP Basic, forwarded by the iFlow |
| Master data in | OData entity sets, `$filter`/`$expand`, numeric `$skiptoken` | JSON POST body per endpoint, `Skip`/`Top`, `RESULT_FLAG`+`DATA` envelope | OData `d.results` unwrapped by a function hook |
| Invoice out | UBL 2.1 + embedded PDF → OpenText VIM ingest | `CREATE_INVOICE` then `ATTACH_INVOICE` (BAPI-shaped JSON) | `A_SupplierInvoice` POST, then `AttachmentContentSet` with `BUS2081` |
| Who fixes the API | customer's SAP/Basis team via the gateway | customer's middleware team | whoever owns the iFlow (Rossum, in the demo) |
| Parts | `mdh-odata-import-skiptoken-sync`, `mdh-odata-filtered-full-refresh`, `export-ubl-to-vim` | `mdh-import-watermark-sync`, `export-create-then-attach-with-state` | `export-create-upload-submit` + `export-oauth-token-cache` as the Request Processor shape |

All three converge on the same Rossum-side design: **a two-phase importer (full load, then watermark) per master-data object, state in its own Data Storage collection, PATCH-merge on a natural key**, and an export whose success is decided by the *business* response, never the transport status.

## Shape A: OData through an API gateway

SAP publishes OData v4 entity sets (`GETPurchaseOrder`, `GETPurchaseOrderItem`, …) through BTP API Management with OAuth2 client credentials. Everything below was **measured** on one such gateway, 2026-01 → 2026-09, against ~2.3 M PO items.

**Paging**
- Page with **`$top` and an incrementing numeric `$skiptoken` offset**. The gateway's relative `@odata.nextLink` was not reliably reconstructible and silently truncated a windowed pull.
- **Only an empty page ends a walk.** The gateway serves fewer rows than `$top` (undocumented per-entity cap, harder on `$expand`); treating the first short page as the end truncated a full load to one page. Advance the offset by rows actually returned — a cap costs requests, never correctness.
- **Order by the entity key, never by a date.** Creation dates are day-granular; thousands of rows tie at every boundary and the server reorders ties between requests, so offset paging skips and duplicates.
- Concurrency: the gateway **serialises beyond ~4 parallel requests**; 8 and 16 workers were no faster.

**Filtering**
- **Scope a full load on the creation date, an incremental on the last-change date.** Filtering a full load on `LastChangeDateTime` redefines it as "touched since X".
- **A standalone `or` is rejected with `403 {"code": "FORBIDDEN_PARAMETER"}`** — the gateway's SQL threat protection; 2 terms and 5 terms alike, so it is the keyword, not the count. `in (...)` returns 500. Only `and` survives: a filter can express a *range*, never a *set*. Per-key lookups therefore mean one request per key.
- Narrow the work list **locally** before sending anything — e.g. only 25 % of PO items carried an account-assignment category, and both sides could filter on it.
- **`$select` strips your watermark.** `$select=PurchaseOrder` returned 0 of 25 headers with `LastChangeDateTime`; adding it returned 25 of 25. Every child then lands with an empty watermark and the next run restarts from the beginning.
- Edm.Date literals are bare ISO dates (`CreationDate ge 2000-01-01`); datetime literals ISO with `Z`.

**Entities**
- A child entity (PO item) may carry **no date at all**. Window it through its parent: header `$filter` + `$expand=_PurchaseOrderItem`, then flatten and **stamp the header's dates (and key) onto every child**. Nested expands work (`_Item($expand=_AccountAssignment)`) but need `$top` 100–200; a separate feed on its own cursor was the better shape.
- **A documented entity may simply not be deployed**: `GETPurchaseOrderAccountAssignment` returned `404 ApplicationNotFound` on the route the customer had published. Probe every entity before designing around it.
- Navigation names ≠ entity names (`_PurOrdAccountAssignment` vs `PurchaseOrderAccountAssignment`); make both settings, not constants.
- Drop `SAP__Messages` and any list/dict value before writing to MDH so columns stay scalar.
- **Deletes never surface** through GET — a removed row stops appearing. For drift detection run a nightly full-replace companion (`mdh-odata-filtered-full-refresh` with an empty filter). Soft deletes do surface as flags (`PO_DELETION_CODE`, `ITEM_DELETION_CODE`) and must be carried, not filtered.

**One-time vendors.** On an OTV purchase order SAP puts a placeholder vendor code on the PO (`ONETIME1/2/3`); the real vendor exists only as a typed address under the header's `_SupplierAddress`. The placeholder *does* exist in the supplier master — as a dummy record — so every field resolved from the master is meaningless. Carry the PO's own address into a dedicated collection (measured: 0.2 % of POs; 259 of 5,134 carried no change stamp, so a filtered daily full refresh, not a watermark). Downstream: no VAT, no due date from the placeholder's payment terms, bank details only from the page — and only for regions where that is acceptable.

## Shape B: customer HTTPS middleware wrapping RFCs

The customer's integration team exposes one REST endpoint per Z-RFC (`Rossum_GET_VENDORS`, `Rossum_GET_PURCHASE_ORDERS`, `Rossum_GET_GOODS_RECEIPT`, `Rossum_CREATE_INVOICE`, `Rossum_ATTACH_INVOICE`), behind a token endpoint. Everything below was **measured** on one such middleware, 2026-08 → 2026-09.

**Connectivity first — and read the two failures apart**
- Serverless hooks may have **no outbound internet** until it is enabled; and the networking is applied **when the function is deployed**, so an existing hook only picks it up after a config change forces a redeploy. Symptom: `ConnectTimeout` with no TCP handshake — looks exactly like a firewall drop at the customer.
- The customer **whitelists Rossum's egress IPs** (four per region). A request that *reaches* the application and is rejected comes back with an error code (`E101`). A laptop is not on that list — testing from a workstation will keep failing after the whitelist works, and the office proxy's egress rotates inside a /24. Verify through the deployed hook, or get the whole /24 whitelisted.

**The envelope**
- `{"DATA": {...}, "RESULT_FLAG": "S"}`. `RESULT_FLAG` is the *transport* status. **Validation failures arrive as HTTP 200 with `RESULT_FLAG: "S"`** and the reason as localized prose in `DATA.EV_MSG`. `EV_MSG` is `null` (not `""`) on success and is the only reliable error channel — match it loosely; it is not a stable code.
- An **empty delta window may be reported as a message**, not an empty page (`No goods receipt records match the requested criteria`). A quiet 30-minute window is the *normal* case, so that one message is a terminator; every other is a failure.
- The **token endpoint needs a body** (`{}`; an empty POST gets `411 Length Required`), returns the token at the top level with no envelope, base64 (never URL-encode it), re-issue on `401` or `RESULT_FLAG "E"`. One deployment stated 600 min validity and 100 requests per token.
- **The documented request/response names may be the RFC signature, not the JSON contract.** `IV_*` request names were rejected (`No input parameters received`); the working names were friendly strings copied from the customer's own call. Casing differed *between endpoints* (`Fiscal Year` vs `Fiscal year`); a response key shipped with a trailing period (`"Document identifier."`). **Copy names verbatim from a working call and never tidy them.**
- Types drift from the guide: `ET_RETURN.NUMBER` arrived as a JSON integer, not a zero-padded string; `LAST_CHANGED` is the number `0` in full mode, not empty; `Skip`/`Top` were accepted as strings *and* integers.

**Paging and windows**
- `Skip = PageIndex × Top`. **The total (`EV_TOTAL_LINES`) is returned only when requested (`"Request Total Lines": "X"`) and only on the first page** — later pages report 0; capture and store it. It is always 0 in delta mode, where a page shorter than `Top` ends the window.
- **Overrunning a delta window returns prose, not an empty page** when the window size is an exact multiple of `Top`.
- Delta windows are **semi-open UTC `[from, to)`** in `YYYYMMDDhhmmss`; supplying one boundary alone is an error, `from >= to` is an error. **Inactive boundaries must be `"0"`, not `""`** — the empty string produced HTTP 500 with no `EV_MSG` on one endpoint although its guide permitted it.
- **One deployed key was misspelled** (`"Chage From Date"`). The guide says preserve it; the corrected spelling is silently ignored and every delta becomes a full load.
- A delta selects **parent records affected in the window and returns all their current children** (5 POs → 113 items). Volume scales with children per parent; `LAST_CHANGED` is parent-level.
- The **documented bounding filter may not work**: a document-date range returned HTTP 500 regardless of other parameters, so the recommended "controlled initial extraction" was unusable. Alternatives: split by company code; filter by document type; or seed from a wide delta window and let incrementals accumulate.

**Endpoint cost differs by an order of magnitude.** Vendors: ~1.7 s a delta page. POs: ~0.4 s a delta page, ~6 s a full page, 3.7 M items ≈ 745 pages of 5,000 ≈ three days of draining on a 30-minute schedule with a 35 s budget. Goods receipts: **12–18 s per call regardless of row count** on a change-date filter — the cost is query planning — while an *anchored* query (one PO, one posting date) answers in 0.5 s. Consequences: `request_timeout_s ≥ 25`, ~2 pages per run, historical load **partitioned by posting day** (~625 receipts/day, ~20 days per 22 s invocation), and receipts for a *specific* PO fetched **on demand** at match time rather than backfilled.

**Data facts that changed the design**
- Supplier numbers **repeat across company codes** (17,569 rows, 16,505 distinct suppliers): the natural key is `SUPPLIER + COMPANY_CODE`.
- `BLOCKED = "X"` on 61 % of vendors is per-company-code blocking, not blocked vendors — confirm before filtering them out of matching.
- `DELIVERY_NOTE_NUMBER` was populated on **18 % of goods receipts**, so a receipt-without-PO matching strategy cannot be primary; PO-anchored (invoice → PO line → its receipts) carries the load.
- A receipt carries **no supplier**; matching chains receipt → PO line → verify the PO's supplier against the already-matched vendor.
- Receipts are **reversed, never deleted** (`IS_REVERSAL`, movement type 102); merge-only storage is correct and a three-way match must net reversals out. Key: `MATERIAL_DOCUMENT + MATERIAL_DOCUMENT_YEAR + MATERIAL_DOCUMENT_ITEM`.

## Shape C: S/4 Cloud OData via a CPI passthrough

Rossum's own SAP S/4HANA demo. A CPI iFlow exposes a handful of HTTP endpoints (`/http/get_bps`, `/http/post_invoice_odata`, `/http/post_attachment`); the caller names the *real* OData target in two custom headers — **`_url`** (the `/sap/opu/odata/sap/<SERVICE>/<EntitySet>` path on the backend host) and **`_query`** (`$filter`, `$expand`, `$select`, always `sap-client=`). HTTP Basic is forwarded.

- Master data: function hooks GET the iFlow, unwrap OData v2 **`d.results`**, and upload the array as a multipart file to `/svc/master-data-hub/api/v1/dataset/<name>` with `replace_or_new=true` (`PUT` replace, or `PATCH` with `id_keys`). Business partners with `$expand=to_Supplier/to_SupplierCompany`; PO items with `$expand=to_PurchaseOrder` and a `$select` list; cost centres, GL accounts, tax codes as full replaces. The demo's date filters are hard-coded cut-offs — a real deployment uses the watermark sync.
- Export: Custom Format Templating renders the `A_SupplierInvoice` JSON (header fields + `to_SuplrInvcItemPurOrdRef.results[]`), REST API Export POSTs it with **`x-csrf-token: fetch`** and Basic auth, storing reply headers/body as document relations; a "Parse response" function reads the relations, treats an `<error>` root as failure (`<message>` → `show_error`) and an `<entry>` root as success (`<title>` → `show_info`), from which formulas regex the SAP object key. An "Attach document" function then POSTs the original binary to `API_CV_ATTACHMENT_SRV/AttachmentContentSet` with `BusinessObjectTypeName: BUS2081`, `Slug: <filename>`, `LinkedSAPObjectKey: <SupplierInvoice><FiscalYear>`.
- Idempotency is **left to SAP**: dedup by `SupplierInvoiceIDByInvcgParty + InvoicingParty + CompanyCode`; the attachment hook runs with `retry_count: 1` to avoid double-attaching. Contrast with Shape B, where Rossum keeps its own state.
- An inactive Request Processor variant (`get_content` → `call_api` → `evaluate`) collapses the four hooks into one — the shape new work should take (`export-create-upload-submit`).

## Master data from SAP: what Rossum needs and where SAP keeps it

Derived from what matching and export actually **read** on a live implementation, mapped to SAP tables and to the standard `API_BUSINESS_PARTNER` properties. Reconcile every property against the live `$metadata` — the baseline catalogue is not the customer's system.

| Rossum use | SAP source | `API_BUSINESS_PARTNER` |
|---|---|---|
| vendor number (primary key; = `Supplier` on the PO; = LIFNR in the export) | LFA1-LIFNR | `A_Supplier.Supplier` |
| name (dropdown label, name matching) | LFA1-NAME1/2 | `A_BusinessPartner.OrganizationBPName1/2` |
| active status (every query filters on it) | LFA1/LFB1/LFM1-LOEVM per the deletion logic | not a baseline flag → confirm in `$metadata`; else the block flags `PostingIsBlocked`, `PurchasingIsBlocked`, `SupplierIsBlockedForPosting` |
| **VAT registration** (primary matching key) | **LFA1-STCEG** — not TXJCD, which is the tax *jurisdiction*; one customer spec had this wrong | `A_Supplier.VATRegistration` |
| local tax numbers (countries without VAT reg.) | LFA1-STCD1/2/3 | `A_BusinessPartnerTaxNumber` (all types) |
| country, postal code, street, city, region | LFA1-LAND1, PSTLZ, STRAS, ORT01, REGIO; ADRC | `A_BusinessPartnerAddress` via `A_BuPaAddressUsage` |
| payment terms code + text | LFB1-ZTERM / LFM1-ZTERM; T052U for text | `A_SupplierCompany.PaymentTerms` per `CompanyCode`; **text is not in the API** — send a T052U table or drop it |
| payment method | LFB1-ZWELS | `A_SupplierCompany.PaymentMethodsList` |
| company-code assignment (is the supplier valid for this entity?) | LFB1-BUKRS | `A_SupplierCompany.CompanyCode` |
| remit-to / bank (**incl. partner bank type — VIM selects the bank by BVTYP**) | LFBK: BANKS, BANKL, BANKN, BKONT, BKREF, **BVTYP**, KOINH | `A_BusinessPartnerBank`: `BankCountryKey`, `BankNumber`, `BankAccount`, `IBAN`, `SWIFTCode`, `BankIdentification` |
| withholding tax code | LFBW: WITHT, WT_WITHCD | `A_SupplierWithHoldingTax` per `CompanyCode` |
| partner supplier (payments to another vendor) | WYT3-LIFN2 | `A_SupplierPartnerFunc` |
| one-time-vendor account group | LFA1-KTOKK | `A_Supplier.SupplierAccountGroup` |

Shape: **one record per vendor number with company-code and bank data nested**, because matching `$lookup`s by number and reads nested paths — a flat per-company-code row set needs a different MDH design. Delta on `A_BusinessPartner.LastChangeDate`; bank and tax entities may need their own change detection.

**Purchase orders.** Header and item in separate collections, key `PurchaseOrder` / `PurchaseOrder + PurchaseOrderItem`. Carry: company code, supplier, currency, document type, item text, material, quantity + unit, net price + price unit, tax code, `GR_FLAG` / `GR_BASED_INVOICE`, deletion codes, and — for export — the header's **payment terms (ZTERM)**. Vendor and payment terms on the export come from the **matched PO header**, which overwrites the vendor match (config order is load-bearing).

**Goods receipts.** Key `MATERIAL_DOCUMENT + YEAR + ITEM`; reversals are separate records. Prefer fetching a PO's receipts on demand at match time (0.5 s) to a historical backfill (minutes per year). The GR-aware invoiceable quantity (`delivered − invoiced`) and the PO line's tolerance are what a three-way match at export needs.

**Account assignments** (GL account, cost centre, WBS, order, asset). SAP normally derives them from the PO for PO-referenced invoices and does not want Rossum's — settle with the SAP/VIM team whether to send them at all before building the feed.

## Writing invoices into SAP

Three write paths were measured. Shared rules first, because they are where the money is.

**Rules that hold for every path**
1. **The transport status is not the business outcome.** Scan the BAPIRET2 table (`ET_RETURN` / `RETURN`): any row typed `A`, `E` or `X` fails the request. HTTP 200 + `RESULT_FLAG "S"` + an `E` row was observed. Render *every* row into the operator message so warnings survive.
2. **A timeout is an unknown outcome.** Without a read-back endpoint you cannot ask SAP whether the document exists, and Rossum's function ceiling (~50 s) makes this live. Record `unknown`, **never auto-retry**, and ask the customer for a read-back by reference number + company code — or confirm SAP's duplicate check on `REF_DOC_NO` is active.
3. **Persist the created document number before the attachment step**, in a Data Storage collection keyed by annotation id, not as hook `operations` — a crash in step 2 must not lead to a second invoice in step 1. Write the outcome back onto the annotation (`sap_invoice_number`, `sap_fiscal_year`, archive id, status, message) with `edit: disabled` on the number.
4. **Park first (`RBSTAT "A"`), post later (`"5"`).** Parking lets AP review in SAP; every early test parks against staging.
5. **Posting date is the run date, not the document date** (the customer's own example posted today for a document dated months earlier). On `M8 535` the message names the open periods — a retry inside one costs one call and the rejection created nothing.
6. **Credit notes**: positive amounts, direction in the indicator (`INVOICE_IND` initial) or the UBL type code (381). A negated total is not even self-consistent.
7. The **PO number and item on each line come from matching**, never from the page. That is the join between the import half and the export half.

### OpenText VIM: UBL 2.1 with an embedded document

Raw UBL 2.1 (`CustomizationID urn:oasis:names:specification:ubl:xsd:Invoice-2`) with the original file as `cac:AdditionalDocumentReference/cac:Attachment/cbc:EmbeddedDocumentBinaryObject` (`DocumentTypeCode 130`), POSTed as a **multipart form part named `document`, type `text/xml`**, to VIM's inbound ingest through the BTP gateway with an OAuth2 bearer; `sap-client` and `apiKey` as **query parameters**. Measured 2026-06 → 2026-09:

- **The gateway's SQL threat-protection policy scans the whole request** and 403s on any standalone SQL keyword. Three carriers, each hit once: `drop`/`/or+` inside the **base64 PDF**; `" or "` in an **attachment filename**; `or` in an **XML developer comment**. Neutralise base64 by inserting a newline mid-keyword (legal whitespace, byte-identical decode), clean the filename stem, strip comments from the bytes, and preflight-report anything left. Never rewrite invoice data to dodge a WAF.
- **VIM's inbound mapping reads paths**: an element it maps must always be present, so withhold values by shipping the element **empty** — except `xsd:decimal` / `xsd:date` (`cbc:Percent`, `cbc:DueDate`), where empty is schema-invalid and omission is the only option.
- `BuyerReference` = company code (BUKRS) from the *document* (queue prefix / `coa`), so one hook serves every SAP queue. Supplier `PartyIdentification schemeID="SAP-LIFNR"`, customer `schemeID="SAP-BUKRS"`. Payment terms as `cac:PaymentTerms/cbc:Note` `ZTERM=<code>` from the matched PO header; SAP computes the due date itself — do not send a competing one. Header `cbc:Note` carries the Rossum annotation id, AP's traceback.
- `PriceAmount × InvoicedQuantity` must equal `LineExtensionAmount` within 2 cents (EN 16931); derive the price from the authoritative line net when the PO price does not reconcile. `LineExtensionAmount` must equal the sum of emitted lines (BR-CO-10) — flag a gap, never infer a freight line.
- Declare the `mimeCode` from the bytes (magic number first; Rossum's `mime_type` can be the literal string `"empty"`). TIFF is legal under plain UBL 2.1; VIM accepted it.
- Response: `{"id": <guid>, "status": "COMPLETED", "documents": [{"regid": <n>, "status": "REGISTERED"}]}`. **The DP number is not in it.**
- Archive the sent XML as a `document_relation` (type `export`) on the annotation, and **update it in place on re-export** — a relation with documents cannot be deleted and `(type, annotation, key)` is unique; filter the lookup on the annotation *id* server-side.
- Field-level policy (VAT registration, tax code vs rate, bank details by region, one-time-vendor rules) was reversed several times within a month on one implementation. Keep such rules as flags.

Part: `export/export-ubl-to-vim`.

### BAPI-shaped middleware: create, then attach

`CREATE_INVOICE` returns `Invoice Document Num` + `Fiscal Year`; `ATTACH_INVOICE` takes both plus the base64 file and an **external document id** and returns an archive id (`"A" + invoice number + fiscal year + external id zero-padded to 15`) with an already-exists flag. Measured contract details: the header is an **array of exactly one object**; dates `YYYYMMDD`; flags the strings `"X"`/`""`; ids keep leading zeros (`"00010"`); amounts are JSON numbers in the header currency; `Invoice Status` has no default; a GR reference was **not** required where `GR_BASED_INVOICE` was blank even with a GR flag set; value-based service lines carry quantity = amount, so no quantity × price check applies to them.

**The external document id must be stable per logical file** — derive it from the annotation id — so a re-attach is recognised (`Exist Ind "X"`, same archive id) instead of archived twice. Verified live.

Part: `export/export-create-then-attach-with-state`.

### S/4 Cloud `A_SupplierInvoice`

Standard OData: POST the `A_SupplierInvoice` entity with `to_SuplrInvcItemPurOrdRef` items (`x-csrf-token: fetch` on the same call, Basic or OAuth), read `SupplierInvoice` + `FiscalYear` from the `<entry>`, then POST the binary to `API_CV_ATTACHMENT_SRV/AttachmentContentSet` with `BusinessObjectTypeName: BUS2081` and `LinkedSAPObjectKey: <SupplierInvoice><FiscalYear>`. Dedup is SAP's (`SupplierInvoiceIDByInvcgParty + InvoicingParty + CompanyCode`); keep the attachment step at one retry. Build it as one Request Processor hook (`export-create-upload-submit` with `export-oauth-token-cache` if OAuth).

## Operating a sync

- **Schedule feeds staggered** (`*/30`, `15,45`, `5,35`, `0,20,40`, `10,30,50`). MDH has one ingest worker; two loads landing together contend, and a row count read right after a load is a **queue snapshot** — one feed reported complete while the collection was still growing for minutes.
- **Runtime budget below the kill**: 40 s under the 60 s serverless cap; a **manual invoke is capped at ~30 s** and a run that exceeds it completes server-side but loses its report. Page size and per-run page limits are the levers; a `RuntimeError` at budget with nothing written is the correct outcome for a replace.
- **The state record is a checkpoint, never a lock.** MDH writes are asynchronous; two invocations seconds apart both read "no state" and both run a full load. Idempotent, but never schedule below the ingest settle time (~1 min), and disable the cron before a hand-driven backfill if a clean progress log matters.
- **`payload_logging_enabled: false`** on every hook whose payload carries `secrets`; redact credential *values* (bearer, API keys, and their percent-encoded forms) before printing any request dump. A `secrets_schema` must stay an open string map if an engine writes tokens back.
- **Probe before you build**: a `{"selftest": true}` manual invocation that exercises the documented behaviours read-only (string vs integer params, one boundary alone, negative skip, an anchored query's latency) answered ten open contract questions in one run on Shape B and caught the `"0"`-vs-`""` 500 and the 12 s GR latency before a schedule depended on them.
- **Egress**: get the four Rossum egress addresses of the region whitelisted; verify only through the deployed hook.

## Rossum-SAP Implementation Examples

| Example | Integration Pattern | Master Data | Export |
|----------|-------------------|-------------|--------|
| **Customer A** | HTTPS XML API via CPI | Synced by customer via MDH API | Rossum pushes sales order to CPI |
| **Customer B** | SFTP-based | Pulled from same SFTP | IDOCs generated and placed on SFTP |
| **Customer C** | Hybrid | Scheduled Imports extension | Custom serverless function calling VIM Invoice API |
| **Customer D** | SFTP-based | Pulled from same SFTP | IDOCs generated and placed on SFTP |
| **Customer E** | Azure API Manager | Customer's Azure middleware calls MDH API | Rossum generates IDOC XML, pushes via Azure APIM |
| **Customer F** (Shape A) | OData v4 via BTP API Management, OAuth2 | Rossum function hooks: PO headers, PO items (`$expand`), account assignments, one-time-vendor addresses — two-phase watermark syncs | UBL 2.1 + embedded PDF to OpenText VIM ingest via the gateway; second target (Coupa) routed by PO series |
| **Customer G** (Shape B) | Customer REST middleware over Z-RFCs, token endpoint | Rossum function hooks: vendors, PO items (3.7 M, resumable), goods receipts (day-partitioned) — two-phase watermark syncs | `CREATE_INVOICE` (parked) then `ATTACH_INVOICE`, state in Data Storage as duplicate guard |
| **Rossum SAP demo** (Shape C) | S/4 OData via a CPI passthrough iFlow (`_url`/`_query` headers), Basic | Manual function hooks: business partners, PO items, cost centres, GL accounts, tax codes | Templated `A_SupplierInvoice` JSON via REST API Export, XML reply parsed, `BUS2081` attachment |

## Parts

| part | shape | what it is |
|---|---|---|
| `master-data/mdh-import-watermark-sync` | B (any paged HTTPS API) | the two-phase watermark engine; adapt one class to the API dialect |
| `master-data/mdh-odata-import-skiptoken-sync` | A | the same engine with the OData-via-gateway dialect built in, incl. `$expand` flattening |
| `master-data/mdh-odata-filtered-full-refresh` | A | filtered population, PUT-replace each run; the deletion-propagating companion |
| `export/export-ubl-to-vim` | A / VIM | UBL 2.1 + embedded document → VIM, with the gateway defences |
| `export/export-create-then-attach-with-state` | B | two-call BAPI-shaped export with the duplicate guard |
| `export/export-create-upload-submit`, `export/export-oauth-token-cache` | C | Request Processor shape for standard OData writes |

All new parts are `candidate` maturity: lifted from production code that runs, generalized, and not yet run in their generalized form. Read `parts-index` before composing.
