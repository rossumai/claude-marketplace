# Coupa Integration Baseline (CIB) SoW — Approved Wording

Use this file instead of [template.md](template.md) whenever the target system is Coupa. The wording
below has been reviewed and approved; it is the clause library, not inspiration. **Copy clauses
verbatim** and change only the `[bracketed parameters]` and the sentences a clause's note says may be
adapted. A reworded clause has to go through review again; a verbatim one does not.

For the CIB solution itself (queues, schemas, matching, export), see the `coupa-baseline-reference`
skill — use it to check that a deliverable's adaptation is buildable on the baseline.

## Document structure

Exactly these parts, in this order. There is no Delivery Plan or Customer Cooperation section in a
CIB SoW — release dates go into *Testing Support, Documentation, Production Release*, and the few
Customer prerequisites the approved wording carries stay inside their clause (see *Data Export to
Coupa* and *Single Sign-On*). Everything else the Customer must provide goes into the Open decisions
report, not the SoW.

1. **CIB preamble** — verbatim, always:

   > The deliverables and services set out in this SoW are based on the standard feature set and functionalities of the Coupa Integration Baseline ("CIB"), as documented at: https://knowledge-base.rossum.ai/docs/cib-configuration-documentation ("CIB Documentation"). This SoW only covers the provision of the standardized CIB-related adjustments and/or configurations as listed below ("CIB Package"); ie not all services or deliverables in the CIB Package need to be utilized or required). Any request for features, functionalities, services or deliverables beyond the scope of the CIB Package, including any customization or configuration, shall be considered out-of-scope services. All out-of-scope services must be formally requested by Customer and are subject to additional scoping and mutually agreed-upon statement of work or change order, which will incur additional Fees.

2. **Project Goal** — one paragraph: what Coupa will implement, for which markets / legal entities,
   which capture solution it replaces (if any), which document types and channels are in scope, and
   the Coupa instance(s) exported to. If Customer will roll out further markets itself, say so and
   point at *Enablement and Knowledge Transfer*. Approved examples:

   > Coupa will implement the Rossum AP document capture solution, based on the Coupa Integration Baseline (CIB), for [N] Wave 1 markets — [Country], [Country], … — replacing Customer's current [capture solution]. In scope are PO-backed invoices, non-PO invoices and credit notes received as PDF by email or manual upload, and structured e-invoices for [Countries], all exported to Customer's single Coupa instance.

   > The initial deployment covers Customer's [Country] operations: [N] legal entities operating one Coupa instance (TEST and PROD), processing indirect-procurement invoices for export to Coupa. Coupa will deliver the [Country] configuration. Further countries and legal entities will be deployed by Customer using the Enablement and Knowledge Transfer described below.

3. **Deliverables** — a two-column table, header `Service / Deliverable` | `Description, Technical
   and Functional Specification`. In the markdown output write each row as `### [Deliverable name]`
   followed by its description, so it pastes cleanly into the Word table.
4. **Out of Scope** — introduced by "The following items will not be covered by this SoW:" then a
   bullet list.

## Writing rules for CIB SoWs

These override the generic rules in SKILL.md where they conflict.

- The delivering party is **Coupa**: "Coupa will configure…". Never "Rossum will…". The product is
  still named where the approved wording names it ("the Rossum AP document capture solution",
  "Rossum's standard administrator training courses").
- **"Coupa's Master Data Hub"** — not "Rossum's Master Data Hub", never "MDH" or "Data Storage".
- The platform is the **"Cloud Based Technology"**; the extraction pipeline is a **Queue**; the field
  set is the **Schema** (capitalised, per [defined-terms.md](defined-terms.md)).
- Every country-specific clause ends with: "Implemented logic will be common to the country's queue,
  applied uniformly to all documents in that country; per-vendor or vendor-specific logic is not in
  scope."
- **Remove, don't contradict.** If a standard deliverable is not sold, delete its clause *and* every
  sentence in other clauses that depends on it. Never keep a deliverable and exclude it again in Out
  of Scope — e.g. keeping *Service Period Parsing* and "PO number + service period dates" while Out of
  Scope lists "Service period parsing, PO line matching by service period dates" is a contradiction
  the Customer will read in their favour. Likewise, drop "(viii) Contracts" from the master data list
  when *Contract Matching* is not in scope, and drop "Tax Code matching" from *Value Recall Mechanism*
  when tax coding stays in Coupa.
- Keep the numbering of enumerations consistent after removing an item: (i), (ii), (iii)… with no
  repeats or gaps.
