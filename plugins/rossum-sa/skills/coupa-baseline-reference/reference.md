# Coupa Integration Baseline (CIB) — Detailed Reference

## 1. Architecture Overview

The Rossum-Coupa integration has three layers:

1. **Technical Integration Components** — Coupa Import, Export Pipeline, MDH, Formula Fields, Business Rules
2. **Coupa Integration Baseline (CIB)** — Pre-configured business logic (this skill)
3. **Customized Integration** — Customer-specific enhancements on top of CIB

### Data Flow

```
Coupa Master Data ──(scheduled import)──> Rossum Data Storage
                                              │
Document arrives (email/upload/API) ──> Rossum AI Extraction
                                              │
                                        Formula Fields (calculations, transformations)
                                              │
                                        Master Data Hub (matching & enrichment)
                                              │
                                        Business Rules (validation & messages)
                                              │
                                        User Review (if needed)
                                              │
                                        Export Pipeline ──> Coupa Invoice/Credit Note
```

### Two Queue Variants

CIB ships with two queues in one workspace:

| Queue | Taxation Model | Typical Region | Key Differences |
|-------|---------------|----------------|-----------------|
| **Line Level Taxation** (ID: 1260377) | Tax codes per line item | Europe, APAC | Has `fully_tax_coded` check, per-line tax rates/amounts, charges table with tax per charge |
| **Header Level Taxation** (ID: 1260388) | Single tax at header | US | No tax codes, shipping/handling/misc charges as separate fields, simpler submission logic |

### Document Workflows

- **Non-PO (Draft)**: No PO number — always drafted in Coupa. Custom account coding can enable submission.
- **PO-backed (Submit or Draft)**: PO matched — submitted if all conditions met (tax coded, no mismatches, not credit note, enforce_draft=No).
- **Contract-backed (Submit or Draft)**: Contract matched with default billing — submitted if conditions met.
- **Credit Notes**: Always drafted in Coupa regardless of other conditions.

---

## 2. Schema Structure

Both schemas share a common structure with sections. All field IDs listed below are the `schema_id` values used in formulas, MDH mappings, and export templates.

### 2.1 General Information Section (`basic_info_section`)

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
| `notes` | string | Captured | Notes field |

### 2.2 Payment Terms Section (`payment_info_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `date_due` | date | Captured | Due date |
| `terms` | string | Captured | Payment terms text |
| `terms_calculated` | string | Formula | Days from issue to due, or parsed from `terms`, or supplier default |
| `payment_terms_match` | enum | MDH | Matched Coupa payment term ID |
| `payment_terms_days_match` | string | MDH | Days for net payment from matched term |
| `payment_terms_code_match` | string | MDH | Payment term code from matched term |
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

### 2.3 Early Payment Discount (EPD) Section

EPD is **disabled by default** (requires Reasoning Fields / AI credits to enable).

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `epd_info` | string | Reasoning | JSON with `is_epd_offered`, `epd_rate`, `epd_days` |
| `epd_detected` | string | Formula | `"true"` if EPD found in `epd_info` JSON |
| `epd_rate` | number | Formula | Discount percentage from `epd_info` |
| `epd_days` | number | Formula | Discount days from `epd_info` |
| `epd_amount` | number | Formula | `(amount_total / 100) * epd_rate` |
| `epd_expires` | number | Formula | Days until discount expiry: `(date_issue + epd_days) - today` |
| `epd_payment_terms_match` | enum | MDH | Matched Coupa payment term with matching discount rate + days |
| `epd_payment_terms_code_match` | string | MDH | EPD payment term code |
| `epd_payment_terms_rate_match` | string | MDH | EPD discount rate from matched term |
| `epd_payment_terms_days_match` | string | MDH | EPD discount days from matched term |
| `epd_tag` | string | Formula | `"discount_terms"` if EPD expires > 0 and match found |

**EPD Export Logic**: If `epd_expires > 0` and a matching Coupa payment term exists, the EPD term is exported instead of the standard payment term. The `epd_tag` Coupa tag is also added.

### 2.4 Customer (Chart of Accounts) Section (`recipient_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `recipient_name` | string | Captured | Customer/entity name |
| `recipient_address` | string | Captured | Customer address |
| `recipient_tax_id` | string | Captured | Customer VAT/tax number |
| `recipient_tax_id_normalized` | string | Formula | `re.sub(r'\W+', '', recipient_tax_id)` |
| `recipient_search` | string | Manual | Manual search field for customer lookup |
| `recipient_match` | enum | MDH | Matched account type (entity) ID |
| `recipient_name_match` | string | MDH | Name of matched account type |
| `recipient_primary_address_match` | string | MDH | Primary address ID of matched entity |
| `recipient_entity_country_code_match` | string | MDH | Country code of matched entity |
| `recipient_tax_registration_match` | enum | MDH | Matched tax registration number |
| `recipient_country_code_match` | string | MDH | Country code from tax registration |
| `recipient_ship_to_match` | enum | MDH | Matched ship-to address |
| `recipient_export` | string | Formula | PO line recipient > contract customer > document customer |
| `recipient_name_export` | string | Formula | Same priority chain for name |

