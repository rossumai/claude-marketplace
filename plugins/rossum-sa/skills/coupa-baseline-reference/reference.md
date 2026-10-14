# Coupa Integration Baseline (CIB) — Detailed Reference

## 0. Which CIB am I looking at?

Two baselines are in production. **CIB 2.0 is a fresh install only — there is no in-place upgrade path from 1.x**, so an organisation runs one or the other, never a mixture. Establish which one before answering anything about a live org: the queue topology, the export chain and the hook names all differ.

| Signal | CIB 1.x | CIB 2.0 |
|---|---|---|
| Workspaces / queues | 1 / 2 | 6 / 7 |
| Country queues, e-invoicing inbox | absent | present |
| Rule names | prefixed `TEST - ` | no prefix |
| Rule count | 48 | 53 |
| Master-data import hooks | `Coupa Webhook Import - X (CIB)`, `type: webhook` | `Coupa Master Data Import - X (CIB)`, `type: job` |
| Export chain | 8 webhooks, four of them `… - parse response` | 5 `Export Pipeline - N.` serverless functions |
| Coupa API responses stored in | schema fields (`api1_response_body`, …) | `document_relations` |
| Baseline queue IDs (source org) | 1260377 line, 1260388 header | 2766927 line, 2766928 header |
| Line-level engine fields | 43 | 63 |
| Email templates | 10 | 35 |
| `_cib_deploy_manifest` collection (optional) | `cib_version: v1.x.y` | `cib_version: v2.x.y` |

**In a live org** — the queue count is the fastest signal:

```
rossum_list_queues    # 2 queues -> 1.x;  7 queues incl. "E-invoicing Inbox" -> 2.0
rossum_list_hooks     # "Coupa Webhook Import - …" -> 1.x;  "Coupa Master Data Import - …" -> 2.0
```

**The `_cib_deploy_manifest` Data Storage collection is optional, but when present it is the strongest signal.** Some deployment tooling writes it after the deploy: one document holding `cib_version`, the deploy run id and every object the deploy created. The release and the CIB deploy tool do not create it, so its absence proves nothing. Read it with `data_storage_find` before you infer the baseline from the topology; if it does not exist, use the signals above.

**In a pulled prd2 tree:**

```bash
ls <org>/<dir>/workspaces        # 1 workspace -> 1.x;  6 -> 2.0
ls <org>/<dir>/rules | head -1   # "TEST - …" -> 1.x
```

Throughout this reference, a heading or row tagged **[1.x]** or **[2.0]** applies only to that baseline. Untagged content applies to both — the MDH matching cascades, every shared schema formula, and all 48 baseline rules are carried into 2.0 unchanged. What 2.0 *did* change in the schema is listed in section 3.

**Names and IDs**. Every shipped hook and rule name ends in ` (CIB)` — e.g. `MDH - Main (CIB)`, `PO is Closed (CIB)` — except the hook `Coupa E-Invoicing`, the four `Move to XX queue` rules, `KSeF Offline Invoice (PL)` and the inactive 1.x `Export Pipeline - Request Processor`. Search by the full name. Every numeric hook, rule, queue, schema and engine ID in this reference is a **source-org ID** of the release; a deployed organisation gets its own, so use the IDs to recognise the shape, never to address objects.

---

## 1. Architecture Overview

The Rossum–Coupa integration has three layers:

1. **Technical Integration Components** — Coupa master-data import, export pipeline, MDH, formula fields, native Rules
2. **Coupa Integration Baseline (CIB)** — the pre-configured business logic (this reference)
3. **Customized Integration** — customer-specific enhancements on top of CIB

### 1.1 Data Flow

The PDF path is the same in both baselines:

```
Coupa Master Data ──(scheduled import)──> Rossum Data Storage
                                              │
Document arrives (email/upload/API) ──> Rossum AI Extraction
                                              │
                                        Formula Fields (calculations, transformations)
                                              │
                                        Master Data Hub (matching & enrichment)
                                              │
                                        Native Rules (validation & messages)
                                              │
                                        User Review (if needed)
                                              │
                                        Export Pipeline ──> Coupa Invoice/Credit Note
```

**[2.0]** Structured e-invoices enter differently. Coupa — not Rossum — receives them from the Peppol network, a national platform or a supplier portal, and hands them to Rossum for enrichment:

```
Peppol / national platform ──> Coupa ──(upload + invoice_enrichable_document)──> E-invoicing Inbox
                                                                                      │
                                                        Coupa E-Invoicing maps XML -> schema fields
                                                                                      │
                                                        routing rule on source_country (on confirm)
                                                                                      │
                                                                   Country queue (BE / PL / FR / DE)
                                                                                      │
                                                        full CIB logic -> Export Pipeline -> Coupa
```