- Each clause appears once. Do not repeat the user-accounts sentence or the onboarding-materials
  sentence in several clauses — keep the first under *Solution Configuration and Setup* and the
  second under *Enablement and Knowledge Transfer* (or *Data Export to Coupa* when there is no
  enablement deliverable).
- Quantify every add-on: queues, inboxes, templates, rules per market, training sessions,
  participants, releases. The approved add-on clauses below show the pattern.

## Standard CIB deliverables (always included unless explicitly removed)

Order as listed. Clause text is approved; adapt only where the note says so.

### Solution Configuration and Setup

Coupa will configure user accounts and their respective authorisation privileges. Coupa will configure queues and workspaces based on document type and regional settings. Coupa will set up the ingestion of new documents through one or more of the following options - email, direct upload in the UI of the Cloud Based Technology, SFTP, S3, etc.

Coupa will set up the Aurora learning, and will provide an AI performance report at the end of onboarding.

*Note:* when the queue layout is known, replace the generic second and third sentences with the
concrete layout — approved examples:

> Coupa will configure [N] Queues: one per Wave 1 market ([Countries]), each in its own workspace, plus one E-invoicing Inbox. Coupa will configure user accounts and authorisation privileges so that users see only the queues of their market. Coupa will set up the ingestion of new documents through email (one inbox per market queue) and direct upload in the UI of the Cloud Based Technology. The original email and its attachments will be retained alongside the document. Structured e-invoices will arrive in the "E-invoicing Inbox" queue and will be routed automatically to the [Country], [Country] or [Country] queue based on the buyer's country.

> Coupa will provision a sandbox and a production environment of the Cloud Based Technology, hosted in Rossum's [US/EU] data region. Coupa will configure user accounts and their respective authorisation privileges for [user group]. Coupa will configure one workspace with one Inbox Queue and [N] processing Queues (one per [legal entity]), all based on the [Line/Header] Level taxation Schema. Coupa will set up the ingestion of new documents through email and direct upload in the UI of the Cloud Based Technology. Coupa will connect the Cloud Based Technology to Customer's Coupa TEST instance for implementation and UAT, and to Coupa PROD for production release.

### Incoming Documents Filtering

Coupa will configure rules to filter incoming email attachments based on size, file name, or MIME type.

### Duplicates Handling

Coupa will configure duplicate document detection (i) within the Cloud Based Technology and (ii) also for documents that already exist in Coupa. The detection rules (the definition of a duplicate) will be configured as agreed upon with Customer (default is Invoice Number and Supplier). Duplicate detection is limited to one rule each for internal Cloud Based Technology detection and Coupa integration detection.

### Schema Adjustments

Coupa will configure adjustments to the standard CIB Schema(s) to accommodate specific Customer requirements. This scope is strictly limited to the addition of (i) up to 5 captured fields (data extracted directly from the document), and (ii) up to 5 computed or logic fields (eg formulas, reasoning, or constants used to complement the extraction logic).

### Due Date and Payment Terms

Coupa will configure the extraction and handling logic for payment terms and due dates, adapting the configuration based on Customer's preference. The configuration options for the payment terms logic are limited to one of the following three options: (i) follow the document (Use the payment terms explicitly stated on the document), (ii) follow the supplier default (Use the default payment terms configured for the supplier), and (iii) follow the PO (Use the payment terms specified on the associated Purchase Order). The payment term defined in Coupa will be matched based on the information on the document and exported to Coupa.

### Import of Master Data from Coupa

Coupa will configure regular automated replication of master data to Coupa's Master Data Hub for the following datasets: (i) Chart of Accounts (Account Types), (ii) Suppliers, (iii) Payment Terms, (iv) Tax Codes, (v) Tax Registrations, (vi) Units of Measure, (vii) Purchase Orders (including Line Items), (viii) Lookup Values, and (ix) Contracts.

*Note:* drop Contracts without *Contract Matching*. Add datasets an add-on needs, each with the
reason — e.g. "Users (required for Requester Resolution)", "Invoices including their approval and
payment status (required for Supplier Statement Reconciliation)". When Coupa is the only source, end
with: "Coupa is the only master data source; no other system will be integrated."

### Chart of Accounts (Account Types) Matching

Coupa will configure data matching of customer entities (subsidiaries) based on the following rules and in the following order (i) Match by VAT number (exact) - or any other unique identifier available on the document and in the master data, (ii) Match by Entity Name and Address (fuzzy match with boosting the importance of the Name), and (iii) Match based only on the Entity Name when Address is not present.