**Customer Matching MDH Query Cascade** (9 queries in `account_types_test`):
1. Manual search (user types in `recipient_search`)
2. Memorized value (lookup in `_customer_memorization_test` by name+address)
3. Exact match on VAT number (`primary-address.vat-number` regex with optional country prefix)
4. Exact match on entity name (case-insensitive)
5. Fuzzy match on entity name (Atlas Search, maxEdits:1, score normalized, threshold bands 0.95→0.6)
6. Exact match on primary address name
7. Fuzzy match on primary address name (score normalized, threshold > 0.8)
8. Fuzzy match on name + address fields (compound: must name boost:2, should address fields boost:0.5)
9. Return all entities (fallback when nothing matches)

**Header-level taxation differences**: No `recipient_search` field. No memorization query. Matching starts with exact VAT match.

### 2.5 Supplier Section (`sender_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `sender_name` | string | Captured | Supplier name |
| `sender_address` | string | Captured | Supplier address |
| `sender_tax_id` | string | Captured | Supplier VAT/tax number |
| `sender_tax_id_normalized` | string | Formula | `re.sub(r'\W+', '', sender_tax_id)` |
| `sender_search` | string | Manual | Manual search for supplier lookup |
| `sender_match` | enum | MDH | Matched supplier ID |
| `sender_name_match` | string | MDH | Supplier name |
| `sender_display_name_match` | string | MDH | Supplier display name |
| `sender_number_match` | string | MDH | Supplier number |
| `sender_country_code_match` | string | MDH | Country code from supplier primary address |
| `sender_payment_days_match` | string | MDH | Default payment days from supplier |
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
5. Exact match on display name (case-insensitive)
6. Fuzzy match on display name (Atlas Search, maxEdits:1, score normalized, threshold bands)
7. Fuzzy match on name + address fields (compound query)

**Header-level taxation differences**: No `sender_search` field. No memorization. Matching starts directly with tax-id exact match.

### 2.6 Bank Details Section (`bank_details_section`)

| Field ID | Type | Purpose |
|----------|------|---------|
| `iban` | string | IBAN |
| `bic` | string | BIC/SWIFT |
| `account_num` | string | Account number |
| `bank_num` | string | Bank code |

**Note**: CIB does NOT implement bank detail matching or Remit-To logic. This varies by customer and is always a custom implementation.

### 2.7 Credit Note Section (`credit_note_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `original_invoice_id` | string | Captured | Original invoice number for credit note |
| `original_invoice_date` | date | Captured | Original invoice date |
| `credit_notes_amounts` | enum | Captured | `"negative"` or `"positive"` — how amounts appear on the document |

**Credit Note Sign Logic**: The `credit_notes_amounts` field tells the system how to interpret signs. The `set_sign` / `set_value_sign_line_items` helper function in amount formulas adjusts values based on:
- Document type (invoice vs credit note)
- Whether header total is positive
- Whether all line values are positive or negative
- The `credit_notes_amounts` setting

### 2.8 Taxes & Amounts Section (`amounts_section`)

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
| `currency` | string | Captured | Currency code |
| `currency_upper` | string | Formula | `currency.upper()` |
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
| Subtotal calculation | `amount_total_base` or `amount_total - tax` | `amount_total - charges - tax` |
| Tax codes | Per-line + header | None |

### 2.9 Charges

#### Line Level Taxation — Charges Table (`charges`)

| Field ID | Type | Source |
|----------|------|--------|
| `charge_description` | string | Captured |
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

### 2.10 Purchase Order Section (`po_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `order_id` | string | Captured | PO number from document |
| `order_id_calculated` | string | Formula | `order_id` or `order_blanket_match` |
| `order_header_match` | enum | MDH | Matched PO header (by `po-number`) |
| `order_header_status_match` | string | MDH | PO status |
| `order_header_ship_to_addr_match` | string | MDH | Ship-to address ID from PO |
| `order_blanket_match` | enum | MDH | Blanket PO selection (for POs not on document) |
| `order_item_match` | enum | MDH | Matched PO line (header-level, for no-line-items case) |
| `po_line_number_match` | string | MDH | PO line number |
| `po_line_type_match` | string | MDH | `OrderQuantityLine` or `OrderAmountLine` |
| `po_line_status_match` | string | MDH | PO line status |
| `po_line_uom_match` | string | MDH | UOM code from PO line |
| `po_line_total_match` | string | MDH | PO line total |
| `po_line_price_match` | string | MDH | PO line price |
| `po_line_description_match` | string | MDH | PO line description |
| `po_line_quantity_match` | string | MDH | PO line quantity |
| `po_line_recipient_match` | string | MDH | Customer from PO line account |
| `po_line_supplier_match` | string | MDH | Supplier from PO line |
| `po_line_currency_match` | string | MDH | Currency from PO line |
| `po_backed` | string | Formula | `"true"` if `order_header_match` or any `item_order_header_match` |
| `po_closed` | string | Formula | `"true"` if PO status in `soft_closed, closed, cancelled` |
| `backing_document` | string | Formula | `"contract"` if no PO and contract approved, else `"po"` |

**Blanket PO Matching** (config "10. Order Blanket Header"):
- Looks up PO lines where: account-type matches `recipient_match` AND supplier matches `sender_match`
- Joins to `purchase_orders_test` to get PO header
- Filters to POs with status `"issued"`
- Groups by order-header to show unique POs for selection