From the country queue onwards an e-invoice is processed exactly like a PDF. See [8. E-invoicing](#8-e-invoicing-20).

### 1.2 Queue Topology

**[1.x] — one workspace, two queues:**

| Queue | ID | Taxation Model | Typical Region | Key Differences |
|-------|----|---------------|----------------|-----------------|
| **AP Documents - line level taxation** | 1260377 | Tax codes per line item | Europe, APAC | Has `fully_tax_coded` check, per-line tax rates/amounts, charges table with tax per charge |
| **AP Documents - header level taxation** | 1260388 | Single tax at header | US | No tax codes, shipping/handling/misc charges as separate fields, simpler submission logic |

**[2.0] — six workspaces, seven queues:**

| Workspace | Queue | ID | Role |
|---|---|---|---|
| Coupa Integration Baseline | AP Documents - line level taxation | 2766927 | Baseline AP queue, line-level taxation |
| Coupa Integration Baseline | AP Documents - header level taxation | 2766928 | Baseline AP queue, header-level taxation |
| 1. Inbox | E-invoicing Inbox | 2860788 | Triage only — maps e-invoice XML, then routes |
| 2. Belgium | AP Documents - BE | 2860789 | Full AP queue |
| 3. Poland | AP Documents - PL | 2841935 | Full AP queue + KSeF section |
| 4. France | AP Documents - FR | 2868272 | Full AP queue + Peppol party fields |
| 5. Germany | AP Documents - DE | 3013757 | Full AP queue |

Every country queue is a **complete AP processing queue** carrying the whole CIB logic — matching, validation, enrichment, tagging and the export pipeline. PDFs for that country can be sent straight to it. The Inbox is the only queue that is *not* a processing queue: it has no export pipeline and no matching, and documents leave it by routing rather than by export.

These are the source-org IDs of the CIB release. A deployed organisation gets its own — use them to recognise the shape, not to address objects.

### 1.3 Extraction Engines

Both baselines deploy two dedicated, learning-enabled engines. **[1.x]** a 43-field line-level and a 41-field header-level engine, each bound to its one queue. **[2.0]** the two below:

| Engine | ID | Fields | Bound queues |
|---|---|---|---|
| AP Documents - line level taxation | 56992 | 63 | baseline line-level, E-invoicing Inbox, BE, PL, FR, DE — **six queues** |
| AP Documents - header level taxation | 56991 | 41 | baseline header-level — one queue |

The line-level engine is **shared by six queues**, which matters when removing any of them — see [2.4 Never remove](#24-never-remove).

On an engine-bound queue the schema and the engine must agree: every extracted field needs a matching engine field, and the schema field must not carry `rir_field_names`. Adding an extracted field means creating the engine field first, then the schema field. One harmless leftover: `charge_type` carries `rir_field_names` on all six line-engine schemas and has no engine field.

Only **some** fields populated from an e-invoice mapping carry `score_threshold: 0` — 22 of the 54 mapped schema ids on the Inbox schema, 13 of 45 on BE. `document_id`, `amount_total`, `date_issue`, `sender_name`, `iban` and others do not. For the fields that have it, extraction confidence never blocks automation while they remain trainable; the others use the normal threshold.

### 1.4 Document Workflows

- **Non-PO (Draft)**: No PO number — always drafted in Coupa. Custom account coding can enable submission.
- **PO-backed (Submit or Draft)**: PO matched — submitted if all conditions met (tax coded, no mismatches, not credit note, enforce_draft=No).
- **Contract-backed (Submit or Draft)**: Contract matched with default billing — submitted if conditions met.
- **Credit Notes**: Always drafted in Coupa regardless of other conditions.

---

## 2. Scoping the Deployment [2.0]

**CIB 2.0 deploys everything, every time.** The init script has no country or feature switches: a customer who processes no e-invoices, or who operates in only some of BE / PL / FR / DE, still gets all seven queues. Trimming the deployment to what they actually use is a normal part of the handover.

**Do it before any document is uploaded.** Queue deletion is asynchronous with a 24-hour grace window, and an annotation sitting in a queue you are about to delete turns a two-minute cleanup into a migration.

### 2.1 Which profile is this customer?

| Customer | Keep | Remove |
|---|---|---|
| No e-invoicing, one taxation model | the baseline queue for that model | the Inbox, all four country queues, the other baseline queue, all e-invoicing hooks |
| No e-invoicing, both taxation models | both baseline queues | the Inbox, all four country queues, all e-invoicing hooks |
| E-invoicing in some of BE/PL/FR/DE | the Inbox + the country queues they operate in | the country queues they do not, and each one's ingestion configuration |
| E-invoicing in a country CIB does not ship | the Inbox + baseline queues | nothing — this is a build, not a prune: a new country queue, schema, ingestion configuration and routing rule are required |

A customer who receives both e-invoices and PDFs for the same country normally runs both through that country's queue and needs no baseline queue at all.

### 2.2 Removing a country

For each dropped country `XX`, in this order:

1. **Routing rule** — delete `Move to XX queue`. Do this *first*: a routing rule pointing at a deleted queue is a rule that fires and fails.
2. **Ingestion configuration** — in the `Coupa E-Invoicing` hook, delete the configuration(s) whose `source_country` literal is `XX` (see [8.4 Format coverage](#84-format-coverage)). Left in place, the XML still maps, still lands in the Inbox, and then has nowhere to go.
3. **Queue-scoped rules** — remove the queue from the `queues` array of every rule listing it. The 48 baseline rules split 36 / 8 / 4: 36 are bound to all six AP queues, 8 to the line-level queue and the four country queues, and 4 to the header-level queue only (see 6). A country queue therefore appears in 44 of them.
4. **Hook bindings** — remove the queue from the `queues` array of every hook bound to it.
5. **Queue, then workspace** — delete the queue, then the now-empty workspace. `rossum_delete_queue` cascade-deletes the queue's schema and inbox.
6. **Poland only** — also delete hook `Get Barcodes - KSeF PL (CIB)` and rule `KSeF Offline Invoice (PL)`. Both are bound to the PL queue alone and are dead weight without it.

If **all four** countries go, apply 2.3 as well — an Inbox with no country queue to route to is a document trap.

### 2.3 Removing e-invoicing entirely

1. Apply 2.2 for all four countries.
2. Delete any remaining `Move to BE/PL/FR/DE queue` rules.
3. Delete hooks `Coupa E-Invoicing`, `Coupa E-Invoicing Status Sync (CIB)`, `Coupa E-Invoicing Status Sync - Inbox (CIB)` and `Get Barcodes - KSeF PL (CIB)`.
4. Delete the `E-invoicing Inbox` queue, then the `1. Inbox` workspace.
5. On the Coupa side, stop pointing the enrichment integration at Rossum — there is no longer a queue to receive `invoice_enrichable_documents`.

What remains is a 1.x-shaped deployment — the two baseline queues carrying the full AP flow — on the 2.0 export pipeline.

### 2.4 Never remove

| Object | Why |
|---|---|
| Engine `AP Documents - line level taxation` (56992) | Shared by six queues. `rossum_delete_queue` cascade-deletes an engine only when that queue is its **sole** reference, so it survives while any of the six remain — and goes with the last one. Deleting the baseline line queue *and* every country queue takes the engine with them. |
| The 12 `Coupa Master Data Import` hooks | Feed every matching cascade on every queue. Dropping a country changes nothing about the master data. |
| `MDH - Main (CIB)`, `- Payment Terms (CIB)`, `- Tax Codes (CIB)`, `- Coupa Invoice Check (CIB)` | Core matching. All are bound to all six AP queues except `MDH - Tax Codes (CIB)`, which is bound to the line-level queue and the four country queues only. |
| The 5 `Export Pipeline` hooks, `Handle Coupa Responses`, both `Export Mapping` hooks | The export chain. |
| `Duplicate Handling`, `Metadata Propagator`, the three memorization hooks | Cross-queue services. |
| The 48 baseline rules | Only their `queues` bindings change. |

### 2.5 Traps

**Ingestion configurations are ordered and country-specific.** `Coupa E-Invoicing` evaluates its six configurations in order; the first matching `trigger_condition` wins. From v2.0.1 the five UBL and CII configurations test the buyer's country and the KSeF configuration matches on the Polish-only `Faktura` root element, so a document whose buyer country is not one of the four matches **nothing** and stays in the Inbox unmapped. That is deliberate — visible and unmapped beats silently attributed to the wrong country. Drop a country's configuration when you drop its queue, or its invoices will map and then strand. On **v2.0.0** the CII/France configuration is a catch-all; see [8.4](#84-format-coverage) before assuming a dropped country is really gone.

**An orphaned routing rule is worse than a missing one.** Deleting a country queue but leaving `Move to XX queue` gives you a rule that fires on confirmation and tries to move the annotation into a queue that no longer exists.

**Deletion is asynchronous.** Queue deletes return 202 and can take up to 24 hours. Until one completes, dependent engine and schema deletes return 409 / `engine_attached_to_queues_waiting_for_deletion`. Plan the cleanup as a single pass, not a retry loop.

**The status sync outlives individual countries.** `Coupa E-Invoicing Status Sync (CIB)` is bound to all four country queues at once. Remove the queue from its `queues` array; delete the hook only when the last country goes.

### 2.6 Verification

```
rossum_list_queues    # only the intended queues remain
rossum_list_rules     # no rule lists a deleted queue; no Move-to rule without a target
rossum_list_hooks     # no hook still bound to a deleted queue
```

Also read each `Move to XX queue` rule's `actions[].payload.queue` and check the **host**, not only the queue id. In an org deployed by the CIB deploy tool the four routing rules kept the source host `https://cib.rossum.app/...` next to the correct target ids: prd2 v2.20.2, which the tool runs, rewrites the ids but not the host. Rewrite the host by hand after the deploy. The effect on routing was not tested.

An empty `queues` array is **not** by itself a fault: `Coupa Master Data Import - Contracts`, `- Purchase Orders` and `- Purchase Order Lines` ship unbound because they write only to Data Storage.

Finish by confirming the `Coupa E-Invoicing` hook holds exactly one configuration per surviving country, and upload one document to each remaining queue before handover.

---

## 3. Schema Structure

All field IDs below are the `schema_id` values used in formulas, MDH mappings and export templates. The structure is shared by every CIB schema — **[1.x]** the two baseline schemas, **[2.0]** those two plus the four country schemas and the Inbox subset.

**Field types**. Every field that MDH writes is `type: enum` with `ui_configuration.type: data` and empty options; MDH fills the options at run time. The formula flags with fixed values — `epd_detected`, `epd_tag`, `po_backed`, `po_closed`, `backing_document`, the `*_tag` fields, `item_po_closed`, `sf_submit_for_approval`, `rossum_tag`, `line_items_present`, `fully_tax_coded` — are enums with fixed options too. A field created as `string` from these tables breaks MDH. The Type column below has been corrected accordingly.

**[2.0] schema differences at a glance:**

- `coupa_technical_fields` was split into four sections — `export_pipeline`, `submission_control`, `validation_tags` and `header_queue_compat` (see 3.14).
- 12 export-plumbing fields were removed (the 13th item in the diff is the `coupa_technical_fields` section itself) and two added; see 3.14.
- 21 fields that previously existed only on country queues were added to the baseline line-level schema, so the shared line-level export mapping resolves everywhere. Twenty are captured with `score_threshold: 0.0` and no pretrained seed; `type_of_receipt` is a `data` field written from e-invoice XML. They are listed in 3.15.
- The country schemas and the Inbox subset are described in [8.7 Country schemas](#87-country-schemas) and [8.5](#85-the-e-invoicing-inbox-queue).
- `contract_number_normalized` no longer strips non-alphanumeric characters: the MDH contract lookup matches Coupa's `number` exactly, so stripping broke any contract number containing a separator.

### 3.1 General Information Section (`general_info_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `document_type` | enum | Captured | `tax_invoice` or `credit_note` |
| `document_id` | string | Captured | Invoice number (AI-extracted) |
| `document_id_manual` | string | Formula | Cleaned invoice number: strips newlines, truncates to 40 chars |
| `date_issue` | date | Captured | Invoice date |
| `date_issue_manual` | date | Formula | Copy of `date_issue` (allows manual override) |
| `header_description` | string | Captured | Document description |
| `description_export` | string | Formula | Uses PO line description if matched, else `header_description` |
| `code` | string | Captured | Supplier part number |
| `notes_manual` | string | Manual | Notes field |

### 3.2 Payment Terms Section (`payment_terms_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `date_due` | date | Captured | Due date |
| `terms` | string | Captured | Payment terms text |
| `terms_calculated` | number | Formula | Days from issue to due, or parsed from `terms`, or supplier default; hidden, `can_export: false` |
| `payment_terms_match` | enum | MDH | Matched Coupa payment term ID |
| `payment_terms_days_match` | enum | MDH | Days for net payment from matched term |
| `payment_terms_code_match` | enum | MDH | Payment term code from matched term |
| `payment_terms_export` | string | Formula | EPD term if active, else standard payment term |
| `payment_terms_code_export` | string | Formula | EPD code if active, else standard code |

**Payment Terms Formula Logic** (`terms_calculated.py`):
```python
# Priority: 1) days between issue and due date, 2) digits from terms string, 3) supplier default
if is_set(field.date_issue) and is_set(field.date_due):
    terms_calculated = (field.date_due - field.date_issue).days
elif is_set(field.terms) and re.sub(r'\D', '', field.terms) != '':
    terms_calculated = re.sub(r'\D', '', field.terms)
elif is_set(field.sender_payment_days_match):
    terms_calculated = int(field.sender_payment_days_match)
```

### 3.3 Early Payment Discount (EPD) Section (`early_payment_dicount_section` — misspelled in the release)

EPD is **disabled by default** (requires Reasoning Fields / AI credits to enable). `epd_info` and `service_period_dates_parsed` ship as hidden manual string placeholders with no reasoning-field configuration; enabling them means converting them to reasoning fields.

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `epd_info` | string | Manual placeholder (convert to Reasoning) | JSON with `is_epd_offered`, `epd_rate`, `epd_days` |
| `epd_detected` | enum | Formula | `"true"` if EPD found in `epd_info` JSON |
| `epd_rate` | number | Formula | Discount percentage from `epd_info` |
| `epd_days` | number | Formula | Discount days from `epd_info` |
| `epd_amount` | number | Formula | `round((amount_total / 100) * epd_rate)` — a whole number; falls back to `epd_payment_terms_rate_match` |
| `epd_expires` | number | Formula | Days until discount expiry: `(date_issue + epd_days) - today` |
| `epd_payment_terms_match` | enum | MDH | Matched Coupa payment term with matching discount rate + days |
| `epd_payment_terms_code_match` | enum | MDH | EPD payment term code |
| `epd_payment_terms_rate_match` | enum | MDH | EPD discount rate from matched term |
| `epd_payment_terms_days_match` | enum | MDH | EPD discount days from matched term |
| `epd_tag` | enum | Formula | `"discount_terms"` if EPD expires > 0 and match found |

**EPD Export Logic**: If `epd_expires > 0` and a matching Coupa payment term exists, the EPD term is exported instead of the standard payment term. `epd_tag` is set too, but it is internal — the export template does not send it to Coupa.

### 3.4 Customer (Chart of Accounts) Section (`customer_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `recipient_name` | string | Captured | Customer/entity name |
| `recipient_address` | string | Captured | Customer address |
| `recipient_tax_id` | string | Captured | Customer VAT/tax number |
| `recipient_tax_id_normalized` | string | Formula | `re.sub(r'\W+', '', recipient_tax_id)` |
| `recipient_search` | string | Manual | Manual search field for customer lookup |
| `recipient_match` | enum | MDH | Matched account type (entity) ID |
| `recipient_name_match` | enum | MDH | Name of matched account type |
| `recipient_primary_address_match` | enum | MDH | Primary address ID of matched entity |
| `recipient_entity_country_code_match` | enum | MDH | Country code of matched entity |
| `recipient_tax_registration_match` | enum | MDH | Matched tax registration number |
| `recipient_country_code_match` | enum | MDH | Country code from tax registration |
| `recipient_ship_to_match` | enum | MDH | Matched ship-to address |
| `recipient_export` | string | Formula | Contract customer (when `backing_document == "contract"`) > PO line recipient (item level) > PO line recipient (header) > document customer — the same order as `sender_export` |
| `recipient_name_export` | string | Formula | Same priority chain for name |

**Customer Matching MDH Query Cascade** (9 queries in `account_types_test`):
1. Manual search (user types in `recipient_search`)
2. Memorized value (lookup in `_customer_memorization_test` by name+address)
3. Exact match on VAT number (`primary-address.vat-number` regex with optional country prefix)
4. Exact match on entity name (case-insensitive)
5. Fuzzy match on entity name (Atlas Search, maxEdits:1, score normalized, threshold bands 0.95→0.6)
6. Exact match on primary address name
7. Fuzzy match on primary address name (score normalized, threshold > 0.8)
8. Fuzzy match on name + address fields (compound: must `primary-address.name` boost:2, should address fields boost:0.5)
9. Return all entities (fallback when nothing matches)

**Header-level taxation**: no difference. Both header schemas carry `recipient_search`, and `MDH - Main (CIB)` is one hook bound to header and line queues with no `queue_ids` scoping, so the header queue runs the same 9-query cascade, including the manual search and the `_customer_memorization` query. The memorization hooks are bound to both queues as well.

### 3.5 Supplier Section (`vendor_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `sender_name` | string | Captured | Supplier name |
| `sender_address` | string | Captured | Supplier address |
| `sender_tax_id` | string | Captured | Supplier VAT/tax number |
| `sender_tax_id_normalized` | string | Formula | `re.sub(r'\W+', '', sender_tax_id)` |
| `sender_search` | string | Manual | Manual search for supplier lookup |
| `sender_match` | enum | MDH | Matched supplier ID |
| `sender_name_match` | enum | MDH | Supplier name |
| `sender_display_name_match` | enum | MDH | Supplier display name |
| `sender_number_match` | enum | MDH | Supplier number |
| `sender_country_code_match` | enum | MDH | Country code from supplier primary address |
| `sender_payment_days_match` | enum | MDH | Default payment days from supplier |
| `sender_export` | string | Formula | PO line supplier > contract supplier > document supplier |
| `sender_name_export` | string | Formula | Same priority chain for name |

**Supplier Export Priority Logic** (`sender_export.py`):
```python
# Priority: contract supplier > PO line supplier (item level) > PO line supplier (header) > document supplier
if field.backing_document == "contract":
    if not is_empty(field.contract_supplier_match): field.contract_supplier_match
elif any(field.item_po_line_supplier_match.all_values):
    int(get_nonempty(field.item_po_line_supplier_match.all_values))
elif field.po_line_supplier_match:
    int(field.po_line_supplier_match)
else:
    int(field.sender_match)
```

**Supplier Matching MDH Query Cascade** (7 queries in `suppliers_test`):
1. Manual search (user types in `sender_search`)
2. Memorized value (lookup in `_supplier_memorization_test` by name+address)
3. Exact match on supplier `tax-id` (regex with optional country prefix)
4. Exact match on `primary-address.vat-number` (same regex pattern)
5. Exact match on name (`$toLower` of `name` against `{sender_name}`)
6. Fuzzy match on `name` (Atlas Search, maxEdits:1, score normalized, threshold bands 0.95→0.6)

No query matches on `display-name`; it is only projected into `sender_display_name_match`.
7. Fuzzy match on name + address fields (compound query)

**Header-level taxation**: no difference. Both header schemas carry `sender_search`, and the header queue runs the same 7-query cascade, including the manual search and the `_supplier_memorization` query.

### 3.6 Bank Details Section (`payment_info_section`)

| Field ID | Type | Purpose |
|----------|------|---------|
| `iban` | string | IBAN |
| `bic` | string | BIC/SWIFT |
| `account_num` | string | Account number |
| `bank_num` | string | Bank code |

**Note**: CIB does NOT implement bank detail matching or Remit-To logic. This varies by customer and is always a custom implementation. The header-level schemas do capture a `sender_remit_to_address` field, which no baseline logic reads.

### 3.7 Credit Note Section (`credit_note_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `original_document_id` | string | Captured | Original invoice number for credit note |
| `original_date_issue` | date | Captured | Original invoice date |
| `credit_notes_amounts` | string | Manual, hidden | `default_value: "negative"`, no options, not captured — users never see it. Sets the sign **Coupa receives** for a credit note, not the sign on the document (see 13) |

**Credit Note Sign Logic**: The `credit_notes_amounts` field sets the sign convention of the exported credit note. The `set_sign` / `set_value_sign_line_items` helper function in amount formulas adjusts values based on:
- Document type (invoice vs credit note)
- Whether header total is positive
- Whether all line values are positive or negative
- The `credit_notes_amounts` setting

### 3.8 Taxes & Amounts Section (`amounts_section`)

#### Line Level Taxation

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `amount_total` | number | Captured | Total amount (gross) |
| `amount_total_base` | number | Captured | Subtotal (net) |
| `amount_total_base_calculated` | number | Formula | Net: `amount_total_base` or `amount_total - tax` |
| `amount_total_tax` | number | Captured | Tax amount |
| `amount_total_tax_calculated` | number | Formula | Tax: from captured, or `total - base`, or `base * rate/100` |
| `tax_rate` | number | Captured | Tax rate % |
| `tax_rate_calculated` | number | Formula | Rate: from captured, or `tax/base * 100` |
| `tax_code_match` | enum | MDH | Matched tax code (header level, for no-line-items case) |
| `currency` | enum | Captured | Currency code — 58 lower-case options, required; exported directly. There is no `currency_upper` field |
| `coupa_total_calculated` | number | Formula | Coupa's backward calculation check |

**Coupa Total Calculation** (line level):
```python
# Mimics how Coupa calculates total from the exported data
if field.line_items_present == 'false':
    total_amount = default_to(field.quantity_export,1) * field.price_export
    taxes = default_to(field.amount_total_tax_calculated,0)
else:
    total_amount = sum(default_to(field.item_net_total_coupa.all_values,0))
    taxes = sum(default_to(field.item_tax_calculated.all_values,0))
taxes += field.charges_total_tax_calculated
charges = field.charges_net_amount_total_calculated
total = total_amount + charges + taxes
```

#### Header Level Taxation — Key Differences

| Difference | Line Level | Header Level |
|-----------|-----------|--------------|
| Tax amount | Single value | List (`all_values` summed) |
| Tax rate calculation | From captured or derived from base/tax | Derived from `tax_calculated / (total - tax_calculated) * 100` |
| Charges | Charges table with individual tax per charge | Three named fields: `shipping_charge`, `handling_charge`, `misc_charge` |
| Subtotal calculation | `amount_total_base` or `amount_total - tax` | captured `amount_total_base` when present, else `amount_total - charges_calculated - abs(tax)`, then the credit-note sign |
| Tax codes | Per-line + header | None |

### 3.9 Charges

#### Line Level Taxation — Charges Table (multivalue `line_item_charges`, tuple `charge_annotated`)

| Field ID | Type | Source |
|----------|------|--------|
| `charge_type` | enum | Hidden — `InvoiceShippingCharge` / `InvoiceHandlingCharge` / `InvoiceMiscCharge`, filled by the 2.0 e-invoice import. There is no `charge_description` |
| `charge_amount` | number | Captured |
| `charge_tax` | number | Captured |
| `charge_tax_calculated` | number | Formula |
| `charge_tax_rate` | number | Captured |
| `charge_tax_rate_calculated` | number | Formula |

Aggregated fields:
- `charges_net_amount_total_calculated` = `sum(charge_amount.all_values)`
- `charges_total_tax_calculated` = `sum(charge_tax_calculated.all_values)`
- `charges_tax_rate_average_calculated` = average of all charge tax rates

All charges are exported as **Shipping** type in Coupa (with associated tax line).

#### Header Level Taxation — Named Charge Fields

- `shipping_charge` → `shipping_charge_calculated` = `sum(shipping_charge.all_values)`
- `handling_charge` → `handling_charge_calculated` = `sum(handling_charge.all_values)`
- `misc_charge` → `misc_charge_calculated` = `sum(misc_charge.all_values)`
- `charges_calculated` = shipping + handling + misc

The header-level schemas also carry the multivalues `amount_total_tax_ns`, `shipping_charge_ns`, `handling_charge_ns` and `misc_charge_ns`, and have no `tax_rate` field.

### 3.10 Purchase Order Section (`po_section`)

The table groups the PO fields by meaning, not by section. Of the `po_line_*_match` fields only `po_line_currency_match`, `po_line_status_match` and `po_line_uom_match` sit in `po_section`. The others, `po_backed` and `po_closed` sit in `coupa_technical_fields` **[1.x]** or in `header_queue_compat` / `submission_control` **[2.0]** (see 3.14).

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `order_id` | string | Captured | PO number from document |
| `order_id_calculated` | string | Formula | `order_id` or `order_blanket_match` |
| `order_header_match` | enum | MDH | Matched PO header (by `po-number`) |
| `order_header_status_match` | enum | MDH | PO status |
| `order_header_ship_to_addr_match` | enum | MDH | Ship-to address ID from PO |
| `order_blanket_match` | enum | MDH | Blanket PO selection (for POs not on document) |
| `order_item_match` | enum | MDH | Matched PO line (header-level, for no-line-items case) |
| `po_line_number_match` | enum | MDH | PO line number |
| `po_line_type_match` | enum | MDH | `OrderQuantityLine` or `OrderAmountLine` |
| `po_line_status_match` | enum | MDH | PO line status |
| `po_line_uom_match` | enum | MDH | UOM code from PO line |
| `po_line_total_match` | enum | MDH | PO line total |
| `po_line_price_match` | enum | MDH | PO line price |
| `po_line_description_match` | enum | MDH | PO line description |
| `po_line_quantity_match` | enum | MDH | PO line quantity |
| `po_line_recipient_match` | enum | MDH | Customer from PO line account |
| `po_line_supplier_match` | enum | MDH | Supplier from PO line |
| `po_line_currency_match` | enum | MDH | Currency from PO line |
| `po_backed` | enum | Formula | `"true"` if `order_header_match` or any `item_order_header_match` |
| `po_closed` | enum | Formula | `"true"` if PO status in `soft_closed, closed, cancelled` |
| `backing_document` | enum | Formula | `"contract"` if no PO and contract approved, else `"po"` |

**Blanket PO Matching** (config "10. Order Blanket Header"):
- Looks up PO lines where: account-type matches `recipient_match` AND supplier matches `sender_match`
- Joins to `purchase_orders_test` to get PO header
- Filters to POs with status `"issued"`
- Groups by order-header to show unique POs for selection

### 3.11 Contract Section (`contract_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `contract_number` | string | Captured | Contract number from document |
| `contract_number_normalized` | string | Formula | **[1.x]** `re.sub(r'[^a-zA-Z0-9]', '', contract_number)`; **[2.0]** `default_to(field.contract_number, '')` |
| `contract_match` | enum | MDH | Matched contract number |
| `contract_status_match` | enum | MDH | Contract status |
| `contract_supplier_match` | enum | MDH | Supplier ID from contract |
| `contract_supplier_name` | enum | MDH | Supplier name from contract |
| `contract_customer_match` | enum | MDH | Customer (account-type) ID from contract default-account |
| `contract_customer_name` | enum | MDH | Customer name from contract |

**Backing Document Logic** (`backing_document.py`):
```python
# Contract-backed only when: no PO matched AND contract status is "approved"
'contract' if (is_empty(field.order_header_match) and is_empty(field.order_blanket_match)
               and default_to(field.contract_status_match, '') == 'approved') else 'po'
```

### 3.12 Service Period Section

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `service_period` | string | Captured | Raw service period text |
| `service_period_dates_parsed` | string | Manual placeholder (convert to Reasoning) | JSON `{"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD"}` |
| `sp_date_start` / `sp_date_end` | date | Captured | Start/end dates |
| `service_period_start_export` / `service_period_end_export` | date | Formula | Captured `sp_date_*` first, then the parsed JSON. The ISO strings are in `service_period_start_text` / `service_period_end_text` |

Also available at **line item level** with `service_period_item`, `sp_item_start_date_export`, `sp_item_end_date_export`.

PO line service period dates are matched from `period.start-date` and `period.end-date` fields and normalized for comparison.

### 3.13 Line Items Tuple (`line_items`)

Each line item has these fields (showing key ones):

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `item_description` | string | Captured | Line item description |
| `item_description_export` | string | Formula | `item_description` as-is |
| `item_description_calculated` | string | Formula | If `po_closed == "true"`, `item_order_id_calculated` (the PO number). Otherwise `item_po_line_description_match`, but only when the header `po_line_description_match` is set (probably a bug), else `item_description` (LINE LEVEL ONLY) |
| `item_quantity` | number | Captured | Quantity |
| `item_quantity_calculated` | number | Formula | `abs(qty)` or derived from `total/price`, default 1 |
| `item_amount_base` | number | Captured | Unit price (net) |
| `item_amount_base_calculated` | number | Formula | Complex credit note sign handling + fallback calc |
| `item_total_base` | number | Captured | Line total (net) |
| `item_total_base_calculated` | number | Formula | Credit note sign handling, or `price * qty` |
| `item_rate` | number | Captured | Tax rate % (LINE LEVEL ONLY) |
| `item_rate_calculated` | number | Formula | Derived from captured, or `tax/total*100`, or header rate (LINE LEVEL ONLY) |
| `item_tax` | number | Captured | Tax amount (LINE LEVEL ONLY) |
| `item_tax_calculated` | number | Formula | From captured or `total * rate/100` (LINE LEVEL ONLY) |
| `item_tax_code_match` | enum | MDH | Matched tax code for this line (LINE LEVEL ONLY) |
| `item_order_id` | string | Captured | Per-line PO number |
| `item_order_id_calculated` | string | Formula | `item_order_id` or `order_id_calculated` or `order_blanket_match` |
| `item_code` | string | Captured | Supplier part number |
| `item_order_item_match` | enum | MDH | Matched PO line for this invoice line |
| `item_order_header_match` | enum | MDH | Matched PO header for this line's PO number |
| `item_po_line_number_match` | enum | MDH | PO line number |
| `item_po_line_type_match` | enum | MDH | `OrderQuantityLine` or `OrderAmountLine` |
| `item_po_line_uom_match` | enum | MDH | UOM from matched PO line |
| `item_po_line_total_match` | enum | MDH | PO line total |
| `item_po_line_price_match` | enum | MDH | PO line price |
| `item_po_line_description_match` | enum | MDH | PO line description |
| `item_po_line_quantity_match` | enum | MDH | PO line quantity |
| `item_po_line_status_match` | enum | MDH | PO line status |
| `item_po_line_recipient_match` | enum | MDH | Customer from PO line |
| `item_po_line_supplier_match` | enum | MDH | Supplier from PO line |
| `item_po_line_currency_match` | enum | MDH | Currency from PO line |
| `item_po_line_sp_start_date_match` | enum | MDH | PO line service period start |
| `item_po_line_sp_end_date_match` | enum | MDH | PO line service period end |
| `item_line_type` | string | Formula | `InvoiceAmountLine` if PO line is `OrderAmountLine`, else `InvoiceQuantityLine` |
| `item_quantity_export` | number | Formula | Rounded qty if `InvoiceQuantityLine`, else empty |
| `item_price_export` | number | Formula | Unit price for qty lines, total for amount lines |
| `item_uom_export` | string | Formula | PO line UOM or default `"EA"` |
| `item_net_total_coupa` | number | Formula | Coupa backward calc: `qty * price` (or just price for amount lines) |
| `item_order_item_number_export` | string | Formula | PO line number if status allows |
| `item_recipient_mismatch_tag` | enum | Formula | `"recipient_mismatch"` if PO customer != document customer |
| `item_supplier_mismatch_tag` | enum | Formula | `"supplier_mismatch"` if PO supplier != document supplier |
| `item_inactive_po_line_tag` | enum | Formula | `"po_line_inactive"` if PO line status not in active set |
| `item_po_closed` | enum | Formula | `"true"` if item's PO status in closed/cancelled |

### 3.14 Coupa Technical Fields

**[1.x]** one `coupa_technical_fields` section. **[2.0]** the same fields split across four sections:

| Section | Holds |
|---|---|
| `export_pipeline` | `country_code`, `coupa_api_base_url`, `oauth_client_id`, `coupa_invoice_id`, `api{1,2,3,4}_status_code`, `attachments_status_code`, `original_file_name`, `rossum_annotation_link` |
| `submission_control` | `sf_submit_for_approval`, `enforce_draft`, `rossum_tag`, `po_backed`, `fully_tax_coded`, `line_items_present`, `po_closed` |
| `validation_tags` | `recipient_mismatch_tag`, `supplier_mismatch_tag`, `inactive_po_line_tag`, `inv_total_issue_tag`, `enforced_draft_tag`, `charges_tag`, `coupa_invoices_statuses`, `is_duplicate`, `duplicate_invoice_statuses` |
| `header_queue_compat` | `line_level_taxation`, `line_type`, eleven `po_line_*_match` fields (type, number, recipient, recipient_name, supplier, supplier_name, supplier_display_name, description, total, price, quantity — currency, status and uom are in `po_section`), `quantity_header_calculated`, `quantity_export`, `price_export`, `uom_export` |

**Removed in 2.0** — the Request Processor supersedes them: `api1_status`, `api{1,2,3,4}_response_body`, `api{2,3,4}_url`, `api{2,4}_gate`, `oauth_url`, `create_draft_url`. Rows for them below are **[1.x] only**.


| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `sf_submit_for_approval` | enum | Formula | `"Yes"` or `"No"` — dynamic submission decision |
| `enforce_draft` | enum | Manual | `"Yes"` or `"No"` — user can force draft |
| `rossum_tag` | enum | Formula | `"rossum_submit"` or `"rossum_draft"` |
| `enforced_draft_tag` | enum | Formula | `"enforced_draft"` if enforce_draft=Yes |
| `line_level_taxation` | string | Default | Hidden, `default_value: "False"` on **every** schema, line level included. No hook or rule reads or writes it: it is dead and cannot tell the queues apart |
| `line_items_present` | enum | Formula | `"true"` if line_items count > 0 |
| `fully_tax_coded` | enum | Formula | `"true"` if tax_code_match set OR all item tax codes set (LINE LEVEL ONLY) |
| `quantity_header_calculated` | number | Formula | Always `1` (fallback line quantity) |
| `recipient_mismatch_tag` | enum | Formula | Aggregates `item_recipient_mismatch_tag` from all lines |
| `supplier_mismatch_tag` | enum | Formula | Aggregates `item_supplier_mismatch_tag` from all lines |
| `inactive_po_line_tag` | enum | Formula | `"po_line_inactive"` if any PO line inactive |
| `inv_total_issue_tag` | enum | Formula | `"inv_total_issue"` if Coupa total != document total |
| `duplicate_invoice_statuses` | enum | MDH | Coupa invoice statuses for duplicate check |
| `coupa_invoices_statuses` | string | Formula | Unique comma-separated statuses from duplicate check |
| `is_duplicate` | string | Hook (`ui: data`) | Written by `Duplicate Handling (CIB)` |
| `charges_tag` | enum | Hook (`ui: data`) | Tag for charges presence |
| `coupa_invoice_id` | string | Export | Coupa invoice ID from API response |
| `api1_status_code` | string | Export | Create Draft HTTP status |
| `api1_response_body` | string | Export | Create Draft response |
| `api2_status_code` | string | Export | Attach Image Scan HTTP status |
| `api2_response_body` | string | Export | Attach Image response |
| `api2_url` | string | Formula | `{base_url}api/invoices/{coupa_invoice_id}/image_scan` |
| `api2_gate` | string | Formula | Non-empty if `api1_status_code == "201"` |
| `api3_status_code` | string | Export | Attach Rossum URL HTTP status |
| `api3_response_body` | string | Export | Attach URL response |
| `api3_url` | string | Formula | `{base_url}api/invoices/{coupa_invoice_id}/attachments` |
| `api4_status_code` | string | Export | Submit HTTP status |
| `api4_response_body` | string | Export | Submit response |
| `api4_gate` | string | Formula | Non-empty if `api1_status_code == "201"` AND `sf_submit_for_approval == "Yes"` |
| `api4_url` | string | Formula | `{base_url}api/invoices/{coupa_invoice_id}/submit` |
| `coupa_api_base_url` | string | Formula | Coupa instance URL — a hard-coded constant (release default `https://<tenant>.coupacloud.com/`), override per environment |
| `oauth_url` | string | Formula | `{coupa_api_base_url}oauth2/token` |
| `oauth_client_id` | string | Formula | OAuth client ID |
| `create_draft_url` | string | Formula | `{coupa_api_base_url}api/invoices` |
| `original_file_name` | string | Function | Original document filename |
| `rossum_annotation_link` | string | Function | URL back to Rossum annotation |

### Submit Logic

**Line Level** (`sf_submit_for_approval.py`):
```python
if (field.enforce_draft == 'No'
    and field.document_type != 'credit_note'
    and ((field.po_backed == "true" and field.fully_tax_coded == "true")
         or field.backing_document == "contract")):
    "Yes"
else:
    "No"
```

**Header Level** — same but WITHOUT `fully_tax_coded` check:
```python
if (field.enforce_draft == 'No'
    and field.document_type != 'credit_note'
    and (field.po_backed == "true" or field.backing_document == "contract")):
    "Yes"
else:
    "No"
```

### Line Items Optional Behavior

When `line_items_present == "false"`:
- Header-level fields `order_item_match` and `tax_code_match` become visible (native rules `ShowHide - Hide Order Item Match (CIB)` / `ShowHide - Hide Tax Code Match (CIB)`, action `show_hide_field`)
- Export uses header fields (`quantity_export`, `price_export`, `uom_export`, `line_type`) to create a single fallback line
- `quantity_header_calculated` = 1, `price_export` = `amount_total_base_calculated`

### 3.15 Fields not listed above

**[2.0] The 21 fields added to the line-level schema:** `delivery_date`, `requester_email_xml`, `source_country`, `type_of_receipt`, `cash_accounting_scheme_reference`, `payment_order_number`, `payment_order_reference`, `split_payment_mechanism`, `recipient_electronic_address`, `sender_electronic_address`, `recipient_party_identifier`, `sender_party_identifier`, `credit_reason`, `exchange_rate`, `invoice_misc_charge`, `invoice_rounding_amount`, `invoice_shipping_charge`, `item_tax_description`, `item_tax_exempt`, `item_tax_out_of_scope`, `item_tax_reverse_charge`. The 20 captured ones are engine fields with `pre_trained_field_id: null`. `type_of_receipt` sits in `payment_terms_section` on the line-level schema but in `general_info_section` on the Inbox.

**Requestor routing:** `requestor_email` (enum, MDH), `requestor_email_calculated` (formula), `item_requestor_email_match`, `item_order_requestor_match`. They feed `requester-email` in the export (see 7.3).

**Other header fields:** `order_item_number_export`, `po_line_recipient_name_match`, `po_line_supplier_name_match`, `po_line_supplier_display_name_match`, `service_period_start_text` / `service_period_end_text`, `po_line_sp_start_date_match` / `po_line_sp_end_date_match`, `po_line_sp_*_normalized`.

**Other line-item columns:** `item_po_line_split_billing_match`, `item_order_header_status_match`, `item_order_header_ship_to_addr_match`, `item_po_line_recipient_name_match`, `item_po_line_supplier_name_match`, `item_po_line_supplier_display_name_match`, `sp_item_start_date_text` / `sp_item_end_date_text`, `item_po_line_sp_*_normalized`, `service_period_item_start` / `service_period_item_end`, `service_period_item_dates_parsed`.

**Per-environment constants:** `coupa_api_base_url` and `oauth_client_id` are formulas that return hard-coded constants and must be overridden per environment (see 14). `country_code` is `''` on the baseline schemas and `'BE'` / `'PL'` / `'FR'` / `'DE'` on the country schemas; the export sends it as `override-tax-country`.

---

## 4. Data Import — Coupa to Rossum

### 4.1 Import Hooks

Twelve scheduled imports replicate Coupa master data into Rossum Data Storage. Each one:
1. Authenticates via OAuth client credentials
2. Calls a Coupa API endpoint with field selection
3. Stores results in a Rossum Data Storage collection
4. Syncs differentially — only records created or updated since the last run

The datasets, endpoints and schedules below are identical in both baselines. What differs is the extension carrying them:

| | CIB 1.x | CIB 2.0 |
|---|---|---|
| Hook name | `Coupa Webhook Import - X (CIB)` | `Coupa Master Data Import - X (CIB)` |
| `type` | `webhook` to `https://<cluster gateway>/svc/scheduled-imports/api/coupa/v1/import` — the release ships `shared-eu2.rossum.app`; the deploy tool sets the host per region from its `hooks.csv` (`elis.rossum.ai`, `shared-eu2.rossum.app`, `us.app.rossum.ai`), never the org's vanity domain | `job` (Store template 55) |
| Events | `invocation.scheduled`, `invocation.manual` | `invocation.scheduled`, `invocation.manual` |
| Settings root | `settings.credentials` + `settings.import_config` + `settings.job_run_settings` | the same |
| Schedule lives in | `config.schedule.cron` | `config.schedule.cron` |
| Webhook-only config | `timeout_s` 30, `retry_count` 4, `max_polling_time_s` 300, webhook `secret` | — |

The settings are identical between the baselines; only `type`, the webhook `config.url` and retry settings, and `hook_template` (none vs 55) differ.

In both baselines the imports subscribe to `invocation.manual`, so they can be triggered by hand — which is how the deploy seeds the datasets right after a deploy — and `settings.job_run_settings.max_run_time_s` is `36000`.

**[2.0] Running the import jobs** (job hooks in general: `rossum-reference` → Job Extension):

- **Template 55 must be visible.** It is shown only to organization groups with the
  `integrations_team` visibility tag; without it `GET /hook_templates/55` returns 404, the hooks
  cannot be installed, and a prd2 deploy of them stalls on a template picker
  (`prd-reference` → Hooks created from Store templates). Only Rossum can add the tag.
- **Each hook needs a `token_owner`** — without one every run fails at once with `Set the token
  owner of the extension.`
- **Watch runs in the run log**, not the hook log: `rossum_list_hook_logs` with
  `include_run_log=true`, or `GET /hooks/runs?hook=<id>`, then
  `GET /hooks/runs/<uuid>/logs` shows `waiting` → `running`, one `Imported N records` line per
  page, and the failure reason. Expect minutes in `waiting` before a worker picks the job up.
- **A run that ends `completed` with no `Imported N records` line imported nothing.** `${last_modified_date}` resolves to the start of the last *successful* operation for that `dataset_name` in the org's MDH operation log (`GET /svc/master-data-hub/api/v1/operation/?dataset_name=<name>`). The log survives teardown, collection drops and new hook IDs, so a redeploy into the same org resumes from the old watermark against an empty collection and imports 0 records. Repair: set the anchor key to a literal `1970-01-01T00:00:00Z`, invoke once, check the count, then restore `${last_modified_date}` (`coupa-bulk-replication`). This applies to both baselines and to any redeploy into an org that had CIB before, whichever way it was deployed. The deploy tool does not do this repair.
- **Paging:** `records_per_request` is at most 50; `offset` and `limit` cannot appear in `query`
  (the job pages with them itself).
- **Records keep Coupa's field names**: `fields: ["updated_at"]` is stored as `updated-at`. Write
  MDH queries against the stored names.

| # | Dataset | Coupa Endpoint | Schedule | Notes |
|---|---------|---------------|----------|-------|
| 1 | `suppliers_test` | `/api/suppliers` | `0 */2 * * *` (every 2h) | Active/inactive suppliers |
| 2 | `account_types_test` | `/api/account_types` | `5 */2 * * *` | Entities / Chart of Accounts |
| 3 | `purchase_orders_test` | `/api/purchase_orders` | `*/15 * * * *` (every 15m) | PO headers — frequent sync |
| 4 | `purchase_order_lines_test` | `/api/purchase_order_lines` | `*/15 * * * *` (every 15m) | PO lines — frequent sync |
| 5 | `lookup_values_test` | `/api/lookup_values` | `15 */2 * * *` | Billing segments (not used in CIB logic directly) |
| 6 | `tax_codes_test` | `/api/tax_codes` | `10 */2 * * *` | Tax codes with rates/countries |
| 7 | `uoms_test` | `/api/uoms` | `10 2 * * *` (daily 2am) | Units of measure |
| 8 | `tax_registrations_test` | `/api/tax_registrations` | `20 */2 * * *` | Tax registrations per entity |
| 9 | `addresses_test` | `/api/addresses` | `25 */2 * * *` | Ship-to addresses |
| 10 | `payment_terms_test` | `/api/payment_terms` | `10 1 * * *` (daily 1am) | Payment terms |
| 11 | `suppliers_remit_to_addresses_test` | `/api/suppliers` (remit_to) | `25 */2 * * *` | Remit-to addresses (for custom use) |
| 12 | `contracts_test` | `/api/contracts` | `0 */2 * * *` | Contracts |

### 4.2 Import Configuration Pattern

Both baselines use the same `settings` layout. Neither release has a `third_party_service_settings`, `nested_fields` or `auth` block; a hook built in that shape cannot be read by the import service.

```json
{
  "credentials": {
    "base_api_url": "https://<tenant>.coupacloud.com/",
    "client_id": "<coupa-oauth-client-id>",
    "client_scope": "core.supplier.read"
  },
  "import_config": {
    "endpoint": "api/suppliers",
    "dataset_name": "suppliers_test",
    "method": "update",
    "id_keys": ["id"],
    "records_per_request": 50,
    "query": {
      "fields": ["id", "name", "display_name", {"primary_address": ["id", "city"]}],
      "order_by": "id",
      "updated-at[gt_or_eq]": "${last_modified_date}"
    }
  },
  "job_run_settings": { "max_run_time_s": 36000 }
}
```

**Key configuration properties**:
- `query.fields` — fields to replicate. Nested objects are single-key objects with Coupa's snake_case names (`primary_address`, `remit_to_addresses`, `payment_term`). Addresses and Payment Terms ship without `fields` and import whole records.
- `query.order_by` — `id` on all 24 shipped import hooks. The import pages by offset, so the sort key must be unique; `updated_at` has ties that duplicate or drop records.
- `query["updated-at[gt_or_eq]"]` — the delta anchor. Remit To Addresses uses `remit-to-addresses[updated-at][gt_or_eq]` instead; tooling that rewrites the anchor must handle both keys. Leaving the anchor out re-imports everything on every run.
- `method` — the dataset write mode, not an HTTP verb. `update` upserts by `id_keys`; `replace` rewrites the dataset. All 12 shipped hooks use `update`.
- `credentials.client_scope` — the OAuth scope for the endpoint (see 16).

For a project-specific slice the baseline does not ship (a `lookup_values` segment, a child
level, one currency's exchange rates), start from the `master-data/coupa-import-delta` part
(`parts-index`).

#### `dir: desc` in the shipped PO imports

The Purchase Orders and Purchase Order Lines imports ship with `"dir": "desc"` in `query` in both releases. The parts library (`coupa-import-delta`, `coupa-bulk-replication`) says never to add it, because the import service can return only the first page and hang until the run-time kill. On 2026-10-13 full loads with it did finish (purchase_orders 24–52 min, purchase_order_lines 32–92 min), so the hang is not deterministic. If a PO import hangs or stays `processing`, remove `dir` first.

#### Status filters on incremental imports

The baseline imports filter on `updated-at[gt_or_eq]: ${last_modified_date}` (Remit To Addresses: `remit-to-addresses[updated-at][gt_or_eq]`) and on no status.
Project-specific slices often add more — `active: "true"`, `status`, `lookup[name][in]` — and a
status key in an incremental query is a trap: the import returns records that **changed and
still match**, so a GL account deactivated after the first load no longer matches, is never
fetched again, and stays `active: true` in the dataset. Matching keeps offering it. On one
project, 17 GL accounts deactivated in Coupa during the previous month were still active in the
dataset.
Deletions never arrive either: the import only upserts.

Ask two questions about every query key other than `updated-at`:

1. **Can a record leave the filtered set through an update?** (`active`, `status`.) If yes, do
   not filter on it in the import. Import the field (keep it in `fields`) and filter in the MDH
   matching query — inside `$search` or after the `$lookup`.
2. **Does anything downstream need the records outside the set?** (A "this PO is closed" or
   "this account is deactivated" warning instead of "not found", a validation rule.) If yes,
   load them, including in the first full load.

A filter is fine when the field never changes for a record (`lookup[name][in]`, a parent, a
currency, a `created-at[gt_or_eq]` floor), when records can only enter the set, and in a one-time
seed or bulk load whose follow-up incremental import is unfiltered. Limit history by
`created-at`, not by status.

**Detect:** query the endpoint with the excluded state and `updated-at[gt_or_eq]` set to the
dataset's first load (e.g. `active=false`), look those `id`s up in the dataset, and count the ones
still active.
**Repair:** first check that every matching query filters the status itself — a query that
relied on the import filter starts offering inactive records. Then remove the filter and let
one run use a fixed `updated-at[gt_or_eq]` date reaching back to the earliest stale record (it
is the backfill — in the case above an 18-record window, a 3-second run, 17 of 17 corrected),
then restore `${last_modified_date}`.

### 4.3 Dataset Naming Convention

Every imported dataset and the supplier and customer memorization collections carry the `_test` suffix by default (e.g., `suppliers_test`); `_tax_code_memorization` has none. The suffix is part of the release: the import hooks' `dataset_name` and the MDH configurations carry it, and the deploy tool creates the memorization collections under these fixed names — it has no suffix setting. It was chosen because CIB is deployed first against a Coupa TEST instance. When promoting to production:
- Create new datasets without `_test` suffix (e.g., `suppliers`)
- Update all MDH configurations, memorization hooks, and import webhooks to reference the new dataset names
- Or keep `_test` suffix and just point the import at the production Coupa URL

### 4.4 Memorization Collections

These are not imported from Coupa but built up by Rossum as users process documents:

| Collection | Created By | Natural Key | Stored Fields |
|-----------|-----------|-------------|---------------|
| `_supplier_memorization_test` | Supplier Memorization hook | `sender_name` + `sender_address` | `sender_match` |
| `_customer_memorization_test` | Customer Memorization hook | `recipient_name` + `recipient_address` | `recipient_match` |
| `_tax_code_memorization` | Tax Coding Memorization hook | `recipient_match` + `sender_match` + `line_item.item_description` + `description_export` | `item_tax_code_match`, `description_export`, `tax_code_match` |

> A filling collection is **not** evidence that memorization works — a key that is too specific mints a new row per document and is never recalled. Run the reuse-rate check in [9.1.1 Health check](#911-health-check-is-the-memorization-collection-actually-being-reused) before trusting any of these.

---

## 5. Master Data Hub — Matching & Enrichment

The MDH is the core engine that connects extracted document data to Coupa master data. CIB has **4 MDH hooks** with **15 match configurations**.

### 5.1 Hook Execution Order

```
MDH - Main (CIB)  (events: initialize, started, updated)
    ├── MDH - Payment Terms (CIB)        (run_after: MDH - Main)
    ├── MDH - Tax Codes (CIB)            (run_after: MDH - Main) — line-level and country queues only
    └── MDH - Coupa Invoice Check (CIB)  (run_after: MDH - Main)
```

Source-org IDs: **[1.x]** 343051 / 453520 / 540278 / 735955; **[2.0]** 1123384 / 1123400 / 1123401 / 1123402. A deployed org has its own.

### 5.2 MDH - Main (10 configurations)

**Config 1: Entities / Account Types** → `recipient_match`
- Dataset: `account_types_test`
- 9-query cascade: manual search → memorization → exact VAT → exact name → fuzzy name → exact address name → fuzzy address name → fuzzy name+address → all entities
- Maps: `recipient_name_match`, `recipient_primary_address_match`, `recipient_entity_country_code_match`

**Config 2: Customer Tax Registration** → `recipient_tax_registration_match`
- Dataset: `tax_registrations_test`
- 4-query cascade: exact number + owner-id → owner-id only → number only → all active
- Maps: `recipient_country_code_match`

**Config 3: Supplier** → `sender_match`
- Dataset: `suppliers_test`
- 7-query cascade: manual search → memorization → exact tax-id → exact primary-address VAT → exact name (`$toLower`) → fuzzy name → fuzzy name+address. No query uses `display-name`
- Maps: `sender_country_code_match`, `sender_payment_days_match`, `sender_display_name_match`, `sender_number_match`, `sender_name_match`

**Config 4: Order Header - Line Items** → `item_order_header_match`
- Dataset: `purchase_orders_test`
- 1 query: exact `po-number` match with `{item_order_id_calculated}`
- Condition: `'{item_order_id_calculated}' != '' and '{order_id}' == '{order_id}' and '{item_order_id}' == '{item_order_id}'`
- Maps: `item_order_header_status_match`, `item_order_header_ship_to_addr_match`, `item_requestor_email_match`

**Config 5: Order Header - Headers** → `order_header_match`
- Dataset: `purchase_orders_test`
- 1 query: exact `po-number` match with `{order_id_calculated}`
- Condition: `'{order_id_calculated}' != '' and '{order_id}' == '{order_id}'`
- Maps: `order_header_status_match`, `order_header_ship_to_addr_match`, `requestor_email`

**Config 6: Order Line - Line Items** → `item_order_item_match`
- Dataset: `purchase_order_lines_test`
- 6-query cascade: date range match → source-part-num match → description exact → description fuzzy → all lines for order → fallback without account lookup
- Condition: `'{item_order_id_calculated}' != ''`
- Maps 16 fields including: `item_po_line_number_match`, `item_po_line_type_match`, `item_po_line_uom_match`, `item_po_line_total_match`, `item_po_line_price_match`, `item_po_line_description_match`, `item_po_line_quantity_match`, `item_po_line_status_match`, `item_po_line_recipient_match`, `item_po_line_recipient_name_match`, `item_po_line_supplier_match`, `item_po_line_supplier_display_name_match`, `item_po_line_supplier_name_match`, `item_po_line_currency_match`, `item_po_line_sp_start_date_match`, `item_po_line_sp_end_date_match`

**PO Line Account Resolution**: The query uses `$cond` to resolve the account from either `account.account-type-id` or the first element of `account-allocations[0].account.account-type-id`, then `$lookup` to `account_types_test` to get the account type name.

**Config 7: Order Line - Headers** → `order_item_match`
- Same as Config 6 but for header-level fields (when no line items)
- Uses `{order_id_calculated}`, `{description_export}`, `{header_description}`, `{code}`, etc.
- Maps 16 fields with `po_line_*` prefix (no `item_` prefix)

**Config 8: Customer Ship-To Address** → `recipient_ship_to_match`
- Dataset: `addresses_test`
- 2-query cascade: exact ID match using `{order_header_ship_to_addr_match}` → fuzzy address search on `{recipient_address}`
- Label template: `{"name"} {"street1"} {"street2"} {"city"} {"postal-code"} {"state"}`

**Config 9: Contract** → `contract_match`
- Dataset: `contracts_test`
- 1 query: exact `number` match with `{contract_number_normalized}`
- Maps: `contract_supplier_match`, `contract_supplier_name`, `contract_customer_match`, `contract_customer_name`, `contract_status_match`

**Config 10: Order Blanket Header** → `order_blanket_match`
- Dataset: `purchase_order_lines_test`
- 1 complex aggregation: finds PO lines where account matches `{recipient_match}` AND supplier matches `{sender_match}`, then joins to POs with status `"issued"`
- All result actions = `default` (always shows list for user selection)

### 5.3 MDH - Payment Terms (2 configurations)

**Config 1: Payment Terms** → `payment_terms_match`
- Dataset: `payment_terms_test`
- 2-query cascade: exact match on `days-for-net-payment` + type `DaysAfterNetPaymentTerm` + active + no discount days → all active DaysAfterNetPaymentTerm without discount
- Maps: `payment_terms_days_match`, `payment_terms_code_match`

**Config 2: Early Payment Discount Terms** → `epd_payment_terms_match`
- Dataset: `payment_terms_test`
- 2-query cascade: exact match on `discount-rate` + `days-for-discount-payment` → all with discount days
- Condition: `'{epd_detected}' == 'true'`
- Maps: `epd_payment_terms_days_match`, `epd_payment_terms_code_match`, `epd_payment_terms_rate_match`

### 5.4 MDH - Tax Codes (2 configurations) — LINE LEVEL ONLY

**Config 1: Tax Codes (line-items)** → `item_tax_code_match`
- Dataset: `tax_codes_test` + `_tax_code_memorization`
- Complex single-query aggregation with:
  1. Memorization lookup via `$unionWith` on `_tax_code_memorization` (matches `sender_match` + `recipient_match` + `item_description`)
  2. Three-tier country matching via `$facet`: recipient country code → entity country code → any country
  3. Deduplication and priority sorting

**Config 2: Tax Codes (headers)** → `tax_code_match`
- Same pattern as line-items but uses `description_export` and `tax_rate_calculated` instead of `item_description` and `item_rate_calculated`

Because the hook is bound only to the line-level and country queues, Config 2 never runs on the header-level queue, so `tax_code_match` stays empty there. The "headers" in its name means "no line items", not "header-level taxation".

### 5.5 MDH - Coupa Invoice Check (1 configuration)

**Config: Coupa Invoice Existence Check** → `duplicate_invoice_statuses`
- Source: **External API** (not dataset — live Coupa call)
- Authentication: OAuth client credentials with `core.invoice.read` scope
- Query: GET `/api/invoices?supplier_id={sender_export}&invoice_number={document_id_manual}&fields=["id","status"]`
- Returns list of existing Coupa invoice statuses for the same supplier + invoice number
- Used for duplicate detection in Coupa
- The Coupa tenant URL and `client_id` are **hard-coded** in `source.auth` and in the query URL; only the secret comes from `{secrets.client_secret}`. Override both per environment (see 14)

---

## 6. Business Rules Validation

Validation is implemented with **native Rossum Rules** (the `/v1/rules` entity) in both baselines — *not* with the legacy Business Rules Validation extension. Each rule is a `trigger_condition` written in TxScript plus an `actions[]` array, typically a `show_message` and an `add_automation_blocker` on the same field. See the `business-rules-reference` and `txscript-reference` packs for the entity and the expression language.

**48 baseline rules**, byte-for-byte identical between 1.x and 2.0 in both trigger conditions and actions. The only difference is the name:

- **[1.x]** every rule is prefixed `TEST - ` (e.g. `TEST - PO is Closed (CIB)`).
- **[2.0]** the prefix is dropped, and five rules are added — see 6.4.

How the 48 are scoped:

| Scope | Count | Bound to |
|---|---|---|
| Common | 36 | every AP queue |
| Line-level taxation only | 8 | the line-level queues (**[2.0]** baseline line queue + all four country queues) |
| Header-level taxation only | 4 | the header-level queue |

Two of the 48 are not validation at all — `ShowHide - Hide Order Item Match` and `ShowHide - Hide Tax Code Match` drive field visibility (see 9.2).

### 6.1 Common Rules — 36 rules

The catalogue below is taken from the release rule objects. Names drop the ` (CIB)` suffix here; **[1.x]** adds the `TEST - ` prefix. *Blocker* means the rule also has an `add_automation_blocker` action.

| Rule | Severity | Blocker | Fires when |
|---|---|---|---|
| `Coupa Duplicate Found` | warning | yes | `coupa_invoices_statuses` not empty. Fixed text — the statuses are not in the message |
| `Coupa Total Mismatch` | warning | yes | `abs(coupa_total_calculated) != abs(amount_total)` — compared directly, not through `inv_total_issue_tag` |
| `Currency Mismatch (No Line Items)` | warning | yes | no line items, PO-backed, and `po_line_currency_match` is empty or differs from `currency.upper()` |
| `Currency Mismatch (With Line Items)` | warning | yes | the same per line, against `item_po_line_currency_match` |
| `Customer Mismatch Warning` | warning | yes | `recipient_name_match != recipient_name_export` — compared directly, not through the tag |
| `Supplier Mismatch Warning` | warning | yes | `sender_name_match != sender_name_export` |
| `Duplicate Detected` | warning | yes | `is_duplicate == 'true'`. Fixed text, no link to the other annotation |
| `Invoice Date Out of Range` | warning | yes | `date_issue_manual` at least 365 days in the past (`PAST_LIMIT_DAYS`) or 180 days in the future |
| `Invoice Number Missing` | warning | yes | `document_id` empty |
| `Invoice Number Too Long` | warning | yes | `len(document_id) > 40` |
| `Invoice Number Manual Too Long` | error | yes | `len(document_id_manual) > 40` |
| `Line Total Not Equal to Quantity x Unit Price` | warning | yes | `round(item_total_base, 2) != round(item_quantity * item_amount_base, 2)` |
| `Negative Amount - Not Credit Note` | warning | yes | not a credit note and `coupa_total_calculated < 0` |
| `Payment Terms Mismatch` | warning | yes | `int(sender_payment_days_match) != int(payment_terms_days_match)` |
| `PO Not Found in Coupa` | warning | yes | `order_id_calculated` set and `order_header_match` empty |
| `PO Not Found in Coupa (Line Items)` | warning | yes | the same per line |
| `PO is Closed` | error | **no** | `order_header_status_match != 'issued'`, on `order_item_match` |
| `PO is Closed (Line Items)` | error | **no** | `item_order_header_status_match != 'issued'` |
| `PO Line Match Required (No Line Items)` | error | yes | no line items, PO-backed, `po_closed != 'true'` and `order_item_match` empty |
| `PO Line Match Required (Line Items)` | error | yes | line items, PO-backed and `item_order_item_match` empty |
| `PO Line is Closed` | error | **no** | `po_line_status_match` not in `created`, `received`, `partially_received`, `soft_closed_for_receiving` |
| `PO Line is Closed (Line Items)` | error | **no** | the same per line |
| `Required - Description (No Line Items)` | error | **no** | no line items and `description_export` empty |
| `Required - Line Total or Unit Price` | error | **no** | `item_total_base` and `item_amount_base` both empty |
| `Required - Recipient Match` | error | **no** | `recipient_export` empty |
| `Service Period …` — 10 rules | warning | yes | header and line-item (`(Line Items)`) pairs of: Start After Document End, Start Before PO Start, Start After PO End, End Before PO Start, End After PO End — comparing `service_period_*_export` / `sp_item_*_date_export` with the `*_po_line_sp_*_normalized` fields |
| `ShowHide - Hide Order Item Match` | `show_hide_field` | — | `line_items_present == 'false'` (see 9.2) |

**Not in either release**, though earlier versions of this reference listed them: a "Customer not matched" or "Supplier not matched" warning (nothing fires on an empty `sender_match`; an empty `recipient_export` is the `Required - Recipient Match` error), "Payment terms not matched", any rule on `terms_calculated`, a contract-status rule and a rule on `inactive_po_line_tag`. No rule uses `currency_upper`, which does not exist.

**Error vs. blocker.** An `error` message blocks confirmation; a `warning` does not. Automation is stopped only by `add_automation_blocker`. Ten validation rules have no blocker — the eight `error` rules marked **no** above and in 6.2, and the two `Tax Code Required` warnings in 6.2 — plus the two ShowHide rules.

### 6.2 Line Level Taxation Rules — 8 rules

Bound to the line-level queue and **[2.0]** the four country queues.

| Rule | Severity | Blocker | Fires when |
|---|---|---|---|
| `Total Amount Not Equal to Without Tax + Tax` | warning | yes | `round(amount_total, 2) != round(amount_total_base + amount_total_tax, 2)` — the captured fields, not the `_calculated` ones |
| `Total Without Tax Not Equal to Sum of Item Totals` | warning | yes | `amount_total_base` differs from the sum of `item_total_base` |
| `Header Tax Not Equal to Sum of Line Taxes and Charges` | warning | yes | `amount_total_tax_calculated` differs from the sum of `item_tax_calculated` + `charges_total_tax_calculated` |
| `Total Amount Less Than Without Tax (Tax Invoice)` | warning | yes | tax invoice and `amount_total < amount_total_base` |
| `Required - Total Amount or Total Without Tax` | error | **no** | `amount_total` and `amount_total_base` both empty |
| `Tax Code Required (No Line Items, PO Backed)` | warning | **no** | no line items, PO-backed and `tax_code_match` empty |
| `Item Tax Code Required (Line Items, PO Backed)` | warning | **no** | line items, PO-backed and `item_tax_code_match` empty |
| `ShowHide - Hide Tax Code Match` | `show_hide_field` | — | `line_items_present == 'false'` |

The per-line currency rule is not line-only: `Currency Mismatch (With Line Items)` is one of the 36 common rules.

### 6.3 Header Level Taxation Rules — 4 rules

Bound to the header-level queue only.

| Rule | Severity | Blocker | Fires when |
|---|---|---|---|
| `Total Amount Not Equal to Subtotal + Tax + Charges` | warning | yes | `amount_total != amount_total_base + amount_total_tax_calculated + charges_calculated` (rounded to 2) — uses `amount_total_base`, not `amount_total_base_calculated` |
| `Total Amount Not Equal to Line Sum + Tax + Charges` | warning | yes | `amount_total` differs from the sum of `item_total_base` + `amount_total_tax_calculated` + `charges_calculated` |
| `Subtotal Not Equal to Sum of Item Totals` | warning | yes | `amount_total_base` differs from the sum of `item_total_base` |
| `Total Amount Less Than Subtotal (Tax Invoice)` | warning | yes | tax invoice and `amount_total < amount_total_base` |

### 6.4 Rules added in 2.0 — 5 rules

| Rule | Queue | Trigger | Action |
|---|---|---|---|
| `Move to BE queue` | E-invoicing Inbox | `field.source_country == 'BE'` | `change_queue` on `annotation_confirmed`, `reimport: true` |
| `Move to PL queue` | E-invoicing Inbox | `field.source_country == 'PL'` | as above |
| `Move to FR queue` | E-invoicing Inbox | `field.source_country == 'FR'` | as above |
| `Move to DE queue` | E-invoicing Inbox | `field.source_country == 'DE'` | as above |
| `KSeF Offline Invoice (PL)` | AP Documents - PL | `field.ksef_offline_invoice == 'true'` | blocking `error` + automation blocker on `ksef_offline_invoice` |

The four routing rules are the only rules on the Inbox, and the only rules in CIB whose action is `change_queue` rather than a message. `KSeF Offline Invoice (PL)` is the only country-specific *validation* rule: an invoice issued while the issuer was offline has no KSeF number yet, and the rule blocks confirmation until one is assigned.

---

## 7. Export Pipeline — Rossum to Coupa

The export pipeline is a chain of hooks that execute on the `annotation_content.export` event, driven by `run_after`. Both baselines start from the same Jinja mapping and end at the same Coupa invoice, but the chain between them was rebuilt in 2.0.

### 7.1 Pipeline Flow

**[1.x] — eight webhooks, four of them response parsers:**

```
Export Mapping (418429/419136)     ← Jinja template → Coupa invoice JSON
    │
Create Draft (418430)              ← POST /api/invoices → Draft invoice created
    │
Create Draft - parse response (418431)  ← Extracts api1_status_code, coupa_invoice_id
    │
    ├── Attach Image Scan (418432)      ← PUT /api/invoices/{id}/image_scan (gated by api2_gate)
    │       │
    │   parse response (418433)         ← Extracts api2_status_code
    │
    ├── Attach Rossum URL (418434)      ← POST /api/invoices/{id}/attachments (gated by api2_gate)
    │       │
    │   parse response (418435)         ← Extracts api3_status_code
    │
Submit Document (418436)           ← PUT /api/invoices/{id}/submit (gated by api4_gate)
    │
Submit Document - parse response (418437)  ← Extracts api4_status_code
    │
Handle Coupa Responses (418438)    ← Serverless function: shows errors/warnings
```

Attach Image Scan and Attach Rossum URL run in **parallel** (both depend on 418431). Submit waits for both.

**[2.0] — five serverless functions in a strict line:**

```
Export Mapping - Line Level (1123383)  /  - Header Level (1123394)   ← Jinja template → Coupa invoice JSON
    │
Export Pipeline - 1. Create Draft (1157241)             ← POST /api/invoices
    │
Export Pipeline - 2. Attach Image Scan (1342015)
    │
Export Pipeline - 3. Attach Rossum URL (1342016)
    │
Export Pipeline - 4. Attach Supplementary Files (1342017)   ← new in 2.0
    │
Export Pipeline - 5. Submit Document (1342018)
    │
Handle Coupa Responses (1123389)                        ← reads document_relations, surfaces errors
```

Three things changed with it:

- **No response-parser hooks.** Each stage handles its own response, and response *bodies* land in `document_relations` rather than in schema fields. 2.0 therefore removed `api1_status`, `api{1,2,3,4}_response_body`, `api{2,3,4}_url`, `api{2,4}_gate`, `oauth_url` and `create_draft_url`. The `api{1,2,3,4}_status_code` fields **survive**, joined by `attachments_status_code` for the new stage 4.
- **No per-cluster URL.** The stages are `function` hooks, so a deployment no longer has to point them at the right region. `Export Mapping` remains a `webhook` against the Custom Format Templating service and still does.
- **Serial, not parallel.** Every stage declares the previous one in `run_after`, including the two attachment stages that ran in parallel in 1.x.
- **Stage 4 is new.** It attaches the source email as `.eml` and every e-invoice XML (`iterate_over` the document relations of type `einvoice`). All five stages run the same Request Processor code and differ only in settings; only Submit has `retry_count` 4, the others 0.

> **[2.0] deployment prerequisite.** The pipeline functions need `maximum_hook_timeout` ≥ 360s on the organization group. Without it every pipeline hook is rejected at deploy with `400 Ensure this value is less than or equal to 60` — and because prd2 logs that per hook and still exits 0, the deploy looks successful while the organisation has **no export chain at all**. Only Rossum can raise the cap; arrange it before the deploy.

### 7.2 Gating Logic

**[1.x]** — gates are schema fields:

- `api2_gate`: non-empty if `api1_status_code == "201"` — gates API 2 and 3
- `api4_gate`: non-empty if `api1_status_code == "201"` AND `sf_submit_for_approval == "Yes"` — gates API 4

**[2.0]** — stages 2–4 gate on `api1_status_code` 200/201 and a non-empty `coupa_invoice_id`. Stage 5 `Submit Document` gates on `api3_status_code` — the **Attach Rossum URL** stage — and on `sf_submit_for_approval == "Yes"`. A failed URL attachment therefore blocks submission, and `Handle Coupa Responses (CIB)` shows no message for it: the invoice silently stays a draft.

In both: if draft creation fails, the later calls are skipped. If submission is not warranted, only the submit call is skipped — the invoice stays a draft in Coupa. **[2.0]** Submit is also skipped when stage 3 fails; **[1.x]** submit depends only on `api1_status_code` through `api4_gate`.

### 7.3 Export Mapping Template (Line Level)

The Jinja template produces Coupa invoice JSON. Key structure:

```json
{
  "taggings": [{"tag": {"name": "rossum_draft|rossum_submit"}}, {"tag": {"name": "rossum_automated"}}],
  "currency": {"code": "EUR"},
  "supplier": {"id": 12345},
  "document-type": "Invoice",
  "account-type": {"id": "entity_id"},
  "invoice-date": "2024-01-15",
  "invoice-number": "INV-001",
  "payment-term": {"id": 12},
  "line-level-taxation": true,
  "invoice-charges": [{"type": "InvoiceShippingCharge", "total": 10, "tax-lines": {"tax-line": {"type": "TaxLine", "amount": 1.9, "rate": 19}}}],
  "invoice-lines": [
    {
      "uom": {"code": "EA"},
      "price": 100.00,
      "type": "InvoiceQuantityLine",
      "quantity": 2,
      "description": "Widget",
      "order-line-num": "1",
      "order-header-num": "PO-001",
      "tax-lines": {"tax-line": {"type": "TaxLine", "amount": 38.00, "rate": 19, "tax-code": {"id": 5}}}
    }
  ]
}
```

**Conditional elements in template:**
- `taggings`: `rossum_tag`, plus `rossum_automated` if the annotation was automated — nothing else. The other `*_tag` fields are internal and are not sent.
- `ship-to-address`: Only if `recipient_ship_to_match` is set
- `contract`: Only if `backing_document == "contract"`
- `payment-term`: Only if `payment_terms_export` is set
- `requester-email`: From `requestor_email` or `requestor_email_calculated`
- Line items: Loop over `line_items` tuple, with fallback to header fields if no line items
- **[2.0]** also sends `creation_source`, a `requester_email_xml` fallback for `requester-email`, `override-tax-country` from `country_code`, the e-invoice party fields, `transaction-uuid`, `split-payment-mechanism`, `line-tax-exempt` and `line-tax-reverse-charge`

### 7.4 Header Level Template Differences

- `line-level-taxation: false`
- Tax at header level: `"tax-lines": [{"amount": tax_amount}]` (no tax-code)
- Charges as separate fields: `"shipping-amount"`, `"handling-amount"`, `"misc-amount"` (no `invoice-charges`)
- No per-line tax-lines

### 7.5 OAuth Configuration

All export API calls use OAuth 2.0 client credentials:
- Token URL: `{coupa_api_base_url}oauth2/token`
- Client ID: from `oauth_client_id` formula field
- Client Secret: from hook secret `client_secret`
- Scopes: `core.invoice.create core.invoice.read core.invoice.write`

Declare `client_secret` in the export hook's `secrets_schema` so the Secrets editor
prefills the key as `__change_me__` — and use the **open string-map shape**, because the
Request Processor caches the OAuth token into hook secrets at runtime (a closed
`"additionalProperties": false` schema would reject that write):

```json
"secrets_schema": {
  "type": "object",
  "properties": {
    "client_secret": { "type": "string", "minLength": 1, "description": "Coupa OAuth client secret" }
  },
  "additionalProperties": { "type": "string" }
}
```

The shipped hooks declare `client_secret` without `minLength`; the schema above is the recommended form.

### 7.6 Handle Coupa Responses (Serverless Function)

The final step parses the API responses and shows errors/warnings to the user. **[1.x]** it reads the `api{1..4}_response_body` schema fields; **[2.0]** it reads `document_relations` — keys `draft_creation_response`, `image_scan_response`, `rossum_url_response`, `email_attachment_response`, `xml_attachment_response`, `submission_response` — but never the stage-4 results, so attachment failures are invisible, and a submit skipped because stage 3 failed shows no message. The error handling is the same in both:

```python
def handle_api_response(x, status_code, response_body, message, is_error):
    if int(status_code) < 400: return
    # Parse Coupa error JSON: {"errors": {"field": ["message1", "message2"]}}
    # Show as error (API 1 - blocks export) or warning (API 2-4)
```

- **API 1 failure** (Create Draft): Shows **error** — document moves to Failed Export
- **API 2-4 failure**: Shows **warning** — document moves to Exported but with visible warnings

---

## 8. E-invoicing [2.0]

### 8.1 Why e-invoices are different

In the classic CIB flow a document arrives as a PDF, Rossum's AI extracts the values, and extraction confidence drives automation. With an e-invoice the data is already structured — there is nothing to extract. What the integration still has to provide is everything *around* extraction: validation against Coupa master data, enrichment, the human review step when something does not reconcile, and the export.

CIB therefore treats an e-invoice as a document whose captured values are populated by a **mapping** instead of by the engine, and then runs it through the same business logic as any other AP document. Some fields populated from XML carry `score_threshold: 0`, so confidence never blocks automation for them; most do not (see 1.3). They stay captured fields, so a user can still correct them against the rendered document and the engine can be trained on them.

### 8.2 Direction of the integration

E-invoices do **not** reach Rossum from the network. Coupa receives them and hands them over:

1. Coupa receives the e-invoice and creates an *invoice enrichable document* record.
2. Coupa uploads the XML (and any rendered PDF) to the Rossum **E-invoicing Inbox** queue.
3. Rossum maps the XML into the schema, determines the source country, and routes to the country queue.
4. The country queue runs the full CIB logic — matching, validation, enrichment, review.
5. The export pipeline creates the invoice in Coupa and, where warranted, submits it.
6. Throughout, Rossum reports annotation status back onto the Coupa record.

The **Coupa upload record, not the Rossum annotation, is the anchor**. Its id is stored on the annotation in the hidden `upload_id` field, and that is what the status sync writes against.

### 8.3 Ingestion — the Coupa E-Invoicing extension

Hook `Coupa E-Invoicing` (1170832), a webhook on `upload.created` bound to the Inbox queue only. It holds one configuration per supported national format under `settings.configurations`. Each has:

- a **`trigger_condition`** — an XPath `selector` evaluated against the uploaded XML plus `file_type: xml`. Configurations are evaluated **in order and the first match wins**.
- a **`fields`** list — for every schema field, one or more XPath `selectors`. The first selector returning a value wins, so a configuration can express fallbacks (the Polish `P_2A` invoice number falling back to `P_2`).

Beyond plain node selection a field mapping can concatenate and normalise (multi-line addresses folded into one `sender_address`), aggregate with XPath functions (`sum()` over the numbered Polish `P_13_*` / `P_14_*` elements), translate a code list through a `mapping` table (KSeF `RodzajFaktury`: `KOR`, `KOR_ROZ`, `KOR_ZAL` → `credit_note`; `VAT`, `ROZ`, `UPR`, `ZAL` → `tax_invoice`; plus a `__default__` entry), or assign a literal — which is how `source_country` is set.

`skip_non_existing_schema_ids: true` means fields the target schema lacks are skipped rather than failing the import, so one configuration can serve queues with slightly different schemas.

### 8.4 Format coverage

Six configurations, in evaluation order:

| # | Format | Root element | Buyer-country test | `source_country` | Fields |
|---|---|---|---|---|---|
| 1 | KSeF FA(3) — Polish National e-Invoice System | `Faktura` | — (format is country-specific) | `PL` | 32 |
| 2 | UBL 2.1 / Peppol BIS 3.0 | `Invoice` or `CreditNote` | `IdentificationCode = FR` | `FR` | 36 |
| 3 | UBL 2.1 / Peppol BIS 3.0 | `Invoice` or `CreditNote` | `IdentificationCode = BE` | `BE` | 32 |
| 4 | UBL 2.1 / Peppol BIS 3.0 | `Invoice` or `CreditNote` | `IdentificationCode = DE` | `DE` | 32 |
| 5 | UN/CEFACT CII — X-Rechnung, ZUGFeRD | `CrossIndustryInvoice` | `CountryID = DE` | `DE` | 30 |
| 6 | UN/CEFACT CII — Factur-X | `CrossIndustryInvoice` | `CountryID = FR` | `FR` | 35 |

The same syntax serves several markets, so the configurations are separated by the buyer's country code — `cac:AccountingCustomerParty/…/cac:Country/cbc:IdentificationCode` for UBL, `BuyerTradeParty/PostalTradeAddress/CountryID` for CII. There is deliberately **no catch-all**: a document whose buyer country is none of the four matches nothing and stays in the Inbox unmapped, rather than being silently attributed to a country it did not come from.

> **v2.0.0 shipped configuration 6 without its country test.** The selector was a bare `/*[local-name()='CrossIndustryInvoice']`, making it a catch-all evaluated after the German one, so in an organisation deployed from v2.0.0 **any CII invoice not identified as German is labelled `FR`**, routed to the French queue, and validated against French expectations. Fixed in **v2.0.1**. Check this selector first when a CII invoice turns up in the wrong country queue, and treat it as a known defect of any org still on v2.0.0.

### 8.5 The E-invoicing Inbox queue

All e-invoices land in the single **E-invoicing Inbox** (2860788) before reaching a country queue. It is a triage queue, not a processing queue: its schema is an 86-field subset of the 260-field line-level schema — the header, supplier, customer, amounts, charges and line-item fields the mappings populate, and nothing else. No matching fields, no `recipient_search` / `sender_search`, no EPD section, no export pipeline, and no MDH hook bound to it. It has only three formula fields — the charge tax ones and `oauth_client_id`, whose client id is hard-coded.

The Inbox exists so that one upload target serves every country (keeping the Coupa side simple), the source country is determined from the document rather than the upload target, and a document in an unexpected format is visible in one place instead of failing silently.

It is the only queue with `automation_level: always`. In production the Inbox is expected to confirm automatically, because every value in it comes from the mapping and no rule blocks it.

### 8.6 Routing to the country queues

Four rules on the Inbox, each firing on `annotation_confirmed`:

| Rule | Condition | Target |
|---|---|---|
| Move to BE queue | `field.source_country == 'BE'` | AP Documents - BE |
| Move to PL queue | `field.source_country == 'PL'` | AP Documents - PL |
| Move to FR queue | `field.source_country == 'FR'` | AP Documents - FR |
| Move to DE queue | `field.source_country == 'DE'` | AP Documents - DE |

All four use `change_queue` with `reimport: true`. Re-import is required because the country schema is far richer than the Inbox schema: on arrival the document is processed from the beginning — values carried over from the Inbox are kept, formula fields are evaluated, and matching against Coupa master data runs for the first time.

> Routing happens **on confirmation**, so in the worst case a user sees an e-invoice twice — once in the Inbox, once in the country queue. If documents pile up in the Inbox, check its automation settings before assuming a mapping problem.

### 8.7 Country schemas

Each country queue has its own schema, derived from the 260-field line-level baseline schema. All four **add** the hidden `upload_id` and **drop** `source_country` — that field exists only to drive the routing rule, and has done its job by the time the document arrives.

| Queue | Fields | Relative to the line-level baseline |
|---|---|---|
| AP Documents - FR | 256 | + `upload_id`; − `source_country`, `invoice_misc_charge`, `invoice_shipping_charge`, `payment_order_number`, `split_payment_mechanism`. Keeps the Peppol `sender`/`recipient_electronic_address` and `…_party_identifier` fields, which Peppol uses for routing and French compliance requires be retained. |
| AP Documents - PL | 262 | + `upload_id` and a 5-field `ksef_section` (`ksef_offline_invoice`, `barcode_qrcode`, `barcode_qrcode_item`, `transaction_id`, `transaction_date`) — 7 nodes with `upload_id` and the section itself; − `source_country` and the four Peppol party fields. Keeps `split_payment_mechanism`, which Poland needs. `type_of_receipt` becomes an enum (VAT, KOR, ZAL, ROZ, UPR, KOR_ZAL, KOR_ROZ), both mismatch tags are hidden, and the new XML fields have `can_export: true`. |
| AP Documents - BE | 252 | + `upload_id`; − `source_country`, the four Peppol party fields, `invoice_misc_charge`, `invoice_shipping_charge`, `payment_order_number`, `split_payment_mechanism`. |
| AP Documents - DE | 252 | identical delta to BE. |

All four also set `country_code` to a constant (`'BE'` / `'PL'` / `'FR'` / `'DE'`; `''` on the baseline schemas) and hide `quantity_export` and `price_export`.

Everything else — every formula, every matching field, every export field — is the baseline schema. That is what makes a country queue a full AP queue and lets PDFs for that country be sent straight to it.

### 8.8 Status synchronisation back to Coupa

Coupa owns the invoice record from the moment it receives the e-invoice, so it has to be told what happened in Rossum. Two `function` hooks (python3.12) do this on `annotation_status.changed`, issuing a `PUT` against `api/invoice_enrichable_documents/{upload_id}`:

- `Coupa E-Invoicing Status Sync (CIB)` (1309898) — bound to all four country queues.
- `Coupa E-Invoicing Status Sync - Inbox (CIB)` (1309899) — bound to the Inbox.

| Rossum status | Coupa `external_status` | Also sets `status: not_completed` |
|---|---|---|
| `to_review` | `reviewing` | no |
| `postponed` | `postponed` | no |
| `exported` | `exported` (with the Coupa invoice id) | no |
| `rejected` | `rejected` | yes |
| `deleted` | `deleted` | yes |
| `failed_export` | `export failed` | yes |
| `failed_import` | `import failed` | yes |

The Inbox instance uses the same map **without** `exported` and `rejected`, because documents leave the Inbox by routing rather than by export. Its Coupa base URL is hard-coded in `settings.coupa_base_url`.

The hooks cache their Coupa token in the hook secret `coupa_auth_token`, and treat a 404 on an already closed enrichable document as a warning.

**Annotations without an `upload_id` are skipped** — they did not arrive through a Coupa upload, so there is no Coupa record to update. This is what lets PDFs and e-invoices share a country queue.

The Coupa base URL and OAuth client id are read from the `coupa_api_base_url` and `oauth_client_id` schema fields when the queue's schema has them, falling back to the extension settings otherwise — the same resolution the export pipeline uses, keeping the queue the single source of truth for its Coupa instance.

### 8.9 Country-specific handling

**Poland — KSeF.** Two mechanics no other supported market needs, which is why the Polish schema carries a dedicated KSeF section:
- *Offline invoices.* An invoice issued while the issuer was offline carries no KSeF number yet. The `KSeF Offline Invoice (PL)` rule raises a blocking error while `ksef_offline_invoice` is true, so the document cannot be confirmed until a KSeF ID is assigned and the document updated.
- *Verification codes.* `Get Barcodes - KSeF PL (CIB)` (1157629) runs on `annotation_content.initialize` — bound to the PL queue alone — and populates the QR/barcode values the Polish rules require.

**Germany.** Served by two configurations because both syntaxes are in real use: UBL (X-Rechnung) and CII (X-Rechnung CII profile, ZUGFeRD). Both map into the same German schema and queue.

**France.** Served by UBL (Peppol BIS 3.0) and CII (Factur-X). The French schema additionally retains the electronic address and party identifier fields for both trading parties.

**Belgium.** UBL only.

### 8.10 Export

E-invoices are exported by the same pipeline as any other CIB document, including the submit stage. Whether a given document is submitted or left as a draft is decided by the CIB submission logic on the document itself, not by which country queue it sits in.

The E-invoicing Inbox has no export pipeline at all — documents leave it by routing, and the country queue does the exporting.

---

## 9. Serverless Functions

### 9.1 Memorization Hooks

Three memorization hooks save user selections to Data Storage for future recall:

IDs differ by baseline: **[1.x]** 693315 / 693316 / 747526; **[2.0]** 1123404 / 1123405 / 1123407. Collections and behaviour are the same.

**Supplier Memorization** (hook 693315 / 1123404):
- Events: `annotation_status.changed`; acts only when the annotation becomes confirmed or exported. `skip_automated_annotations: true`, so **automated documents are never memorized**, and `skip_record_insert` skips rows with an empty name and address
- Collection: `_supplier_memorization_test`
- Saves: `sender_name`, `sender_address` → `sender_match`
- Natural key: `sender_name` + `sender_address` (first record wins for `sender_match`)

**Customer Memorization** (hook 693316 / 1123405):
- Same code as supplier, different settings
- Collection: `_customer_memorization_test`
- Saves: `recipient_name`, `recipient_address` → `recipient_match`

**Tax Coding Memorization** (hook 747526 / 1123407):
- Collection: `_tax_code_memorization`
- Saves: `sender_match`, `recipient_match`, `item_description`, `description_export`, `tax_code_match`, `item_tax_code_match`
- Uses `unwind` on `line_item` — creates separate records per line item
- Same event and `skip_automated_annotations: true`; `skip_record_insert` skips rows with an empty description or an empty supplier and customer. Header and line memories share the one collection
- Natural key: `recipient_match` + `sender_match` + `line_item.item_description` + `description_export`

### 9.1.1 Health check: is the memorization collection actually being reused?

A memorization hook that writes perfectly can still never be read from. Rows accumulate, the hook log is green, nothing errors — and yet every confirmed document mints a **new** key, so the `$unionWith` recall in the MDH cascade never once hits. This is a silent failure: from the write side a memory that has fired thousands of times and one that has never fired look identical. Check it explicitly; never assume a memorization feature works because the collection is filling up.

**The check — reuse rate.**

1. Read the hook's `settings.unique_natural_key`. That is the authoritative key — not this document, not the hook's `.py`.
2. Group the collection by exactly those fields, applying the **same normalization the consuming MDH `$unionWith` applies** (typically `$trim` → `$toLower` → `$toString` → `$ifNull`). Normalizing differently here measures a key nobody uses.
3. Compare distinct-key count to row count.

```json
[
  { "$group": {
      "_id": {
        "name": { "$toLower": { "$trim": { "input": { "$ifNull": ["$sender_name", ""] } } } },
        "addr": { "$toLower": { "$trim": { "input": { "$ifNull": ["$sender_address", ""] } } } }
      },
      "rows": { "$sum": 1 }
  }},
  { "$group": { "_id": null, "distinct_keys": { "$sum": 1 }, "rows": { "$sum": "$rows" } } },
  { "$addFields": { "reuse_rate": { "$divide": ["$rows", "$distinct_keys"] } } }
]
```

| Reading | Meaning |
|---|---|
| `reuse_rate ≈ 1.0` (distinct keys ≈ rows) | **The memory has never fired.** Every document creates a new key — the key is too specific. Write-only collection. |
| `reuse_rate > 1` | Keys are being hit again; the memory is doing real work. |

**Companion check — which key components are load-bearing?** Group by a *subset* of the key and look for subset-keys that map to more than one target value:

```json
[
  { "$group": { "_id": "$sender_address", "targets": { "$addToSet": "$sender_match" } } },
  { "$addFields": { "n": { "$size": "$targets" } } },
  { "$match": { "n": { "$gt": 1 } } },
  { "$sort": { "n": -1 } },
  { "$limit": 20 }
]
```

A subset that almost never maps to more than one target is **not discriminating** — it is only fragmenting the key, and dropping the other components would cost no accuracy while restoring recall. A subset with many multi-target keys genuinely needs the extra components to stay unambiguous.

**Measured example (production deployment, 2026-08).** A ship-to memorization collection held 49 rows across 48 distinct keys — a reuse rate of 1.02, meaning the memory had fired essentially never. The natural key includes the raw OCR'd `recipient_address`, and OCR of one physical location varies like this:

```
1200 EXAMPLE DRIVE, # B
1200 EXAMPLE DRIVE SUITE B
1200 Example Dr, Suite B
```

Those are three separate keys for one location, all pointing at the same ship-to. `$trim` only strips the **ends** of a string, so internal punctuation, abbreviation variance (`DRIVE`/`Dr`, `SUITE`/`#`), and a trailing country line defeat it completely.

This generalizes to every settings-driven memorization collection in the CIB shape — a single deployment typically runs several (supplier, customer, GL coding, GL location, tax code), all built with the same key construction, so one over-specific key pattern tends to affect all of them at once. Run the reuse-rate check on each before reporting that memorization "is working".

**Fixes**, in order of preference: narrow the key to the components the companion check proves are load-bearing (often an ID or code, not an address); or normalize harder on **both** the write and the `$unionWith` read side — collapse internal whitespace and punctuation, not just the ends. Changing normalization on only one side orphans every existing row.

### 9.2 ShowHide Fields

Field visibility is driven by **two native Rules, not hooks** — in both baselines:

| Rule | Scope | Controls |
|---|---|---|
| `ShowHide - Hide Order Item Match (CIB)` | every AP queue | `order_item_match` |
| `ShowHide - Hide Tax Code Match (CIB)` | line-level queues only | `tax_code_match` |

When the `line_items` multivalue is empty, the header-level PO line match (and, on a line-level queue, the header tax code match) is shown; when line items are present those fields are hidden, because matching happens per line item instead.

### 9.3 Metadata Propagator (hook 735954 / 1123391)

Sets two technical fields on `initialize` and `started` events:
- `rossum_annotation_link` = URL to the Rossum annotation
- `original_file_name` = name of the uploaded document file

---

## 10. Duplicate Detection

### 10.1 Rossum Duplicates (Duplicate Handling hook 444021 / 1123399)

Uses the `duplicate-finder.rossum-ext.app` extension:
- Searches by: `document_id_manual` + `sender_export`
- When duplicate found: `is_duplicate` field is populated
- Rule `Duplicate Detected (CIB)` shows a fixed-text warning and adds an automation blocker when `is_duplicate == 'true'` — no link to the other annotation
- Runs on `initialize`, `started`, `updated`, `confirm`, `export` and `annotation_status.changed`, with `run_after: MDH - Main (CIB)`. It has two configurations: data-based (`fill_field` + `mark_duplicate`) and file-based (`relation`, on initialize only)

**Two detection methods:**
- **File-based**: Bitwise comparison — detects exact same file uploaded multiple times
- **Extracted data-based**: Same invoice number + same supplier = duplicate

### 10.2 Coupa Duplicates (MDH - Coupa Invoice Check hook 735955 / 1123402)

Live API call to Coupa on every document open/update:
- GET `/api/invoices?supplier_id={sender_export}&invoice_number={document_id_manual}`
- Returns statuses of any existing Coupa invoices with same supplier + number
- `duplicate_invoice_statuses` field shows results
- `coupa_invoices_statuses` formula joins unique statuses as comma-separated string
- Rule `Coupa Duplicate Found (CIB)` shows a fixed-text warning — the statuses are not in the message — and adds an automation blocker
- The Coupa URL and `client_id` are hard-coded in the hook; see 5.5

---

## 11. Tagging System

Tags are Coupa-side labels attached to invoices. **Only two reach Coupa**: `rossum_tag` (`rossum_draft` / `rossum_submit`) and `rossum_automated`. The Export Mapping templates of both baselines send nothing else in `taggings`; the other rows below are internal formula flags, visible in Rossum only. To send one to Coupa, add it to the template's `taggings` array (see 14).

| Tag | Formula Field | Condition | Purpose |
|-----|--------------|-----------|---------|
| `rossum_draft` | `rossum_tag` | `sf_submit_for_approval == "No"` | Document drafted (not submitted) |
| `rossum_submit` | `rossum_tag` | `sf_submit_for_approval == "Yes"` | Document submitted |
| `rossum_automated` | (in template) | Annotation was automated in Rossum | No human review needed |
| `enforced_draft` | `enforced_draft_tag` | `enforce_draft == "Yes"` | User forced draft |
| `inv_total_issue` | `inv_total_issue_tag` | Coupa total != document total | Amount discrepancy |
| `recipient_mismatch` | `recipient_mismatch_tag` | PO customer != document customer | Entity mismatch |
| `supplier_mismatch` | `supplier_mismatch_tag` | PO supplier != document supplier | Supplier mismatch |
| `po_line_inactive` | `inactive_po_line_tag` | PO line status not active | Inactive PO line |
| `discount_terms` | `epd_tag` | EPD expires > 0 and EPD term matched | Early payment discount |

---

## 12. UOM & Line Type Logic

### Invoice Line Type
- PO line type `OrderAmountLine` → Invoice line type `InvoiceAmountLine`
- PO line type `OrderQuantityLine` (or no PO) → Invoice line type `InvoiceQuantityLine`

### Export Values by Line Type

| | InvoiceQuantityLine | InvoiceAmountLine |
|---|---|---|
| Quantity | `round(item_quantity_calculated, 6)` | Empty (not sent) |
| Price | `item_amount_base_calculated` (unit price) | `item_total_base_calculated` (total) |
| UOM | PO line UOM or `"EA"` | PO line UOM or `"EA"` |

**Header level taxation additional handling**: For `OrderAmountLine`, `item_net_total_coupa` = price only (no quantity multiplication).

### UOM Precision Warning
Coupa UOMs have configurable decimal precision. If precision is 0 and quantity has decimals, Coupa truncates. Always verify UOM precision settings in Coupa.

---

## 13. Credit Note Handling

### Sign Logic

The hidden `credit_notes_amounts` field (default `"negative"`) sets the sign **Coupa receives** for a credit note — not the sign on the source document:
- `"negative"` — credit-note amounts are exported as `-abs(value)`
- `"positive"` — credit-note amounts are exported positive

Reading it as a description of the document inverts credit notes.

The `set_value_sign_line_items()` helper in amount formulas:
1. Checks if header total is positive or negative
2. Checks if all line values are positive or all negative
3. Applies sign correction based on `credit_notes_amounts` setting

### Credit Note Export Rules
- Credit notes are **always drafted** in Coupa (`sf_submit_for_approval` always returns `"No"` for credit notes)
- `document-type` in export template: `"Credit Note"` (vs `"Invoice"`)
- `is-credit-note`: `true`
- Amounts are adjusted so Coupa receives them in correct sign convention

---

## 14. Customization Guide

### Common Customizations on Top of CIB

**Account Coding (Billing Segments)**:
- Replicate lookup values from Coupa (already done by CIB: `lookup_values_test`)
- Add segment fields to schema (e.g., `gl_account`, `cost_center`, `department`)
- Create MDH configurations to match segments
- Add segment fields to export template invoice lines
- Implement memorization for segment selections

**Remit-To / Bank Details**:
- Varies by customer: Remit-To addresses, SIM records, custom fields
- Replicate relevant data (already done for `suppliers_remit_to_addresses_test`)
- Add matching logic and validation rules
- Add to export template

**Custom Fields**:
- Add schema fields for Coupa custom fields
- Add to export template as `custom-fields: {"custom-field-name": "value"}`
- May need MDH matching for lookup-type custom fields

**Additional Tags**:
- Add formula fields for custom tag logic
- Add to export template taggings array

**Withholding Tax**:
- Not in CIB — add as custom feature
- Typically involves additional tax lines in export

### Deployment / Environment Promotion

When moving from test to production:
1. **Update Coupa API base URL** in `coupa_api_base_url` formula
2. **Update OAuth client ID** in `oauth_client_id` formula
3. **Update `MDH - Coupa Invoice Check (CIB)`** — the Coupa URL and `client_id` are hard-coded in `source.auth` and in the query URL
4. **[2.0] Update `Coupa E-Invoicing Status Sync - Inbox (CIB)`** `settings.coupa_base_url`, and the `oauth_client_id` formula on the **Inbox** schema, which has its own hard-coded client id
5. **Update OAuth client secret** in hook secrets
6. **Update dataset names** if removing `_test` suffix (affects ALL MDH configs, memorization hooks, import hooks)
7. **Update the import hooks' `settings.credentials`** (`base_api_url`, `client_id`) and their secrets
8. **Update import schedules** if different for production
9. **Verify OAuth scopes** match production Coupa setup

Miss 3 or 4 and the Coupa duplicate check and the Inbox status sync keep talking to the old Coupa instance. At first deploy the deploy tool writes the configured Coupa URL and client id into all of these (schema formulas including the Inbox, import credentials, Invoice Check `source.auth` and query URL, Status Sync settings); they need updating by hand only when an existing org is re-pointed to another Coupa.

### Adding a New Import

To replicate a new Coupa object:
1. **[1.x]** create a `webhook` on `invocation.scheduled` + `invocation.manual` with `config.url` `https://<cluster gateway>/svc/scheduled-imports/api/coupa/v1/import` for the org's region (see 4.1); **[2.0]** create a `Coupa Master Data Import` `job` hook from Store template 55
2. Configure `settings.credentials` and `settings.import_config` as in 4.2: `endpoint`, `dataset_name`, `method: update`, `id_keys`, `query.fields`, `query.order_by: id`, the `updated-at[gt_or_eq]` anchor and `credentials.client_scope`
3. Set schedule (cron expression)
4. The import creates/updates the Data Storage collection automatically

### Adding a New MDH Configuration

1. Add a new configuration object to the appropriate MDH hook's `configurations` array
2. Define: dataset, queries (cascade from exact → fuzzy → fallback), mapping, result_actions
3. Set `action_condition` if the match should only run conditionally
4. Add `additional_mappings` for fields to populate beyond the main target
5. Create corresponding schema fields for target and additional mappings

### Modifying Business Rules

CIB rules are **native Rossum Rules** (`/v1/rules`), one object per rule — not entries in an extension's `checks[]` array. Each has a TxScript `trigger_condition` and an `actions[]` list. The example is the shipped `PO is Closed (CIB)`: an error with no automation blocker. Most warnings add an `add_automation_blocker` action next to the `show_message` (see 6.1):

```json
{
  "name": "PO is Closed (CIB)",
  "enabled": true,
  "trigger_condition": "not is_empty(field.order_header_status_match) and field.order_header_status_match != 'issued'",
  "actions": [
    { "type": "show_message", "event": "validation",
      "payload": { "type": "error", "content": "The PO is closed.", "schema_id": "order_item_match" } }
  ],
  "queues": ["…/queues/2766927", "…/queues/2860789"]
}
```

- The trigger fires when the condition is **true** — write the *failure* case, not the pass case.
- `show_message` with `payload.type` `error` blocks confirmation; `warning` does not.
- `add_automation_blocker` is a separate action; a warning alone does not stop automation.
- Scope is the `queues` array. Adding a queue to a CIB deployment means adding it to the 48 baseline rules; removing one means taking it out again (see [2. Scoping the Deployment](#2-scoping-the-deployment-20)).

Guard every field reference with `is_empty` / `not is_empty` — see the `business-rules-reference` and `txscript-reference` packs.

---

## 15. Hook Execution Order

### On Document Open/Update (initialize, started, updated)

Same shape in both baselines; validation is native Rules, which run after the hooks rather than as hooks.

Shipped names end in ` (CIB)` and the IDs in brackets below are source-org IDs; a deployed org has its own.

```
1. MDH - Main (CIB)                       no run_after
1. Metadata Propagator (CIB)              no run_after — initialize, started only
1. Get Barcodes - KSeF PL (CIB)           no run_after — [2.0] initialize only, PL queue only
   ├── 2. MDH - Payment Terms (CIB)       (run_after: MDH - Main)
   ├── 2. MDH - Tax Codes (CIB)           (run_after: MDH - Main) — line-level and country queues only
   ├── 2. MDH - Coupa Invoice Check (CIB) (run_after: MDH - Main)
   └── 2. Duplicate Handling (CIB)        (run_after: MDH - Main) — also on confirm, export, status change
--- then, on validation ---
3. Native Rules (48 baseline + [2.0] KSeF Offline on PL)
--- on status change (confirmed / exported) ---
4. Supplier / Customer / Tax Coding Memorization (CIB) — skip automated documents
5. Coupa E-Invoicing Status Sync (CIB)    [2.0] country queues; …- Inbox (CIB) on the Inbox
```

**[2.0]** `Coupa E-Invoicing` is not in this chain — it runs on `upload.created`, before an annotation exists.

### On Export

**[1.x]**

```
1. Export Mapping [418429 or 419136] (per queue)
2. Create Draft [418430] (run_after: both 418429 and 419136)
3. Create Draft - parse response [418431]
4a. Attach Image Scan [418432] (parallel, gated)
4b. Attach Rossum URL [418434] (parallel, gated)
5a. Attach Image Scan - parse response [418433]
5b. Attach Rossum URL - parse response [418435]
6. Submit document [418436] (lower-case d in the shipped name; waits for 5a+5b, gated)
7. Submit Document - parse response [418437]
8. Handle Coupa Responses [418438]
```

**[2.0]** — strictly serial, no parse-response hooks:

```
1. Export Mapping - Line Level [1123383] or - Header Level [1123394]  (per queue)
2. Export Pipeline - 1. Create Draft [1157241]               (run_after: both mapping hooks)
3. Export Pipeline - 2. Attach Image Scan [1342015]
4. Export Pipeline - 3. Attach Rossum URL [1342016]
5. Export Pipeline - 4. Attach Supplementary Files [1342017]
6. Export Pipeline - 5. Submit Document [1342018]            (gated on api3_status_code and sf_submit_for_approval)
7. Handle Coupa Responses [1123389]
```

---

## 16. Required OAuth Scopes

Coupa keys a scope to the API's **owning domain**, not to the object name — `tax_codes` needs `common.read`, `tax_registrations` needs `invoice.read`. Guessing `core.tax_code.read` from the endpoint name produces a credential that fails at run time.

| Scope | Unlocks |
|---|---|
| `core.accounting.read` | account_types |
| `core.common.read` | addresses, lookup_values, payment_terms, tax_codes, uoms |
| `core.contract.read` | contracts |
| `core.invoice.create` | invoice creation (Coupa draft/submit flow) |
| `core.invoice.read` | tax_registrations, invoice duplicate check, **[2.0]** e-invoicing status sync |
| `core.invoice.write` | invoice export (`POST api/invoices`), **[2.0]** e-invoicing status sync |
| `core.purchase_order.read` | purchase_orders, purchase_order_lines |
| `core.supplier.read` | suppliers, remit_to_addresses |

**[2.0]** This list ships with the release as `deploy/required_scopes.json`, generated from the hooks in that release, and the init script checks the customer's credential against it before deploying. Treat that file as authoritative over this table.

---

## 17. Troubleshooting Guide

### Export Fails with Error

1. **[1.x]** Check `api1_status_code` — if not 201, draft creation failed; read `api1_response_body` for the Coupa error.
   **[2.0]** Those fields no longer exist. Read the response from `document_relations`, or from the `Export Pipeline - 1. Create Draft` hook log.
2. **[2.0]** If *no* export hook ran at all, check the pipeline hooks exist: a deploy into an organisation group with `maximum_hook_timeout` < 360s rejects all five and still reports success (see 7.1).
3. Common causes:
   - Missing required fields (supplier, currency, at least one line item)
   - Invalid field values (wrong tax code, invalid date format)
   - Coupa validation errors (duplicate invoice number for supplier)

### Matching Not Working

1. Verify the Data Storage collection has data: use `data_storage_find` or `data_storage_aggregate` with `[{"$sample": {"size": 1}}]`
2. Check the import hook is running (cron schedule active, no errors in hook logs). A run that ends `completed` with no `Imported N records` line imported nothing — usually an MDH operation-log watermark left by an earlier deploy into the same org; see 4.1
3. Verify field values being sent to MDH queries (check the `{field_id}` placeholders)
4. For fuzzy search: ensure Atlas Search indexes exist on the collection
5. For memorization: check the memorization collection has records

### Document Always Drafted

Check submission conditions:
1. `enforce_draft` must be `"No"`
2. `document_type` must not be `"credit_note"`
3. Must be PO-backed (`po_backed == "true"`) or contract-backed (`backing_document == "contract"`)
4. For line-level and PO-backed: `fully_tax_coded` must be `"true"`. The formula is (`po_backed` and `fully_tax_coded`) or contract, so contract-backed documents skip the tax-coding check
5. Automation blockers do not affect submission. **[2.0]** Check `api3_status_code` instead: a failed Attach Rossum URL stage blocks submit silently (see 7.2)

### Coupa Total Mismatch

The `inv_total_issue_tag` fires when `coupa_total_calculated != amount_total`. This means Coupa's backward calculation from exported data produces a different total than the document. Common causes:
- Rounding differences on line items
- Charges calculation differences
- Tax calculation differences (especially with multiple tax rates)

---

## 18. Quick Reference — All Datasets

| Dataset | Source | Used By | Records |
|---------|--------|---------|---------|
| `suppliers_test` | Coupa Import | MDH Supplier matching | Suppliers |
| `account_types_test` | Coupa Import | MDH Customer matching, PO line account resolution | Entities/CoA |
| `purchase_orders_test` | Coupa Import | MDH PO Header matching, Blanket PO | PO headers |
| `purchase_order_lines_test` | Coupa Import | MDH PO Line matching, Blanket PO | PO lines |
| `lookup_values_test` | Coupa Import | Not used in CIB (for custom account coding) | Billing segments |
| `tax_codes_test` | Coupa Import | MDH Tax Code matching | Tax codes |
| `uoms_test` | Coupa Import | Nothing reads it — `MDH - Main` uses the `uom` object embedded in the PO lines | Units of measure |
| `tax_registrations_test` | Coupa Import | MDH Tax Registration matching | Tax registrations |
| `addresses_test` | Coupa Import | MDH Ship-To matching | Addresses |
| `payment_terms_test` | Coupa Import | MDH Payment Terms matching, EPD matching | Payment terms |
| `suppliers_remit_to_addresses_test` | Coupa Import | Not used in CIB (for custom Remit-To) | Remit-to addresses |
| `contracts_test` | Coupa Import | MDH Contract matching | Contracts |
| `_supplier_memorization_test` | Memorization hook | MDH Supplier matching (query 2) | Supplier selections |
| `_customer_memorization_test` | Memorization hook | MDH Customer matching (query 2) | Customer selections |
| `_tax_code_memorization` | Memorization hook | MDH Tax Code matching (memorization stage) | Tax code selections |
| `_cib_deploy_manifest` (optional) | Deployment tooling, not the release or the deploy tool | Identifying the deployed release (`cib_version`, run id, created objects) | 1 document |

---

## 19. Quick Reference — All Hooks

Both release trees hold **33 hook objects**. The 1.x tree's 33rd is the inactive `Export Pipeline - Request Processor` [1090218], an early draft of the 2.0 pipeline, which the deploy tool does not create: a deployed 1.x org has **32** hooks, all active. All 33 are active in 2.0.

The IDs below are **source-org IDs** of the release; a deployed organisation gets new ones. Find hooks by name.

### 19.1 CIB 2.0

| ID | Name | Type | Event | Queues |
|----|------|------|-------|--------|
| 1341764 | Coupa Master Data Import - Suppliers (CIB) | job | scheduled/manual | all AP |
| 1341765 | Coupa Master Data Import - Account Types (CIB) | job | scheduled/manual | all AP |
| 1341766 | Coupa Master Data Import - Lookup values (CIB) | job | scheduled/manual | all AP |
| 1341767 | Coupa Master Data Import - Purchase Orders (CIB) | job | scheduled/manual | — |
| 1341768 | Coupa Master Data Import - Tax Codes (CIB) | job | scheduled/manual | all AP |
| 1341769 | Coupa Master Data Import - Tax Registrations (CIB) | job | scheduled/manual | all AP |
| 1341770 | Coupa Master Data Import - Purchase Order Lines (CIB) | job | scheduled/manual | — |
| 1341771 | Coupa Master Data Import - Addresses (CIB) | job | scheduled/manual | all AP |
| 1341772 | Coupa Master Data Import - Payment Terms (CIB) | job | scheduled/manual | all AP |
| 1341773 | Coupa Master Data Import - Remit To Addresses (CIB) | job | scheduled/manual | all AP |
| 1341774 | Coupa Master Data Import - Contracts (CIB) | job | scheduled/manual | — |
| 1341763 | Coupa Master Data Import - Units of Measure (CIB) | job | scheduled/manual | all AP |
| 1123384 | MDH - Main (CIB) | webhook | init/started/updated | all AP |
| 1123400 | MDH - Payment Terms (CIB) | webhook | init/started/updated | all AP |
| 1123401 | MDH - Tax Codes (CIB) | webhook | init/started/updated | line-level + 4 country (not header) |
| 1123402 | MDH - Coupa Invoice Check (CIB) | webhook | init/started/updated | all AP |
| 1123399 | Duplicate Handling (CIB) | webhook | init/started/updated/confirm/export, status changed | all AP |
| 1123391 | Metadata Propagator (CIB) | function | init/started | all AP |
| 1123404 | Supplier memorization (CIB) | function | annotation_status.changed | all AP |
| 1123405 | Customer memorization (CIB) | function | annotation_status.changed | all AP |
| 1123407 | Tax Coding Memorization (CIB) | function | annotation_status.changed | all AP |
| 1123383 | Export Mapping - LIne Level Taxation (CIB) | webhook | export | line-level + 4 country |
| 1123394 | Export Mapping - Header Level Taxation (CIB) | webhook | export | header-level |
| 1157241 | Export Pipeline - 1. Create Draft (CIB) | function | export | all AP |
| 1342015 | Export Pipeline - 2. Attach Image Scan (CIB) | function | export | all AP |
| 1342016 | Export Pipeline - 3. Attach Rossum URL (CIB) | function | export | all AP |
| 1342017 | Export Pipeline - 4. Attach Supplementary Files (CIB) | function | export | all AP |
| 1342018 | Export Pipeline - 5. Submit Document (CIB) | function | export | all AP |
| 1123389 | Handle Coupa Responses (CIB) | function | export | all AP |
| 1170832 | Coupa E-Invoicing | webhook | upload.created | E-invoicing Inbox |
| 1309898 | Coupa E-Invoicing Status Sync (CIB) | function | annotation_status.changed | 4 country queues |
| 1309899 | Coupa E-Invoicing Status Sync - Inbox (CIB) | function | annotation_status.changed | E-invoicing Inbox |
| 1157629 | Get Barcodes - KSeF PL (CIB) | function | init | AP Documents - PL |

"all AP" = the six AP queues (baseline line-level and header-level, plus BE / PL / FR / DE) — never the Inbox. The three imports with no queue binding write only to Data Storage; that is not a fault.

**Note the misspelling** in `Export Mapping - LIne Level Taxation (CIB)` — it is in the shipped object name, so match on it when searching.

### 19.2 CIB 1.x

Events and queue bindings. The imports subscribe to `invocation.scheduled` + `invocation.manual`; Contracts, Purchase Orders and Purchase Order Lines are bound to no queue, the other nine to both.

| ID | Name | Type | Event | Queues |
|----|------|------|-------|--------|
| 339158 | Coupa Webhook Import - Suppliers (CIB) | webhook | scheduled/manual | Both |
| 339902 | Coupa Webhook Import - Account Types (CIB) | webhook | scheduled/manual | Both |
| 339970 | Coupa Webhook Import - Purchase Orders (CIB) | webhook | scheduled/manual | — |
| 340003 | Coupa Webhook Import - Tax Codes (CIB) | webhook | scheduled/manual | Both |
| 342923 | Coupa Webhook Import - Lookup values (CIB) | webhook | scheduled/manual | Both |
| 342955 | Coupa Webhook Import - Units of Measure (CIB) | webhook | scheduled/manual | Both |
| 421929 | Coupa Webhook Import - Tax Registrations (CIB) | webhook | scheduled/manual | Both |
| 421930 | Coupa Webhook Import - Addresses (CIB) | webhook | scheduled/manual | Both |
| 421931 | Coupa Webhook Import - Purchase Order Lines (CIB) | webhook | scheduled/manual | — |
| 422850 | Coupa Webhook Import - Payment Terms (CIB) | webhook | scheduled/manual | Both |
| 694975 | Coupa Webhook Import - Remit To Addresses (CIB) | webhook | scheduled/manual | Both |
| 784911 | Coupa Webhook Import - Contracts (CIB) | webhook | scheduled/manual | — |
| 343051 | MDH - Main (CIB) | webhook | init/started/updated | Both |
| 453520 | MDH - Payment Terms (CIB) | webhook | init/started/updated | Both |
| 540278 | MDH - Tax Codes (CIB) | webhook | init/started/updated | Line Level only |
| 735955 | MDH - Coupa Invoice Check (CIB) | webhook | init/started/updated | Both |
| 444021 | Duplicate Handling (CIB) | webhook | init/started/updated/confirm/export, status changed | Both |
| 735954 | Metadata Propagator (CIB) | function | init/started | Both |
| 693315 | Supplier memorization (CIB) | function | annotation_status.changed | Both |
| 693316 | Customer memorization (CIB) | function | annotation_status.changed | Both |
| 747526 | Tax Coding Memorization (CIB) | function | annotation_status.changed | Both |
| 418429 | Export Mapping - LIne Level Taxation (CIB) | webhook | export | Line Level only |
| 419136 | Export Mapping - Header Level Taxation (CIB) | webhook | export | Header Level only |
| 418430 | Create Draft (CIB) | webhook | export | Both |
| 418431 | Create Draft - parse response (CIB) | webhook | export | Both |
| 418432 | Attach Image Scan (CIB) | webhook | export | Both |
| 418433 | Attach Image Scan - parse response (CIB) | webhook | export | Both |
| 418434 | Attach Rossum URL (CIB) | webhook | export | Both |
| 418435 | Attach Rossum URL - parse response (CIB) | webhook | export | Both |
| 418436 | Submit document (CIB) | webhook | export | Both |
| 418437 | Submit Document - parse response (CIB) | webhook | export | Both |
| 418438 | Handle Coupa Responses (CIB) | function | export | Both |
| 1090218 | Export Pipeline - Request Processor | function | export | **inactive; release tree only, not deployed** |

There are **no** `Business Rules - …` or `ShowHide Fields` hooks in any released CIB. Validation and field visibility are native Rules in both baselines (see 6 and 9.2); a deployment that has such hooks predates the public release or is a customer customization.