If multiple matches are found, the user's selection will be recalled automatically for future automation. If no match is found, the system will flag an error, requiring the user to manually enter the subsidiary identifier.

*Note:* may be limited to named entities — "…of customer entities (subsidiaries), limited to the [Country] legal entities, based on…".

### Supplier Matching

Coupa will configure data matching of suppliers based on the following rules and in the following order (i) Match by VAT number (exact) - or any other unique identifier available on the document and in the master data, (ii) Match by Supplier Name and Address (fuzzy match with boosting the importance of the Name), and (iii) Match based only on the Supplier Name when Address is not present.

If multiple matches are found, the user's selection will be recalled automatically for future automation. If no match is found, the system will flag an error, requiring the user to manually enter the supplier identifier.

### PO/PO Lines Matching

Coupa will configure data matching of PO lines based on the following rules and in the following order (i) Match by PO number + service period dates, (ii) Match by PO number + description (fuzzy match), and (iii) Show all active lines of the PO when nothing is found based on the previous query.

*Note:* without *Service Period Parsing*, drop rule (i) and renumber. When POs appear on lines, add:
"The PO number will be captured either at the header level or on individual invoice lines; where a line carries its own PO number, that number will be used for matching the respective line, otherwise the header PO number will be used."

### Contract Matching

Coupa will configure data matching of Contracts based on the contract number extracted from the document. The contract will then be linked to the invoice during the export.

### Tax Coding

Coupa will implement logic for assigning tax codes based on the extracted tax rate and invoice receiver country. The logic will also employ the recall mechanism for automation of ambiguous cases.

*Alternative* when tax codes stay in Coupa (typical for Header Level taxation) — replace with:

> **Tax Handling (Header Level Taxation)** — Coupa will configure tax handling using the Header Level taxation model. The Cloud Based Technology will capture tax amounts at the header level, including documents carrying more than one tax (eg [tax types]), and will export them to Coupa as header-level tax lines, either one line per tax type or one combined line, as confirmed against Customer's Coupa tax configuration during the design sessions. Tax code determination will remain in Coupa; no tax code matching will be configured in the Cloud Based Technology. The tax registration identifiers required in the Coupa payload will be populated per legal entity as agreed with Customer during the design sessions.

### Value Recall Mechanism

Coupa will configure recall logic that remembers previous user selections in ambiguous matching scenarios (specifically Supplier, Chart of Accounts and Tax Code matching) and automatically re-applies them to new documents with the same context.

*Note:* the list follows the matching actually in scope (e.g. "Supplier, Chart of Accounts and GL Coding segment matching" with GL Coding and no Tax Coding).

### Service Period Parsing

Coupa will prepare parsing logic to identify the start and end date of the service period based on the information extracted from the document. The start and end date values will be mapped to Coupa fields and exported along with the document data. The service period dates will also be utilized for the PO line matching logic.

### Additional Charges

Coupa will implement functionality to handle additional charges (treating all detected charges as shipping) including aggregation of the charges to satisfy Coupa requirements.

### Calculations

Coupa will modify existing CIB calculations to support data extracted from documents of diverse structures and varying values.

The modified calculations will be unified, to the extent possible, across [regions / markets / legal entities]. The deliverable covers [2 variations of the Schema (Line and Header level taxation) | one variation of the Schema ([Line/Header] Level taxation)] and calculations logic related to [their differences | it].

### Business Rules

Coupa will configure the capture and mapping of new custom fields beyond the standard CIB-exported fields. Coupa will implement business rules to validate the extracted data - credit notes handling, invoice line types quantity vs. amount based, invoice data vs. PO data checks (quantity, unit price, supplier, entity, etc.). The business rules, to the extent possible, will be aligned across [regions / markets]. The deliverable covers [2 variations of the Schema (Line and Header Level taxation) | 1 variation of the Schema ([Line/Header] Level taxation)] and business logic related to [their differences | it].

*Note:* approved extensions — add the checks actually sold, e.g. "existence and status of the referenced PO", "validation of tax rates and tax amounts against the invoice totals, and reconciliation of invoice lines against header totals and tax amounts"; or a counted carry-over: "This includes up to [5] market-specific validation rules per Wave 1 market (up to [20] in total) carried over from Customer's existing [capture solution] rule set."

### Credit Notes Handling