### 2.11 Contract Section (`contract_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `contract_number` | string | Captured | Contract number from document |
| `contract_number_normalized` | string | Formula | `re.sub(r'[^a-zA-Z0-9]', '', contract_number)` |
| `contract_match` | enum | MDH | Matched contract number |
| `contract_status_match` | string | MDH | Contract status |
| `contract_supplier_match` | string | MDH | Supplier ID from contract |
| `contract_supplier_name` | string | MDH | Supplier name from contract |
| `contract_customer_match` | string | MDH | Customer (account-type) ID from contract default-account |
| `contract_customer_name` | string | MDH | Customer name from contract |

**Backing Document Logic** (`backing_document.py`):
```python
# Contract-backed only when: no PO matched AND contract status is "approved"
'contract' if (is_empty(field.order_header_match) and is_empty(field.order_blanket_match)
               and default_to(field.contract_status_match, '') == 'approved') else 'po'
```

### 2.12 Service Period Section

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `service_period` | string | Captured | Raw service period text |
| `service_period_dates_parsed` | string | Reasoning | JSON `{"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD"}` |
| `sp_date_start` / `sp_date_end` | date | Captured | Start/end dates |
| `service_period_start_export` / `service_period_end_export` | string | Formula | ISO date from parsed JSON or captured dates |

Also available at **line item level** with `service_period_item`, `sp_item_start_date_export`, `sp_item_end_date_export`.

PO line service period dates are matched from `period.start-date` and `period.end-date` fields and normalized for comparison.

### 2.13 Line Items Tuple (`line_items`)

Each line item has these fields (showing key ones):

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `item_description` | string | Captured | Line item description |
| `item_description_export` | string | Formula | `item_description` as-is |
| `item_description_calculated` | string | Formula | PO line description if PO closed, else PO match description, else captured (LINE LEVEL ONLY) |
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
| `item_po_line_number_match` | string | MDH | PO line number |
| `item_po_line_type_match` | string | MDH | `OrderQuantityLine` or `OrderAmountLine` |
| `item_po_line_uom_match` | string | MDH | UOM from matched PO line |
| `item_po_line_total_match` | string | MDH | PO line total |
| `item_po_line_price_match` | string | MDH | PO line price |
| `item_po_line_description_match` | string | MDH | PO line description |
| `item_po_line_quantity_match` | string | MDH | PO line quantity |
| `item_po_line_status_match` | string | MDH | PO line status |
| `item_po_line_recipient_match` | string | MDH | Customer from PO line |
| `item_po_line_supplier_match` | string | MDH | Supplier from PO line |
| `item_po_line_currency_match` | string | MDH | Currency from PO line |
| `item_po_line_sp_start_date_match` | string | MDH | PO line service period start |
| `item_po_line_sp_end_date_match` | string | MDH | PO line service period end |
| `item_line_type` | string | Formula | `InvoiceAmountLine` if PO line is `OrderAmountLine`, else `InvoiceQuantityLine` |
| `item_quantity_export` | string | Formula | Rounded qty if `InvoiceQuantityLine`, else empty |
| `item_price_export` | number | Formula | Unit price for qty lines, total for amount lines |
| `item_uom_export` | string | Formula | PO line UOM or default `"EA"` |
| `item_net_total_coupa` | number | Formula | Coupa backward calc: `qty * price` (or just price for amount lines) |
| `item_order_item_number_export` | string | Formula | PO line number if status allows |
| `item_recipient_mismatch_tag` | string | Formula | `"recipient_mismatch"` if PO customer != document customer |
| `item_supplier_mismatch_tag` | string | Formula | `"supplier_mismatch"` if PO supplier != document supplier |
| `item_inactive_po_line_tag` | string | Formula | `"po_line_inactive"` if PO line status not in active set |
| `item_po_closed` | string | Formula | `"true"` if item's PO status in closed/cancelled |

### 2.14 Coupa Technical Fields Section (`coupa_section`)

| Field ID | Type | Source | Purpose |
|----------|------|--------|---------|
| `sf_submit_for_approval` | string | Formula | `"Yes"` or `"No"` — dynamic submission decision |
| `enforce_draft` | enum | Manual | `"Yes"` or `"No"` — user can force draft |
| `rossum_tag` | string | Formula | `"rossum_submit"` or `"rossum_draft"` |
| `enforced_draft_tag` | string | Formula | `"enforced_draft"` if enforce_draft=Yes |
| `line_level_taxation` | string | Default | `"True"` (line level) or `"False"` (header level) |
| `line_items_present` | string | Formula | `"true"` if line_items count > 0 |
| `fully_tax_coded` | string | Formula | `"true"` if tax_code_match set OR all item tax codes set (LINE LEVEL ONLY) |
| `quantity_header_calculated` | number | Formula | Always `1` (fallback line quantity) |
| `recipient_mismatch_tag` | string | Formula | Aggregates `item_recipient_mismatch_tag` from all lines |
| `supplier_mismatch_tag` | string | Formula | Aggregates `item_supplier_mismatch_tag` from all lines |
| `inactive_po_line_tag` | string | Formula | `"po_line_inactive"` if any PO line inactive |
| `inv_total_issue_tag` | string | Formula | `"inv_total_issue"` if Coupa total != document total |
| `duplicate_invoice_statuses` | enum | MDH | Coupa invoice statuses for duplicate check |
| `coupa_invoices_statuses` | string | Formula | Unique comma-separated statuses from duplicate check |
| `is_duplicate` | string | Formula | From duplicate handling extension |
| `charges_tag` | string | Formula | Tag for charges presence |
| `coupa_invoice_id` | string | Export | Coupa invoice ID from API response |
| `api1_status_code` | string | Export | Create Draft HTTP status |
| `api1_response_body` | string | Export | Create Draft response |
| `api2_status_code` | string | Export | Attach Image Scan HTTP status |
| `api2_response_body` | string | Export | Attach Image response |
| `api2_gate` | string | Formula | Non-empty if `api1_status_code == "201"` |
| `api3_status_code` | string | Export | Attach Rossum URL HTTP status |
| `api3_response_body` | string | Export | Attach URL response |
| `api3_url` | string | Formula | `{base_url}api/invoices/{coupa_invoice_id}/attachments` |
| `api4_status_code` | string | Export | Submit HTTP status |
| `api4_response_body` | string | Export | Submit response |
| `api4_gate` | string | Formula | Non-empty if `api1_status_code == "201"` AND `sf_submit_for_approval == "Yes"` |
| `api4_url` | string | Formula | `{base_url}api/invoices/{coupa_invoice_id}/submit` |
| `coupa_api_base_url` | string | Formula | Coupa instance URL (e.g., `https://customer.coupacloud.com/`) |
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
- Header-level fields `order_item_match` and `tax_code_match` become visible (ShowHide hooks)
- Export uses header fields (`quantity_export`, `price_export`, `uom_export`, `line_type`) to create a single fallback line
- `quantity_header_calculated` = 1, `price_export` = `amount_total_base_calculated`

---

## 3. Data Import — Coupa to Rossum

### 3.1 Import Webhooks

All imports use the **Coupa Import Extension** (`coupalink-import.rossum-ext.app`). They are scheduled webhooks that:
1. Authenticate via OAuth client credentials
2. Call a Coupa API endpoint with field selection
3. Store results in a Rossum Data Storage collection
4. Support differential sync (only new/updated records since last run)

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

### 3.2 Import Configuration Pattern

Each import webhook JSON has this structure in `settings.third_party_service_settings`:

```json
{
  "coupa_api_url": "https://customer.coupacloud.com/",
  "dataset_name": "suppliers_test",
  "endpoint": "api/suppliers",
  "fields": ["id", "name", "display-name", "number", "status", ...],
  "nested_fields": {"primary-address": ["id", "name", "street1", ...]},
  "order_by": "updated_at",
  "auth": {
    "client_id": "...",
    "scopes": "core.supplier.read"
  }
}
```

**Key configuration properties**:
- `fields` — top-level fields to replicate (limits response size)
- `nested_fields` — nested object fields to include
- `order_by` — typically `updated_at` for differential sync
- `auth.scopes` — OAuth scope needed for this endpoint

### 3.3 Dataset Naming Convention

All CIB datasets use the `_test` suffix by default (e.g., `suppliers_test`). This is because CIB is always deployed first against a Coupa TEST instance. When promoting to production:
- Create new datasets without `_test` suffix (e.g., `suppliers`)
- Update all MDH configurations, memorization hooks, and import webhooks to reference the new dataset names
- Or keep `_test` suffix and just point the import at the production Coupa URL

### 3.4 Memorization Collections

These are not imported from Coupa but built up by Rossum as users process documents:

| Collection | Created By | Natural Key | Stored Fields |
|-----------|-----------|-------------|---------------|
| `_supplier_memorization_test` | Supplier Memorization hook | `sender_name` + `sender_address` | `sender_match` |
| `_customer_memorization_test` | Customer Memorization hook | `recipient_name` + `recipient_address` | `recipient_match` |
| `_tax_code_memorization` | Tax Coding Memorization hook | `sender_match` + `recipient_match` + `item_description` | `item_tax_code_match`, `description_export`, `tax_code_match` |