Coupa will configure the AP workflow to detect credit notes and apply appropriate business logic to handle them separately from standard invoices.

*Note:* may add "…including capture of the original invoice number and original invoice date, which will be exported to Coupa."

### Data Export to Coupa

Coupa will configure mapping and export of the validated and enriched data to Coupa. The documents that are fully prepared in the Cloud Based Technology will be submitted to Coupa. The conditions for the submitting will be dynamically evaluated on each document.

*Note:* name Customer-specific Coupa fields when agreed, e.g. "The export mapping will include the Customer-specific Coupa invoice fields agreed in the design sessions, within the field budget defined in Schema Adjustments: [field] and [field], [how derived]. These fields must exist in Customer's Coupa TEST and PROD instances before the export is configured." Without an *Enablement and Knowledge Transfer* deliverable, end with: "Coupa will provide onboarding materials (i) online materials - Help Center, API Reference, and Developer Documentation, and (ii) offline materials - decks and handbooks."

### Testing Support, Documentation, Production Release

Coupa will provide necessary support during testing, production release and hypercare period (ie for a period up to 2 weeks following delivery). Coupa will deliver documentation of the solution.

*Note:* with several releases: "(ie for a period up to 2 weeks following each release). The solution will be released to production in [N] releases: (i) [scope], (ii) [scope], and (iii) [scope], aligned with Customer's deployment plan ([month year], [month year] and [month year] respectively)."

### Project Management

Coupa will deliver the following activities (i) kick-off meeting, (ii) workflow design, (iii) user enablement, (iv) implementing modifications based on the UAT, (v) weekly status meetings, (vi) monthly status meetings to summarize the progress, and (vii) onboarding business review and handover to post-onboarding team(s).

## Country clauses (one per country in scope)

### Country-Specific Variances: [Country]