> A filling collection is **not** evidence that memorization works — a key that is too specific mints a new row per document and is never recalled. Run the reuse-rate check in [7.1.1 Health check](#711-health-check-is-the-memorization-collection-actually-being-reused) before trusting any of these.

---

## 4. Master Data Hub — Matching & Enrichment

The MDH is the core engine that connects extracted document data to Coupa master data. CIB has **4 MDH hooks** with **15 match configurations**.

### 4.1 Hook Execution Order

```
MDH - Main [343051]  (events: initialize, started, updated)
    ├── MDH - Payment Terms [453520]  (run_after: 343051)
    ├── MDH - Tax Codes [540278]      (run_after: 343051) — LINE LEVEL ONLY
    └── MDH - Coupa Invoice Check [735955] (run_after: 343051)
```

### 4.2 MDH - Main (10 configurations)

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
- 7-query cascade: manual search → memorization → exact tax-id → exact primary-address VAT → exact display name → fuzzy display name → fuzzy name+address
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

### 4.3 MDH - Payment Terms (2 configurations)

**Config 1: Payment Terms** → `payment_terms_match`
- Dataset: `payment_terms_test`
- 2-query cascade: exact match on `days-for-net-payment` + type `DaysAfterNetPaymentTerm` + active + no discount days → all active DaysAfterNetPaymentTerm without discount
- Maps: `payment_terms_days_match`, `payment_terms_code_match`

**Config 2: Early Payment Discount Terms** → `epd_payment_terms_match`
- Dataset: `payment_terms_test`
- 2-query cascade: exact match on `discount-rate` + `days-for-discount-payment` → all with discount days
- Condition: `'{epd_detected}' == 'true'`
- Maps: `epd_payment_terms_days_match`, `epd_payment_terms_code_match`, `epd_payment_terms_rate_match`

### 4.4 MDH - Tax Codes (2 configurations) — LINE LEVEL ONLY

**Config 1: Tax Codes (line-items)** → `item_tax_code_match`
- Dataset: `tax_codes_test` + `_tax_code_memorization`
- Complex single-query aggregation with:
  1. Memorization lookup via `$unionWith` on `_tax_code_memorization` (matches `sender_match` + `recipient_match` + `item_description`)
  2. Three-tier country matching via `$facet`: recipient country code → entity country code → any country
  3. Deduplication and priority sorting

**Config 2: Tax Codes (headers)** → `tax_code_match`
- Same pattern as line-items but uses `description_export` and `tax_rate_calculated` instead of `item_description` and `item_rate_calculated`

### 4.5 MDH - Coupa Invoice Check (1 configuration)

**Config: Coupa Invoice Existence Check** → `duplicate_invoice_statuses`
- Source: **External API** (not dataset — live Coupa call)
- Authentication: OAuth client credentials with `core.invoice.read` scope
- Query: GET `/api/invoices?supplier_id={sender_export}&invoice_number={document_id_manual}&fields=["id","status"]`
- Returns list of existing Coupa invoice statuses for the same supplier + invoice number
- Used for duplicate detection in Coupa

---

## 5. Business Rules Validation

Business rules are configured as **Business Rules Validation** webhooks using the `business-rules-validation.rossum-ext.app` service.

### 5.1 Common Rules (both queues) — 33 rules

Key validation rules:

**Customer/Supplier Matching:**
- Warning if `recipient_match` empty — "Customer not matched"
- Warning if `sender_match` empty — "Supplier not matched"
- Warning if `recipient_mismatch_tag == "recipient_mismatch"` — "PO x Invoice customer mismatch"
- Warning if `supplier_mismatch_tag == "supplier_mismatch"` — "PO x Invoice supplier mismatch"

**Payment Terms:**
- Warning if `payment_terms_match` empty — "Payment terms not matched"
- Warning if `terms_calculated != payment_terms_days_match` — "Payment terms mismatch between document and match"
- Warning if `terms_calculated != sender_payment_days_match` — "Payment terms mismatch between document and supplier default"

**PO Matching:**
- Error if `po_closed == "true"` — "PO is closed" (blocks confirmation)
- Warning if `po_backed == "true"` and `order_item_match` empty and `line_items_present == "false"` — "PO line not matched"
- Warning if `po_backed == "true"` and any `item_order_item_match` empty — "PO line not matched on line item"
- Warning if `inactive_po_line_tag == "po_line_inactive"` — "Inactive PO line matched"

**Duplicate Detection:**
- Warning if `is_duplicate` not empty — Shows duplicate document info
- Warning if `coupa_invoices_statuses` not empty — "Invoice already exists in Coupa: {statuses}"

**Amounts:**
- Warning if `inv_total_issue_tag == "inv_total_issue"` — "Coupa total calculation mismatch"
- Warning if `currency_upper != po_line_currency_match` — "PO line currency mismatch"

**Dates:**
- Warning if `date_issue_manual` > 356 days ago — "Invoice date is more than 356 days in the past"
- Warning if `date_issue_manual` > 180 days in future — "Invoice date is more than 180 days in the future"

**Contract:**
- Warning if `contract_status_match != "approved"` and `contract_match` set — "Contract is not in approved status"

**Service Period:**
- Warning if item service period falls outside PO line service period

### 5.2 Line Level Taxation Rules — 8 additional rules

- Warning if `amount_total != amount_total_base_calculated + amount_total_tax_calculated` (tolerance check)
- Warning if sum of `item_total_base_calculated` != `amount_total_base_calculated` (with tolerance)
- Warning if `tax_code_match` empty and `line_items_present == "false"` — "Tax code not matched"
- Warning if any `item_tax_code_match` empty — "Tax code not matched on line item"
- Warning on currency mismatch per line item

### 5.3 Header Level Taxation Rules — 4 additional rules

- Warning if `amount_total != amount_total_base_calculated + amount_total_tax_calculated + charges_calculated`
- Warning if sum of `item_total_base_calculated` != `amount_total_base_calculated`

---

## 6. Export Pipeline — Rossum to Coupa

The export pipeline is a chain of webhooks that execute sequentially on the `export` event. It makes 4 Coupa API calls.

### 6.1 Pipeline Flow

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

**Note**: Attach Image Scan and Attach Rossum URL run in **parallel** (both depend on 418431). Submit waits for both to complete.

### 6.2 Gating Logic

- `api2_gate`: Non-empty if `api1_status_code == "201"` — gates API 2, 3
- `api4_gate`: Non-empty if `api1_status_code == "201"` AND `sf_submit_for_approval == "Yes"` — gates API 4

If draft creation fails (non-201), subsequent calls are skipped. If submission is not warranted, only the submit call is skipped.

### 6.3 Export Mapping Template (Line Level)

The Jinja template produces Coupa invoice JSON. Key structure:

```json
{
  "taggings": [{"name": "rossum_draft|rossum_submit"}, ...],
  "currency": {"code": "EUR"},
  "supplier": {"id": 12345},
  "document-type": "Invoice",
  "account-type": {"id": "entity_id"},
  "invoice-date": "2024-01-15",
  "invoice-number": "INV-001",
  "payment-term": {"code": "NET30"},
  "line-level-taxation": true,
  "invoice-charges": [{"shipping-amount": 10, "tax-line": {"amount": 1.9, "rate": 19}}],
  "invoice-lines": [
    {
      "uom": {"code": "EA"},
      "price": 100.00,
      "type": "InvoiceQuantityLine",
      "quantity": 2,
      "description": "Widget",
      "order-line-num": "1",
      "order-header-num": "PO-001",
      "tax-lines": [{"tax-code": {"id": 5}, "amount": 38.00, "rate": 19}]
    }
  ]
}
```

**Conditional elements in template:**
- `taggings`: Always includes `rossum_tag`. Adds `rossum_automated` if annotation was automated. Adds `enforced_draft_tag`, `inv_total_issue_tag`, `recipient_mismatch_tag`, `supplier_mismatch_tag`, `inactive_po_line_tag`, `charges_tag`, `epd_tag` when set.
- `ship-to-address`: Only if `recipient_ship_to_match` is set
- `contract`: Only if `backing_document == "contract"`
- `payment-term`: Only if `payment_terms_export` is set
- `requester-email`: From `requestor_email` or `requestor_email_calculated`
- Line items: Loop over `line_items` tuple, with fallback to header fields if no line items

### 6.4 Header Level Template Differences

- `line-level-taxation: false`
- Tax at header level: `"tax-lines": [{"amount": tax_amount}]` (no tax-code)
- Charges as separate fields: `"shipping-amount"`, `"handling-amount"`, `"misc-amount"` (no `invoice-charges`)
- No per-line tax-lines

### 6.5 OAuth Configuration

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

### 6.6 Handle Coupa Responses (Serverless Function)

The final step parses all 4 API responses and shows errors/warnings to the user:

```python
def handle_api_response(x, status_code, response_body, message, is_error):
    if int(status_code) < 400: return
    # Parse Coupa error JSON: {"errors": {"field": ["message1", "message2"]}}
    # Show as error (API 1 - blocks export) or warning (API 2-4)
```

- **API 1 failure** (Create Draft): Shows **error** — document moves to Failed Export
- **API 2-4 failure**: Shows **warning** — document moves to Exported but with visible warnings

---

## 7. Serverless Functions

### 7.1 Memorization Hooks

Three memorization hooks save user selections to Data Storage for future recall:

**Supplier Memorization** (hook 693315):
- Events: `annotation_content.user_update` (but code checks for confirmed/exported status)
- Collection: `_supplier_memorization_test`
- Saves: `sender_name`, `sender_address` → `sender_match`
- Natural key: `sender_name` + `sender_address` (first record wins for `sender_match`)

**Customer Memorization** (hook 693316):
- Same code as supplier, different settings
- Collection: `_customer_memorization_test`
- Saves: `recipient_name`, `recipient_address` → `recipient_match`

**Tax Coding Memorization** (hook 747526):
- Collection: `_tax_code_memorization`
- Saves: `sender_match`, `recipient_match`, `item_description`, `description_export`, `tax_code_match`, `item_tax_code_match`
- Uses `unwind` on `line_item` — creates separate records per line item
- Natural key: `sender_match` + `recipient_match` + `line_item.item_description`

### 7.1.1 Health check: is the memorization collection actually being reused?

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

### 7.2 ShowHide Fields

Two hooks control field visibility based on whether line items are present:

**Line Level** (hook 541028): Shows/hides `order_item_match` AND `tax_code_match`
**Header Level** (hook 541085): Shows/hides `order_item_match` only

When `line_items` multivalue is empty → show header-level PO line match (and tax code match for line-level)
When line items are present → hide those fields (matching happens per line item)

### 7.3 Metadata Propagator (hook 735954)

Sets two technical fields on `initialize` and `started` events:
- `rossum_annotation_link` = URL to the Rossum annotation
- `original_file_name` = name of the uploaded document file

---

## 8. Duplicate Detection

### 8.1 Rossum Duplicates (Duplicate Handling hook 444021)

Uses the `duplicate-finder.rossum-ext.app` extension:
- Searches by: `document_id_manual` + `sender_export`
- When duplicate found: `is_duplicate` field is populated
- Business rule shows warning with link to duplicate annotation

**Two detection methods:**
- **File-based**: Bitwise comparison — detects exact same file uploaded multiple times
- **Extracted data-based**: Same invoice number + same supplier = duplicate

### 8.2 Coupa Duplicates (MDH - Coupa Invoice Check hook 735955)

Live API call to Coupa on every document open/update:
- GET `/api/invoices?supplier_id={sender_export}&invoice_number={document_id_manual}`
- Returns statuses of any existing Coupa invoices with same supplier + number
- `duplicate_invoice_statuses` field shows results
- `coupa_invoices_statuses` formula joins unique statuses as comma-separated string
- Business rule warns: "Invoice already exists in Coupa: draft, approved"

---

## 9. Tagging System

Tags are Coupa-side labels attached to invoices. CIB uses tags to communicate workflow context.

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

## 10. UOM & Line Type Logic

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

## 11. Credit Note Handling

### Sign Logic

The `credit_notes_amounts` field indicates how amounts appear on the source document:
- `"negative"` — amounts on document are already negative
- `"positive"` — amounts on document are positive (common for credit notes)

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

## 12. Customization Guide

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
3. **Update OAuth client secret** in hook secrets
4. **Update dataset names** if removing `_test` suffix (affects ALL MDH configs, memorization hooks, import hooks)
5. **Update import webhook Coupa URLs and credentials**
6. **Update import schedules** if different for production
7. **Verify OAuth scopes** match production Coupa setup

### Adding a New Import

To replicate a new Coupa object:
1. Create a new scheduled webhook using `coupalink-import.rossum-ext.app`
2. Configure: endpoint, fields, nested_fields, dataset_name, auth scopes
3. Set schedule (cron expression)
4. The import creates/updates the Data Storage collection automatically

### Adding a New MDH Configuration

1. Add a new configuration object to the appropriate MDH hook's `configurations` array
2. Define: dataset, queries (cascade from exact → fuzzy → fallback), mapping, result_actions
3. Set `action_condition` if the match should only run conditionally
4. Add `additional_mappings` for fields to populate beyond the main target
5. Create corresponding schema fields for target and additional mappings

### Modifying Business Rules

Rules are in `settings.third_party_service_settings.checks` array. Each rule has:
```json
{
  "rule": "expression evaluating to true/false",
  "message": "Message shown to user with {field_id} interpolation",
  "type": "error|warning|info",
  "automation_blocker": true|false,
  "condition": "optional condition when rule applies"
}
```

- `error` = blocks confirmation
- `warning` = shows warning, blocks automation if `automation_blocker: true`
- `automation_blocker: true` = prevents automatic processing

---

## 13. Hook Execution Order

### On Document Open/Update (initialize, started, updated)

```
1. MDH - Main [343051]
   ├── 2. MDH - Payment Terms [453520] (run_after: 343051)
   ├── 2. MDH - Tax Codes [540278] (run_after: 343051) — LINE LEVEL ONLY
   └── 2. MDH - Coupa Invoice Check [735955] (run_after: 343051)
3. Business Rules - Common [540353] (run_after: 453520)
3. Business Rules - Line Level [443698] (run_after: 453520, 540278) — LINE LEVEL ONLY
3. Business Rules - Header Level [540311] (run_after: 453520) — HEADER LEVEL ONLY
4. Duplicate Handling [444021] (run_after: 343051)
5. ShowHide Fields [541028/541085] (run_after: 540278/453520)
6. Metadata Propagator [735954] (on initialize, started only)
7. Supplier Memorization [693315] (on user_update — checks status)
7. Customer Memorization [693316] (on user_update — checks status)
7. Tax Coding Memorization [747526] (on user_update — checks status)
```

### On Export

```
1. Export Mapping [418429 or 419136] (per queue)
2. Create Draft [418430] (run_after: both 418429 and 419136)
3. Create Draft - parse response [418431]
4a. Attach Image Scan [418432] (parallel, gated)
4b. Attach Rossum URL [418434] (parallel, gated)
5a. Attach Image Scan - parse response [418433]
5b. Attach Rossum URL - parse response [418435]
6. Submit Document [418436] (waits for 5a+5b, gated)
7. Submit Document - parse response [418437]
8. Handle Coupa Responses [418438]
```

---

## 14. Required OAuth Scopes

| Scope | Used By |
|-------|---------|
| `core.invoice.create` | Create Draft (export) |
| `core.invoice.read` | Create Draft (export), Coupa Invoice Check (MDH) |
| `core.invoice.write` | Attach Image, Attach URL, Submit (export) |
| `core.supplier.read` | Supplier import |
| `core.account_type.read` | Account Types import |
| `core.purchase_order.read` | PO import |
| `core.purchase_order_line.read` | PO Lines import |
| `core.lookup_value.read` | Lookup Values import |
| `core.tax_code.read` | Tax Codes import |
| `core.uom.read` | UOM import |
| `core.tax_registration.read` | Tax Registrations import |
| `core.address.read` | Addresses import |
| `core.payment_term.read` | Payment Terms import |
| `core.contract.read` | Contracts import |

---

## 15. Troubleshooting Guide

### Export Fails with Error

1. Check `api1_status_code` — if not 201, draft creation failed
2. Read `api1_response_body` for Coupa error details
3. Common causes:
   - Missing required fields (supplier, currency, at least one line item)
   - Invalid field values (wrong tax code, invalid date format)
   - Coupa validation errors (duplicate invoice number for supplier)

### Matching Not Working

1. Verify the Data Storage collection has data: use `data_storage_find` or `data_storage_aggregate` with `[{"$sample": {"size": 1}}]`
2. Check import webhook is running (cron schedule active, no errors in hook logs)
3. Verify field values being sent to MDH queries (check the `{field_id}` placeholders)
4. For fuzzy search: ensure Atlas Search indexes exist on the collection
5. For memorization: check the memorization collection has records

### Document Always Drafted

Check submission conditions:
1. `enforce_draft` must be `"No"`
2. `document_type` must not be `"credit_note"`
3. Must be PO-backed (`po_backed == "true"`) or contract-backed (`backing_document == "contract"`)
4. For line-level: `fully_tax_coded` must be `"true"`
5. Check for automation blockers in business rules

### Coupa Total Mismatch

The `inv_total_issue_tag` fires when `coupa_total_calculated != amount_total`. This means Coupa's backward calculation from exported data produces a different total than the document. Common causes:
- Rounding differences on line items
- Charges calculation differences
- Tax calculation differences (especially with multiple tax rates)

---

## 16. Quick Reference — All Datasets

| Dataset | Source | Used By | Records |
|---------|--------|---------|---------|
| `suppliers_test` | Coupa Import | MDH Supplier matching | Suppliers |
| `account_types_test` | Coupa Import | MDH Customer matching, PO line account resolution | Entities/CoA |
| `purchase_orders_test` | Coupa Import | MDH PO Header matching, Blanket PO | PO headers |
| `purchase_order_lines_test` | Coupa Import | MDH PO Line matching, Blanket PO | PO lines |
| `lookup_values_test` | Coupa Import | Not used in CIB (for custom account coding) | Billing segments |
| `tax_codes_test` | Coupa Import | MDH Tax Code matching | Tax codes |
| `uoms_test` | Coupa Import | UOM precision checks | Units of measure |
| `tax_registrations_test` | Coupa Import | MDH Tax Registration matching | Tax registrations |
| `addresses_test` | Coupa Import | MDH Ship-To matching | Addresses |
| `payment_terms_test` | Coupa Import | MDH Payment Terms matching, EPD matching | Payment terms |
| `suppliers_remit_to_addresses_test` | Coupa Import | Not used in CIB (for custom Remit-To) | Remit-to addresses |
| `contracts_test` | Coupa Import | MDH Contract matching | Contracts |
| `_supplier_memorization_test` | Memorization hook | MDH Supplier matching (query 2) | Supplier selections |
| `_customer_memorization_test` | Memorization hook | MDH Customer matching (query 2) | Customer selections |
| `_tax_code_memorization` | Memorization hook | MDH Tax Code matching (memorization stage) | Tax code selections |

---

## 17. Quick Reference — All Hooks

| ID | Name | Type | Event | Queues |
|----|------|------|-------|--------|
| 339158 | Coupa Import - Suppliers | webhook (scheduled) | — | — |
| 339902 | Coupa Import - Account Types | webhook (scheduled) | — | — |
| 339970 | Coupa Import - Purchase Orders | webhook (scheduled) | — | — |
| 340003 | Coupa Import - Tax Codes | webhook (scheduled) | — | — |
| 342923 | Coupa Import - Lookup Values | webhook (scheduled) | — | — |
| 342955 | Coupa Import - Units of Measure | webhook (scheduled) | — | — |
| 421929 | Coupa Import - Tax Registrations | webhook (scheduled) | — | — |
| 421930 | Coupa Import - Addresses | webhook (scheduled) | — | — |
| 421931 | Coupa Import - Purchase Order Lines | webhook (scheduled) | — | — |
| 422850 | Coupa Import - Payment Terms | webhook (scheduled) | — | — |
| 694975 | Coupa Import - Remit To Addresses | webhook (scheduled) | — | — |
| 784911 | Coupa Import - Contracts | webhook (scheduled) | — | — |
| 343051 | MDH - Main | webhook | init/started/updated | Both |
| 453520 | MDH - Payment Terms | webhook | init/started/updated | Both |
| 540278 | MDH - Tax Codes | webhook | init/started/updated | Line Level only |
| 735955 | MDH - Coupa Invoice Check | webhook | init/started/updated | Both |
| 443698 | Business Rules - Line Level | webhook | init/started/updated | Line Level only |
| 540311 | Business Rules - Header Level | webhook | init/started/updated | Header Level only |
| 540353 | Business Rules - Common | webhook | init/started/updated | Both |
| 444021 | Duplicate Handling | webhook | init/started/updated | Both |
| 541028 | ShowHide Fields - Line Level | function | init/started/updated | Line Level only |
| 541085 | ShowHide Fields - Header Level | function | init/started/updated | Header Level only |
| 693315 | Supplier Memorization | function | user_update | Both |
| 693316 | Customer Memorization | function | user_update | Both |
| 747526 | Tax Coding Memorization | function | user_update | Both |
| 735954 | Metadata Propagator | function | init/started | Both |
| 418429 | Export Mapping - Line Level | webhook | export | Line Level only |
| 419136 | Export Mapping - Header Level | webhook | export | Header Level only |
| 418430 | Create Draft | webhook | export | Both |
| 418431 | Create Draft - parse response | webhook | export | Both |
| 418432 | Attach Image Scan | webhook | export | Both |
| 418433 | Attach Image Scan - parse response | webhook | export | Both |
| 418434 | Attach Rossum URL | webhook | export | Both |
| 418435 | Attach Rossum URL - parse response | webhook | export | Both |
| 418436 | Submit Document | webhook | export | Both |
| 418437 | Submit Document - parse response | webhook | export | Both |
| 418438 | Handle Coupa Responses | function | export | Both |