Coupa will implement the country-specific variances required for [Country adjective] invoices. This includes local VAT handling, [reverse-charge treatment — ONLY WHEN SCOPED,] and capture and validation of the [local tax identifier, e.g. UK VAT registration number / Belgian VAT / enterprise number / Polish NIP / Italian Partita IVA / Codice Fiscale], plus additional [Country]-specific fields and rules [identified in the design sessions | limited to those documented in Customer's existing [capture solution] configuration for [Country]]. [[Country adjective] e-invoicing is covered by a separate deliverable.] Implemented logic will be common to the country's queue, applied uniformly to all documents in that country; per-vendor or vendor-specific logic is not in scope.

*Note:* prefer "limited to those documented in Customer's existing … configuration" over "identified in the design sessions" whenever a source rule set exists — it caps the scope. Multi-tax countries name the taxes and the model, e.g. "handling of federal and provincial taxes (GST, HST, PST and QST) within the Header Level taxation model"; multilingual countries add "processing of [language]- and [language]-language invoices".

### Country-Specific E-Invoicing: [Country]

Coupa will enable processing of the applicable [Country] structured e-invoice format(s) ([format, e.g. UBL 2.1 / Peppol BIS 3.0, KSeF FA(3), FatturaPA, Factur-X]). Coupa will ingest and extract the e-invoice content and map it to the shared Schema and the Coupa export. The e-invoices are [received over the Peppol network | retrieved from the [national system, e.g. Polish National e-Invoicing System (KSeF) / Italian Exchange System (Sistema di Interscambio, SDI) / country's government portal]] by Coupa's e-invoicing solution where supported, or will be facilitated by one of Coupa's marketplace partners. Implemented logic will be common to the country's queue, applied uniformly to all documents in that country; per-vendor or vendor-specific logic is not in scope.

## E-invoicing clauses (only if e-invoicing is in scope)

### Ingestion of e-invoices on behalf of Customer

Ingestion of e-invoices configured using the applicable e-invoicing standards for the following VAT IDs and protocols (ingestion methods):

[Country]: [VAT IDs]; [Protocols]

*Note:* include when Customer's VAT IDs and protocols are known; otherwise list them as an open decision.

### SFTP E-Invoice Ingestion

Coupa will configure scheduled pickup of structured e-invoice XML files for [Countries] from one SFTP location provided by Customer or its e-invoicing provider. Picked-up files will be imported into the E-invoicing Inbox and moved to an archive folder on the SFTP server. The pickup will run on a fixed interval and apply uniformly to all files.

### Mapping of e-invoice data to the Schema

Mapping of data from e-invoices to the Schema configured in the Cloud Based Technology. The e-invoicing values available for mapping are limited to the fields listed at the following link; https://rossum.ai/e-invoicing/supported-fields (as may be updated from time to time).

## Add-on clauses (only when sold)

### GL Coding

Coupa will replicate the structure of the [N] billing segments defined in Coupa. There will be fields created for each segment on the invoice lines to make it possible to define the billing string in the Cloud Based Technology[, for both PO-backed and non-PO invoices].

Coupa will implement logic for matching billing segments based on the extracted invoice line description. The logic will also employ the recall mechanism to associate user selected billing segments with the line descriptions, supplier and entity for future automation.

*Approved variant* for PO + non-PO: "For PO-backed lines, the billing segments will be pre-filled from the account coding of the matched PO line. For non-PO lines, the user will select the segment values replicated from Coupa (Lookup Values), and the recall mechanism will store the selection against the line description, supplier and entity and re-apply it to future lines with the same context."

### Document Splitting

Coupa will configure multi-invoice splitting so that a single PDF containing several invoices produces one document per invoice. The splitting rules must be within the capabilities of the Document Splitting extension and will be identical across all [N] country queues.

### Invoice and Attachment Identification and Splitting

*Only when the splitting and classification engine is sold.*

Coupa will deploy its splitting and sorting engine to identify the invoice within each incoming submission and separate it from accompanying attachments. Two scenarios are covered: (1) Single Merged PDF - where the invoice and its attachments arrive as a single PDF, the engine will identify which pages belong to an invoice and which to an attachment. The document will then be automatically split; the part identified as the invoice will be processed, and the other parts will be added as attachments via document relations. (2) Multiple Email Attachments - the file containing the invoice will be identified using the splitting and sorting engine and then extracted. The remaining files will be added as attachments to the invoice via document relations. In both scenarios, the non-invoice content is added as an attachment to the invoice and exported to Coupa together with it, and the full submission remains viewable in Coupa. Coupa will configure one identification-and-splitting approach that handles both scenarios uniformly.

### Document Relation Creation

*Only when the splitting and classification engine is sold.*

Coupa will implement an Extension that, after invoice and attachment identification and splitting, automatically creates document relations between the classified parts of each submission. Each part classified as a non-invoice document is linked to the identified invoice as its attachment. One relation set is created per submission, covering both splitting and classification scenarios (single merged PDF and multiple email attachments). The created relations keep the attachments associated with the invoice throughout processing, keep the full submission viewable from the invoice in Coupa, and are used by the export to deliver the attachments to Coupa together with the invoice.

### Document Sorting by Legal Entity

Coupa will configure one Inbox Queue receiving all [Country] documents and automatic sorting of each document to the processing Queue of the legal entity identified by the Chart of Accounts (Account Types) Matching. Documents whose legal entity cannot be identified will remain in the Inbox Queue for manual assignment. One sorting rule set covering the [N] [Country] legal entities will be configured.

### Requester Resolution

Coupa will configure requester resolution for non-PO invoices with the following strategy:

1. Exact match of the requester name or email captured on the invoice against Coupa users
2. Fallback to the requester most recently confirmed for the same supplier on that country queue
3. Otherwise left empty and flagged to the user

*Alternative* (lookup-table driven) — **Requester Assignment**: "Coupa will configure the population of the requester on invoices exported to Coupa. The requester will be determined from one lookup table provided by Customer as a spreadsheet, loaded by Coupa into the Master Data Hub and maintained by Customer thereafter, with a static default requester per legal entity applied when no lookup value is found. The requester will be exported to Coupa as an email address. One lookup table and one set of lookup rules will be configured."

### Supplier Rejection and Notification

Coupa will configure document rejection with an email notification back to the sender for [N] scenarios:

1. Duplicate invoice
2. Invoice dated before the country's backdating cut-off (one cut-off value per country)
3. Document that is not an invoice or credit note (for example a change of bank details)
4. Invoice from a supplier that is required to invoice through the Coupa Supplier Portal

The notification text will vary by the supplier's Coupa Supplier Portal status (registered / not registered). Coupa will configure up to [8] email templates. For each scenario and country, rejection will be configurable as either automatic or proposed to a user for confirmation; all scenarios will start in user-confirmation mode at go-live.

### Supplier Statement Setup

Coupa will configure one supplier statement Queue per [market] ([N] queues), with the same user access as the market's invoice queue. Coupa will configure extraction of the statement header (supplier, statement date, currency, closing balance) and statement lines (document number, date, amount). Implemented logic will be common to all markets; per-vendor statement layouts are not in scope.

### Supplier Statement Matching

Coupa will configure matching of each statement line against Coupa invoices by supplier, invoice number and amount. Each line will be classified as one of: paid, open, in approval, not found in Coupa, or amount mismatch.

*Requires* "Invoices including their approval and payment status" in *Import of Master Data from Coupa*.

### Supplier Statement Reconciliation Summary

Coupa will configure one reconciliation summary email template listing the classified statement lines, sent to the supplier after user confirmation.

### Single Sign-On

Coupa will configure Single Sign-on for the Cloud Based Technology with one Customer identity provider using [OIDC | SAML], covering the sandbox and production environments. Customer will provide the identity provider metadata and a test user.

### Enablement and Knowledge Transfer

Coupa will deliver knowledge transfer so that Customer's internal team can roll out further [markets / countries and legal entities] and administer the solution after [Wave 1 | the go-live]:

- Access to Rossum's standard administrator [and technical] training courses for up to [6] Customer participants
- [6] [remote | onsite] knowledge-transfer sessions ([up to 2 hours each | N days]) covering queue and Schema changes, Master Data Hub datasets and matching, business rules, [GL Coding,] export to Coupa, user administration and adding a new country queue
- Customer participation in the configuration of [one Queue], performed jointly with Coupa as a supervised exercise
- [2] end-user training sessions per [market / legal entity] ([N] in total), remote, in English
- Onboarding materials: (i) online materials — Help Center, [Coupa University,] API Reference, and Developer Documentation, and (ii) offline materials — decks and handbooks

## Out of Scope library

Always include the first five (CIB defaults); then add the conditional lines that match the scope.
Never exclude something a deliverable above still promises.

Always:

- Splitting and grouping of lines in the Cloud Based Technology UI (eg to match lines on the corresponding PO).
- Approval workflows in the Cloud Based Technology.
- Cloud Based Technology UI customisation outside the built-in platform capabilities.
- The creation of complex algorithms, multi-field conditional logic, or business rules that depend on the inter-relationships of multiple, disparate data fields.
- The introduction, integration, or lookup of data from additional external datasets or third-party systems and services that are not part of the standard CIB workflow (eg bank verification via API, [ERP] master data).

Conditional:

- Without GL Coding: "Assignment of GL, cost center, and similar types of codes (unless covered by a separate GL coding package)." With it: "…beyond the GL Coding deliverable ([N] billing segments on invoice lines)."
- Tax coding stays in Coupa: "Tax code determination or tax code matching in the Cloud Based Technology; tax coding remains in Coupa."
- One taxation variant only: "The CIB [Header/Line] Level taxation Schema variation and its calculations and business rules."
- Service period not sold: "Service period parsing, PO line matching by service period dates" — and remove them from the deliverables.
- Contracts not sold: "Contract matching" — and remove Contracts from the master data list.
- Other targets: "Submission of invoices to [ERP] or any other system than Coupa[, including direct-material invoices], unless covered by a separate statement of work." / "Integration with any system other than Coupa[, including …]."
- Coupa side: "Coupa-side configuration, including approval workflows, invoice routing to PO owners or requesters inside Coupa, booking/release holds, remit-to and bank account validation, and the Coupa Supplier Portal." / "Changes to Customer's Coupa configuration (custom fields, lookup values, approval chains, …) and to Customer's existing invoice capture solutions, including their decommissioning."
- Geography: "Deployment, configuration review or support for countries and legal entities other than [those] listed in this SoW."
- No e-invoicing: "Ingestion, mapping or routing of structured e-invoices in the Cloud Based Technology."
- With e-invoicing: "E-invoicing compliance validation against government mandates." / "E-invoicing for any country other than [Countries]." / "Ingestion of e-invoices from sources not explicitly listed." / "Mapping of values from e-invoices not explicitly listed among the supported fields listed at the following link; https://rossum.ai/e-invoicing/supported-fields (as may be updated from time to time)."
- Any e-invoicing posture: "API integration with third-party applications for ingestion of business to business e-invoice files."
- Without the splitting engine: "Classification of documents mixing invoices and credit notes in one file beyond what the Document Splitting extension supports."
- Without SSO: "Single Sign-on (SSO) configuration."
- Replacing a capture solution: "Migration of historical documents from [capture solution]."
- Always worth adding when Customer runs UAT: "Automated regression testing frameworks or test automation tooling." / "Change management, communications and UAT execution, which remain Customer's responsibility."
