# Structured Formats Import (SFI) — Complete Reference

## Overview

SFI processes non-visual structured documents (XML, JSON) and renders a PDF for human review in Rossum. It extracts data from structured files using XPath (XML) or JMESPath (JSON) selectors and maps them to Rossum schema datapoints.

**Key capabilities:**
- XML parsing with XPath 1.0 selectors (via lxml)
- JSON parsing with JMESPath selectors (with custom `upper()`, `lower()`, `round()` functions)
- Document splitting (one file → multiple annotations)
- PDF rendering (generated from extracted data or extracted from embedded base64)
- Value transformation mappings (e.g., code → enum label)
- Date parsing with configurable formats
- ZUGFeRD/X-Rechnung PDF attachment extraction (embedded XML from PDF invoices)

---

## Step 1: Allow Structured Documents in Rossum

### Queue Settings (always required)

In Queue → Queue Settings (Django admin), add desired MIME types to `accepted_mime_types`:

```json
{
  "accepted_mime_types": [
    "image/*",
    "text/plain",
    "application/pdf",
    "application/json",
    "application/xml",
    "text/xml"
  ]
}
```

### Organization Group Settings (XML only)

If processing XML documents, check the Organization Group settings:

In Organization Group → Features, remove `application/xml` and `text/xml` from `store_only_mime_types`. Alternatively, remove the `store_only_mime_types` field entirely unless you need to exclude specific MIME types.

If `store_only_mime_types` is not present, no action needed — the default works with SFI.

---

## Step 2: Create the SFI Extension Hook

Create a new **custom extension** in Rossum:

1. Set the event to `upload.created`
2. Choose your queues
3. Set the URL based on environment:

| Environment | URL |
|---|---|
| prod-eu | `https://elis.task-manager.rossum-ext.app/api/v1/tasks/structured-formats-import` |
| prod-eu2 (customer domain) | `https://{customer_subdomain}.task-manager.rossum-ext.app/api/v1/tasks/structured-formats-import` |
| prod-eu2 (shared) | `https://shared-eu2.task-manager.rossum-ext.app/api/v1/tasks/structured-formats-import` |
| prod-us2 | `https://us.task-manager.rossum-ext.app/api/v1/tasks/structured-formats-import` |
| prod-jp | `https://shared-jp.task-manager.rossum-ext.app/api/v1/tasks/structured-formats-import` |
| master | `https://dev-eu.task-manager.ext.master.r8.lol/api/v1/tasks/structured-formats-import` |

4. Assign a **token owner**
5. If imports may run longer than 10 minutes, set `token_lifetime_s` in the hook (max: 7200 = 2 hours)
6. Set the polling timeout: `"max_polling_time_s": 3600` in `hook.config` (via Django admin or API)

---

## Step 3: Configure Hook Settings

The hook settings contain the `configurations` array. Each configuration defines trigger conditions and field mappings.

### Configuration Structure

```json
{
  "configurations": [
    {
      "trigger_condition": {
        "file_type": "xml",
        "selector": "/*[local-name()='Invoice']"
      },
      "pdf_file": {
        "content_selectors": ["..."],
        "name_selectors": ["..."]
      },
      "split_selectors": ["invoice/invoices"],
      "parse_formats": {
        "date_format": "%m/%d/%Y %H:%M:%S"
      },
      "fields": [
        {
          "schema_id": "document_id",
          "selectors": ["/*[local-name()='Invoice']/*[local-name()='ID']"],
          "skip_non_existing_schema_ids": true
        }
      ]
    }
  ]
}
```

### Configuration Matching

The first configuration where:
1. `file_type` matches the uploaded document, AND
2. `selector` evaluates to true (finds an element)

...will be used. No selector is equivalent to "always true" (fallback config).

---

## Field Mapping Reference

### Basic Field Mapping

```json
{
  "schema_id": "document_id",
  "selectors": [
    "invalid/selector",
    "/*[local-name()='Invoice']/*[local-name()='ID']"
  ]
}
```

- `schema_id`: The Rossum schema field ID to populate
- `selectors`: Array of selectors tried in order; first one returning a result is used
- `skip_non_existing_schema_ids`: If `true`, silently skips when the schema_id doesn't exist in the schema (default: `false` — import fails on missing schema_id)

### Line Items (Multivalue Fields)

```json
{
  "schema_id": "line_items",
  "selectors": ["items/item"],
  "fields": [
    {
      "schema_id": "item_quantity",
      "selectors": ["quantity"]
    },
    {
      "schema_id": "item_amount",
      "selectors": ["price"]
    },
    {
      "schema_id": "item_description",
      "selectors": ["../../description"]
    }
  ]
}
```

When `fields` is present on a multivalue entry:
- The top-level `selectors` find multiple elements (rows)
- For each element found, the nested `fields` selectors are evaluated **relative to that element**
- Relative selectors like `../../description` go up from the current context node (XPath only)

---

## Value Transformation Mapping

Maps extracted values to different output values. Only works with `string` and `enum` datapoint types.

```json
{
  "schema_id": "document_type",
  "selectors": ["/*[local-name()='Invoice']/*[local-name()='InvoiceTypeCode']"],
  "mapping": {
    "380": "tax_invoice",
    "381": "credit_note",
    "383": "debit_note",
    "__default__": "other"
  }
}
```

**Rules:**
- `__default__` key is **mandatory**
- If `mapping` key is not defined, is `null`, or is empty `{}`, no mapping is applied
- Only applicable to `string` and `enum` type datapoints — using it on other types fails the entire import

### Currency Mapping Example

```json
{
  "schema_id": "currency",
  "selectors": ["/*[local-name()='Invoice']/*[local-name()='DocumentCurrencyCode']"],
  "mapping": {
    "AUD": "aud", "CHF": "chf", "CNY": "cny", "CZK": "czk",
    "DKK": "dkk", "EUR": "eur", "GBP": "gbp", "HUF": "huf",
    "INR": "inr", "JPY": "jpy", "NOK": "nok", "PLN": "pln",
    "RON": "ron", "RUB": "rub", "SEK": "sek", "USD": "usd",
    "__default__": "other"
  }
}
```

---

## Date Parsing

### Global Date Format

Set `parse_formats.date_format` at the configuration level to apply to all date fields:

```json
{
  "parse_formats": {
    "date_format": "%m/%d/%Y %H:%M:%S"
  },
  "fields": [...]
}
```

### Per-Field Date Format

Set `format` on individual fields (takes precedence over global):

```json
{
  "schema_id": "date_issue",
  "format": "%m/%d/%Y %H:%M:%S",
  "selectors": ["issued"]
}
```

Uses Python's `datetime.strptime()` format codes. If parsed successfully, the value is stored in ISO format (`2020-10-23`). If parsing fails or no format is defined, the raw value is passed through unchanged.

---

## XPath Selector Patterns

SFI uses XPath 1.0 (via lxml). Default namespaces are automatically removed for cleaner selectors.

### Namespace-Safe Selectors

Use `local-name()` to match elements regardless of namespace:

```xpath
/*[local-name()='Invoice']/*[local-name()='ID']
```

### Address Concatenation Pattern

Combines multiple address fields with conditional separators (no double commas when fields are missing):

```
normalize-space(
  concat(
    normalize-space(StreetNameXPath),
    substring(', ', 1 div string-length(normalize-space(AdditionalStreetXPath))),
    normalize-space(AdditionalStreetXPath),
    substring(', ', 1 div string-length(normalize-space(PostalZoneXPath))),
    normalize-space(PostalZoneXPath),
    ' ',
    normalize-space(CityNameXPath),
    substring(', ', 1 div string-length(normalize-space(CountrySubentityXPath))),
    normalize-space(CountrySubentityXPath),
    substring(', ', 1 div string-length(normalize-space(CountryCodeXPath))),
    normalize-space(CountryCodeXPath)
  )
)
```

**How it works:**
- `normalize-space()` trims whitespace
- `substring(', ', 1 div string-length(field))` inserts `, ` only if the field has content (length > 0)
- Postal code and city are separated by space (always adjacent)
- Other components use conditional commas

**Produces:** `123 High Street, Building A, SW1A 1AA London, England, GB`

### UBL Sender Address (Complete Example)

```json
{
  "schema_id": "sender_address",
  "selectors": [
    "normalize-space(concat(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='StreetName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='AdditionalStreetName']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='AdditionalStreetName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='PostalZone']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='PostalZone']), ' ', normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CityName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CountrySubentity']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CountrySubentity']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='Country']/*[local-name()='IdentificationCode']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='Country']/*[local-name()='IdentificationCode'])))"
  ],
  "skip_non_existing_schema_ids": true
}
```

### CII/ZUGFeRD Sender Address (Complete Example)

```json
{
  "schema_id": "sender_address",
  "selectors": [
    "normalize-space(concat(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineOne']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineTwo']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineTwo']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='PostcodeCode']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='PostcodeCode']), ' ', normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CityName']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountrySubentity']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountrySubentity']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountryID']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountryID'])))"
  ],
  "skip_non_existing_schema_ids": true
}
```

### Upper/Lower Case Conversion (XPath 1.0)

XPath 1.0 doesn't support case conversion directly. Use `translate()`:

```xpath
translate('HELLO WORLD', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
```

Real example — convert invoice notes to lowercase:

```json
{
  "schema_id": "notes",
  "selectors": [
    "translate(/*[local-name()='Invoice']/*[local-name()='Note'], 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
  ],
  "skip_non_existing_schema_ids": true
}
```

### String Concatenation (XPath)

```xpath
concat(/*/vendor/address/state, " - ", /*/vendor/address/zip)
```

### Counting Elements

```json
{
  "schema_id": "einvoice_lines_bg25",
  "selectors": ["count(/*[local-name()='Invoice']/*[local-name()='InvoiceLine'])"],
  "mapping": {
    "0.0": "",
    "__default__": "present"
  }
}
```

---

## JSON Selector Patterns

SFI uses JMESPath for JSON documents, with custom functions.

### Upper/Lower Case (JSON)

```json
"lower(invoice.note)"
"upper(invoice.note)"
```

### String Concatenation (JSON)

Uses `join()`:

```json
"join(', ', [vendor.address.street, vendor.address.city])"
```

---

## PDF Rendering

### Embedded PDF Extraction

```json
{
  "pdf_file": {
    "content_selectors": ["pdf_content_field"],
    "name_selectors": ["pdf_name_field"]
  }
}
```

- `content_selectors`: Searches for base64-encoded PDF content in the document
- `name_selectors`: Uses matched value as PDF filename
- If content not found or decoding fails, a PDF is generated from extracted values
- If name not found, the original filename is used (e.g., `file.xml` → `file.pdf`)

### ZUGFeRD PDF Support

SFI automatically extracts embedded XML from PDF invoices. It looks for attachments named:
- `zugferd-invoice.xml`
- `factur-x.xml`
- `xrechnung.xml`

### E-Invoice Filename Convention (`einvoice<invoice id>.pdf` / `.xml`)

Production e-invoice import configurations commonly set `name_selectors` (see *Embedded PDF
Extraction* above) so the rendered PDF is named `einvoice<invoice id>.pdf` and its source XML
twin `einvoice<invoice id>.xml`, where `<invoice id>` is the **source network's own invoice id**
(e.g. an id assigned by the e-invoicing network the document arrived from), not anything
Rossum-assigned. Because that id is minted by the source system and passes through untouched, it
is a free exact join key back to the source system — no separate mapping table is needed to
reconcile a Rossum document against the invoice it came from.

A small number of real documents deviate from the plain pattern: `einvoice<invoice
id>_<annotation id>.pdf`, believed to come from a copy/re-upload path — **measured:** 4
occurrences out of 8,357 documents sampled over two months. A filename parser written against a
strict `^einvoice(\d+)\.(pdf|xml)$` regex silently skips these: no match, no error, just a
document invisible to the parser. Anything that recovers the invoice id from the filename should
accept an optional `_<digits>` suffix and take the invoice id from the *first* number only.

---

## Document Splitting

```json
{
  "split_selectors": ["invoice/invoices", "document/sheet/cues"]
}
```

- First selector yielding results is used
- Each matched element becomes a separate annotation
- Split documents are indexed in filename: `original (1).pdf`, `original (2).pdf`
- Processes up to 5 split documents concurrently

---

## Non-Existing Schema IDs

```json
{
  "schema_id": "document_id",
  "selectors": ["/*[local-name()='Invoice']/*[local-name()='ID']"],
  "skip_non_existing_schema_ids": true
}
```

- Default (`false`): If the schema_id doesn't exist in the queue's schema, the import **fails with an error**
- `true`: Silently skips fields whose schema_id is not found
- Set this on **every field** when reusing configs across queues with different schemas

---

## Testing XPath Queries Locally

```python
import pathlib
from lxml import etree

SELECTOR = "/invoice"

BASE_PATH = pathlib.Path(__file__).parent
with open(BASE_PATH / "test.xml", "rb") as xml_file:
    xml_tree = etree.XML(xml_file.read())

found_elements = xml_tree.xpath(SELECTOR, namespaces=xml_tree.nsmap)

if not found_elements:
    print("No Element Found")
else:
    print("Found Elements:")

for e in found_elements:
    print(f"Element {e.tag} with value {e.text}")
```

**Setup:**
1. Save as `test_selector.py`
2. Save your XML as `test.xml` in the same folder
3. `pip install lxml`
4. Set `SELECTOR` to your XPath query
5. Run: `python test_selector.py`

**Caveat:** If the XML has a default namespace (`xmlns="..."`), remove it manually for testing — XPath without `local-name()` won't match namespaced elements.

---

## Complete Configuration Examples

### General XML Configuration (Annotated)

```json
{
  "configurations": [
    {
      "trigger_condition": {
        "file_type": "xml",
        "selector": "vendor[name='Apple']"
      },
      "pdf_file": {
        "content_selectors": ["pdf_content"],
        "name_selectors": ["invalid_selector", "pdf_name"]
      },
      "split_selectors": ["invoice/invoices", "document/sheet/cues"],
      "parse_formats": {
        "date_format": "%m/%d/%Y %H:%M:%S"
      },
      "fields": [
        {
          "schema_id": "document_id",
          "selectors": ["identifier"]
        },
        {
          "schema_id": "sender_name",
          "selectors": ["invalidselector/qwe", "vendor/name"]
        },
        {
          "schema_id": "date_issue",
          "format": "%m/%d/%Y %H:%M:%S",
          "selectors": ["issued"]
        },
        {
          "schema_id": "line_items",
          "selectors": ["items/item"],
          "fields": [
            {
              "schema_id": "item_quantity",
              "selectors": ["quantity"]
            },
            {
              "schema_id": "item_amount",
              "selectors": ["price"]
            },
            {
              "schema_id": "item_description",
              "selectors": ["../../description"]
            }
          ]
        }
      ]
    }
  ]
}
```

**Key points:**
- First configuration with matching `file_type` and truthy `selector` is used
- Multiple selectors per field — first match wins
- Line item selectors are relative to the matched parent element
- `parse_formats.date_format` applies globally; per-field `format` overrides it

---

## German E-Invoicing: UBL X-Rechnung Configuration

Trigger: matches `<Invoice>` root element in XML.

```json
{
  "trigger_condition": {
    "selector": "/*[local-name()='Invoice']",
    "file_type": "xml"
  },
  "pdf_file": {
    "content_selectors": [
      "/*[local-name()='Invoice']/*[local-name()='AdditionalDocumentReference']/*[local-name()='Attachment']/*[local-name()='EmbeddedDocumentBinaryObject']"
    ]
  },
  "fields": [
    {
      "schema_id": "document_id",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "date_issue",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='IssueDate']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "date_due",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='DueDate']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "order_id",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='OrderReference']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "account_num",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PartyIdentification']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "iban",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='PaymentMeans']/*[local-name()='PayeeFinancialAccount']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "bic",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='PaymentMeans']/*[local-name()='PayeeFinancialAccount']/*[local-name()='FinancialInstitutionBranch']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "amount_total",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='LegalMonetaryTotal']/*[local-name()='PayableAmount']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "amount_total_base",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='LegalMonetaryTotal']/*[local-name()='TaxExclusiveAmount']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "sender_name",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PartyLegalEntity']/*[local-name()='RegistrationName']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "sender_address",
      "selectors": [
        "normalize-space(concat(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='StreetName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='AdditionalStreetName']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='AdditionalStreetName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='PostalZone']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='PostalZone']), ' ', normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CityName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CountrySubentity']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CountrySubentity']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='Country']/*[local-name()='IdentificationCode']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='Country']/*[local-name()='IdentificationCode'])))"
      ],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "sender_vat_id",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='PartyTaxScheme']/*[local-name()='CompanyID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "recipient_name",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PartyLegalEntity']/*[local-name()='RegistrationName']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "recipient_address",
      "selectors": [
        "normalize-space(concat(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='StreetName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='AdditionalStreetName']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='AdditionalStreetName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='PostalZone']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='PostalZone']), ' ', normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CityName']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CountrySubentity']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='CountrySubentity']), substring(', ', 1 div string-length(normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='Country']/*[local-name()='IdentificationCode']))), normalize-space(/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='Party']/*[local-name()='PostalAddress']/*[local-name()='Country']/*[local-name()='IdentificationCode'])))"
      ],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "recipient_vat_id",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='AccountingCustomerParty']/*[local-name()='PartyIdentification']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "recipient_delivery_name",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='Delivery']/*[local-name()='ShipToParty']/*[local-name()='PartyName']/*[local-name()='Name']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "amount_total_tax",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='TaxTotal']/*[local-name()='TaxAmount']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "notes",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='Note'] | /*[local-name()='Invoice']/*[local-name()='DueDate']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "document_type",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='InvoiceTypeCode']"],
      "mapping": {
        "380": "tax_invoice",
        "381": "credit_note",
        "383": "debit_note",
        "__default__": "other"
      },
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "currency",
      "selectors": ["/*[local-name()='Invoice']/*[local-name()='DocumentCurrencyCode']"],
      "mapping": {
        "AUD": "aud", "CHF": "chf", "CNY": "cny", "CZK": "czk",
        "DKK": "dkk", "EUR": "eur", "GBP": "gbp", "HUF": "huf",
        "INR": "inr", "JPY": "jpy", "NOK": "nok", "PLN": "pln",
        "RON": "ron", "RUB": "rub", "SEK": "sek", "USD": "usd",
        "__default__": "other"
      },
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "language",
      "selectors": [
        "/*[local-name()='Invoice']/*[local-name()='Note']/@xml:lang",
        "/*[local-name()='Invoice']/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']/*[local-name()='Language']/*[local-name()='LanguageID']"
      ],
      "mapping": {
        "CZ": "ces", "DE": "deu", "EN": "eng", "FR": "fra", "SK": "slk",
        "DAN": "dan", "ESP": "esp", "FIN": "fin", "HUN": "hun",
        "NOR": "nor", "POL": "pol", "POR": "por", "SWE": "swe", "ITAL": "ital",
        "__default__": ""
      },
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "line_items",
      "selectors": ["//*[local-name()='Invoice']/*[local-name()='InvoiceLine']"],
      "skip_non_existing_schema_ids": true,
      "fields": [
        {
          "schema_id": "item_quantity",
          "selectors": ["*[local-name()='InvoicedQuantity']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_amount",
          "selectors": ["*[local-name()='Price']/*[local-name()='PriceAmount']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_description",
          "selectors": ["*[local-name()='Item']/*[local-name()='Name']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_amount_total",
          "selectors": ["*[local-name()='LineExtensionAmount']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_code",
          "selectors": ["*[local-name()='Item']/*[local-name()='SellersItemIdentification']/*[local-name()='ID']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_uom",
          "selectors": ["*[local-name()='InvoicedQuantity']/@unitCode"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_rate",
          "selectors": ["*[local-name()='Item']/*[local-name()='ClassifiedTaxCategory']/*[local-name()='Percent']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_tax",
          "selectors": ["*[local-name()='TaxTotal']/*[local-name()='TaxAmount']"],
          "skip_non_existing_schema_ids": true
        }
      ]
    }
  ]
}
```

---

## German E-Invoicing: CII / ZUGFeRD Configuration

Trigger: matches `<CrossIndustryInvoice>` or `<CrossIndustryDocument>` root element.

```json
{
  "trigger_condition": {
    "selector": "/*[local-name()='CrossIndustryInvoice' or local-name()='CrossIndustryDocument']",
    "file_type": "xml"
  },
  "fields": [
    {
      "schema_id": "document_id",
      "selectors": [
        "//*[local-name()='HeaderExchangedDocument']/*[local-name()='ID']",
        "//*[local-name()='ExchangedDocument']/*[local-name()='ID']",
        "//*[local-name()='ID']"
      ],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "date_issue",
      "selectors": [
        "//*[local-name()='HeaderExchangedDocument']/*[local-name()='IssueDateTime']/*[local-name()='DateTimeString']",
        "//*[local-name()='ExchangedDocument']/*[local-name()='IssueDateTime']/*[local-name()='DateTimeString']",
        "//*[local-name()='IssueDate']"
      ],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "date_due",
      "selectors": ["//*[local-name()='SpecifiedTradePaymentTerms']/*[local-name()='DueDateDateTime']/*[local-name()='DateTimeString']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "order_id",
      "selectors": ["//*[local-name()='ApplicableHeaderTradeAgreement']/*[local-name()='BuyerOrderReferencedDocument']/*[local-name()='IssuerAssignedID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "iban",
      "selectors": ["//*[local-name()='PayeePartyCreditorFinancialAccount']/*[local-name()='IBANID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "bic",
      "selectors": ["//*[local-name()='PayeeSpecifiedCreditorFinancialInstitution']/*[local-name()='BICID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "amount_total",
      "selectors": ["//*[local-name()='SpecifiedTradeSettlementHeaderMonetarySummation']/*[local-name()='DuePayableAmount']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "amount_total_base",
      "selectors": ["//*[local-name()='SpecifiedTradeSettlementHeaderMonetarySummation']/*[local-name()='TaxBasisTotalAmount']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "sender_name",
      "selectors": ["//*[local-name()='SellerTradeParty']/*[local-name()='Name']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "sender_address",
      "selectors": [
        "normalize-space(concat(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineOne']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineTwo']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineTwo']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='PostcodeCode']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='PostcodeCode']), ' ', normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CityName']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountrySubentity']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountrySubentity']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountryID']))), normalize-space(//*[local-name()='SellerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountryID'])))"
      ],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "sender_vat_id",
      "selectors": ["//*[local-name()='SellerTradeParty']/*[local-name()='SpecifiedTaxRegistration']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "recipient_name",
      "selectors": ["//*[local-name()='BuyerTradeParty']/*[local-name()='Name']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "recipient_address",
      "selectors": [
        "normalize-space(concat(normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineOne']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineTwo']))), normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='LineTwo']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='PostcodeCode']))), normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='PostcodeCode']), ' ', normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CityName']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountrySubentity']))), normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountrySubentity']), substring(', ', 1 div string-length(normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountryID']))), normalize-space(//*[local-name()='BuyerTradeParty']/*[local-name()='PostalTradeAddress']/*[local-name()='CountryID'])))"
      ],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "recipient_vat_id",
      "selectors": ["//*[local-name()='BuyerTradeParty']/*[local-name()='SpecifiedTaxRegistration']/*[local-name()='ID']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "amount_total_tax",
      "selectors": ["//*[local-name()='ApplicableTradeTax']/*[local-name()='CalculatedAmount']"],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "notes",
      "selectors": [
        "//*[local-name()='IncludedNote']/*[local-name()='Content']",
        "//*[local-name()='IncludedNote']"
      ],
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "document_type",
      "selectors": [
        "//*[local-name()='HeaderExchangedDocument']/*[local-name()='TypeCode']",
        "//*[local-name()='ExchangedDocument']/*[local-name()='TypeCode']"
      ],
      "mapping": {
        "380": "tax_invoice",
        "381": "credit_note",
        "383": "debit_note",
        "__default__": "other"
      },
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "currency",
      "selectors": [
        "//*[local-name()='ExchangedDocumentContext']/*[local-name()='CurrencyCode']",
        "//*[local-name()='SupplyChainTradeTransaction']/*[local-name()='ApplicableHeaderTradeSettlement']/*[local-name()='InvoiceCurrencyCode']"
      ],
      "mapping": {
        "AUD": "aud", "CHF": "chf", "CNY": "cny", "CZK": "czk",
        "DKK": "dkk", "EUR": "eur", "GBP": "gbp", "HUF": "huf",
        "INR": "inr", "JPY": "jpy", "NOK": "nok", "PLN": "pln",
        "RON": "ron", "RUB": "rub", "SEK": "sek", "USD": "usd",
        "__default__": "other"
      },
      "skip_non_existing_schema_ids": true
    },
    {
      "schema_id": "line_items",
      "selectors": ["//*[local-name()='IncludedSupplyChainTradeLineItem']"],
      "skip_non_existing_schema_ids": true,
      "fields": [
        {
          "schema_id": "item_quantity",
          "selectors": [
            "*[local-name()='SpecifiedLineTradeDelivery']/*[local-name()='BilledQuantity']",
            "*[local-name()='SpecifiedSupplyChainTradeDelivery']/*[local-name()='BilledQuantity']"
          ],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_amount",
          "selectors": [
            "*[local-name()='SpecifiedLineTradeAgreement']/*[local-name()='NetPriceProductTradePrice']/*[local-name()='ChargeAmount']",
            "*[local-name()='SpecifiedSupplyChainTradeAgreement']/*[local-name()='NetPriceProductTradePrice']/*[local-name()='ChargeAmount']"
          ],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_description",
          "selectors": ["*[local-name()='SpecifiedTradeProduct']/*[local-name()='Name']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_amount_total",
          "selectors": [
            "*[local-name()='SpecifiedLineTradeSettlement']/*[local-name()='SpecifiedTradeSettlementLineMonetarySummation']/*[local-name()='LineTotalAmount']",
            "*[local-name()='SpecifiedSupplyChainTradeSettlement']/*[local-name()='SpecifiedTradeSettlementMonetarySummation']/*[local-name()='LineTotalAmount']"
          ],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_code",
          "selectors": ["*[local-name()='SpecifiedTradeProduct']/*[local-name()='SellerAssignedID']"],
          "skip_non_existing_schema_ids": true
        },
        {
          "schema_id": "item_rate",
          "selectors": ["*[local-name()='SpecifiedLineTradeSettlement']/*[local-name()='ApplicableTradeTax']/*[local-name()='RateApplicablePercent']"],
          "skip_non_existing_schema_ids": true
        }
      ]
    }
  ]
}
```

---

## Additional Mapping Examples

### ZUGFeRD to Pre-Trained Fields (Namespace-Prefixed)

For environments where namespace prefixes are preserved (not using `local-name()`):

```json
{
  "fields": [
    {"schema_id": "invoice_number", "selectors": ["//rsm:ExchangedDocument/ram:ID"]},
    {"schema_id": "invoice_date", "selectors": ["//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"]},
    {"schema_id": "amount_total", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount"]},
    {"schema_id": "amount_due", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:DuePayableAmount"]},
    {"schema_id": "tax_amount", "selectors": ["//ram:ApplicableTradeTax/ram:CalculatedAmount"]},
    {"schema_id": "supplier_name", "selectors": ["//ram:SellerTradeParty/ram:Name"]},
    {"schema_id": "supplier_tax_id", "selectors": ["//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID"]},
    {"schema_id": "supplier_address", "selectors": ["//ram:SellerTradeParty/ram:PostalTradeAddress"]},
    {"schema_id": "customer_name", "selectors": ["//ram:BuyerTradeParty/ram:Name"]},
    {"schema_id": "customer_tax_id", "selectors": ["//ram:BuyerTradeParty/ram:SpecifiedTaxRegistration/ram:ID"]},
    {"schema_id": "customer_address", "selectors": ["//ram:BuyerTradeParty/ram:PostalTradeAddress"]},
    {"schema_id": "payment_terms", "selectors": ["//ram:SpecifiedTradePaymentTerms/ram:Description"]}
  ]
}
```

### X-Rechnung CII to Pre-Trained Fields

```json
{
  "fields": [
    {"schema_id": "document_id", "selectors": ["//rsm:ExchangedDocument/ram:ID"]},
    {"schema_id": "date_issue", "selectors": ["//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"]},
    {"schema_id": "amount_total", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount"]},
    {"schema_id": "amount_due", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:DuePayableAmount"]},
    {"schema_id": "currency", "selectors": ["//ram:ApplicableHeaderTradeSettlement/ram:InvoiceCurrencyCode"]},
    {"schema_id": "sender_name", "selectors": ["//ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty/ram:Name"]},
    {"schema_id": "sender_address", "selectors": ["//ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty/ram:PostalTradeAddress"]},
    {"schema_id": "recipient_name", "selectors": ["//ram:ApplicableHeaderTradeAgreement/ram:BuyerTradeParty/ram:Name"]},
    {"schema_id": "recipient_address", "selectors": ["//ram:ApplicableHeaderTradeAgreement/ram:BuyerTradeParty/ram:PostalTradeAddress"]},
    {"schema_id": "iban", "selectors": ["//ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeePartyCreditorFinancialAccount/ram:IBANID"]},
    {"schema_id": "bic", "selectors": ["//ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID"]},
    {"schema_id": "tax_amount", "selectors": ["//ram:ApplicableTradeTax/ram:CalculatedAmount"]}
  ]
}
```

### ZUGFeRD to Rossum EU Default Schema (Full)

Complete mapping covering all standard fields including payment, amounts, vendor/customer details:

```json
{
  "fields": [
    {"schema_id": "document_id", "selectors": ["//rsm:ExchangedDocument/ram:ID"]},
    {"schema_id": "order_id", "selectors": ["//rsm:SpecifiedSupplyChainTradeTransaction/ram:ApplicableSupplyChainTradeAgreement/ram:BuyerOrderReferencedDocument/ram:ID"]},
    {"schema_id": "date_issue", "selectors": ["//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"]},
    {"schema_id": "date_due", "selectors": ["//ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString"]},
    {"schema_id": "document_type", "selectors": ["//rsm:ExchangedDocument/ram:TypeCode"]},
    {"schema_id": "language", "selectors": ["//rsm:ExchangedDocument/ram:LanguageID"]},
    {"schema_id": "customer_id", "selectors": ["//ram:BuyerTradeParty/ram:ID"]},
    {"schema_id": "account_num", "selectors": ["//ram:PayeePartyCreditorFinancialAccount/ram:AccountID"]},
    {"schema_id": "bank_num", "selectors": ["//ram:PayeeSpecifiedCreditorFinancialInstitution/ram:ID"]},
    {"schema_id": "iban", "selectors": ["//ram:PayeePartyCreditorFinancialAccount/ram:IBANID"]},
    {"schema_id": "bic", "selectors": ["//ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID"]},
    {"schema_id": "terms", "selectors": ["//ram:SpecifiedTradePaymentTerms/ram:Description"]},
    {"schema_id": "payment_method", "selectors": ["//ram:SpecifiedTradeSettlementPaymentMeans/ram:TypeCode"]},
    {"schema_id": "amount_total", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount"]},
    {"schema_id": "amount_due", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:DuePayableAmount"]},
    {"schema_id": "amount_total_base", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TaxBasisTotalAmount"]},
    {"schema_id": "amount_total_tax", "selectors": ["//ram:ApplicableTradeTax/ram:CalculatedAmount"]},
    {"schema_id": "amount_rounding", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:RoundingAmount"]},
    {"schema_id": "amount_paid", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:PaidAmount"]},
    {"schema_id": "currency", "selectors": ["//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount/@currencyID"]},
    {"schema_id": "sender_name", "selectors": ["//ram:SellerTradeParty/ram:Name"]},
    {"schema_id": "sender_address", "selectors": ["//ram:SellerTradeParty/ram:PostalTradeAddress"]},
    {"schema_id": "sender_ic", "selectors": ["//ram:SellerTradeParty/ram:SpecifiedLegalOrganization/ram:ID"]},
    {"schema_id": "sender_vat_id", "selectors": ["//ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID"]},
    {"schema_id": "sender_email", "selectors": ["//ram:SellerTradeParty/ram:DefinedTradeContact/ram:EmailURIUniversalCommunication"]},
    {"schema_id": "recipient_name", "selectors": ["//ram:BuyerTradeParty/ram:Name"]},
    {"schema_id": "recipient_address", "selectors": ["//ram:BuyerTradeParty/ram:PostalTradeAddress"]},
    {"schema_id": "recipient_ic", "selectors": ["//ram:BuyerTradeParty/ram:SpecifiedLegalOrganization/ram:ID"]},
    {"schema_id": "recipient_vat_id", "selectors": ["//ram:BuyerTradeParty/ram:SpecifiedTaxRegistration/ram:ID"]},
    {"schema_id": "recipient_delivery_name", "selectors": ["//ram:ActualDeliverySupplyChainEvent/ram:ShipToTradeParty/ram:Name"]},
    {"schema_id": "recipient_delivery_address", "selectors": ["//ram:ActualDeliverySupplyChainEvent/ram:ShipToTradeParty/ram:PostalTradeAddress"]}
  ]
}
```

---

## E-Invoice Schema Reference

When setting up SFI for German e-invoicing, the queue schema typically includes an **EN16931 e-invoice fields section** with read-only data fields populated by SFI alongside the standard captured fields. Key schema IDs:

| Schema ID | Label | BT Reference |
|---|---|---|
| `einvoice_spec_identifier_bt24` | Specification Identifier | BT-24 |
| `einvoice_invoice_number_bt1` | Invoice Number | BT-1 |
| `einvoice_invoice_issue_date_bt2` | Invoice Issue Date | BT-2 |
| `einvoice_invoice_type_code_bt3` | Invoice Type Code | BT-3 |
| `einvoice_currency_code_bt5` | Currency Code | BT-5 |
| `einvoice_seller_name_bt27` | Seller Name | BT-27 |
| `einvoice_buyer_name_bt44` | Buyer Name | BT-44 |
| `einvoice_seller_postal_address_bg5` | Seller Postal Address | BG-5 |
| `einvoice_seller_country_code_bt40` | Seller Country Code | BT-40 |
| `einvoice_buyer_postal_address_bg8` | Buyer Postal Address | BG-8 |
| `einvoice_buyer_country_code_bt55` | Buyer Country Code | BT-55 |
| `einvoice_sum_of_line_net_amount_bt106` | Sum of Line Net Amount | BT-106 |
| `einvoice_total_amount_wo_vat_bt109` | Total Amount Without VAT | BT-109 |
| `einvoice_total_amount_w_vat_bt112` | Total Amount With VAT | BT-112 |
| `einvoice_amount_due_bt115` | Amount Due | BT-115 |
| `einvoice_lines_bg25` | Line Items Group Present | BG-25 |
| `einvoice_vat_cat_taxable_amount_bt116` | VAT Taxable Amount | BT-116 |
| `einvoice_vat_cat_tax_amount_bt117` | VAT Tax Amount | BT-117 |
| `einvoice_vat_cat_code_bt118` | VAT Category Code | BT-118 |
| `einvoice_vat_cat_rate_bt119` | VAT Category Rate | BT-119 |

E-invoice line items use a separate multivalue (`einvoice_line_items`) with fields:
- `einvoice_item_quantity_bt129` (BT-129)
- `einvoice_item_uom_code_bt130` (BT-130)
- `einvoice_line_net_amount_bt131` (BT-131)
- `einvoice_item_name_bt153` (BT-153)
- `einvoice_item_net_price_bt146` (BT-146)

These fields typically have `"ui_configuration": {"type": "data", "edit": "disabled"}` — they are read-only display fields showing raw e-invoice data for compliance verification.

---

## Troubleshooting

### Common Issues

| Problem | Cause | Fix |
|---|---|---|
| Import fails with "schema_id not found" | Field mapped to non-existent schema ID | Add `"skip_non_existing_schema_ids": true` or add the field to the schema |
| No data extracted | Wrong selector / namespace issues | Test selectors locally; use `local-name()` for namespaced XML |
| Import times out | Large file or slow network | Increase `token_lifetime_s` and `max_polling_time_s` |
| XML rejected by queue | MIME type not allowed | Add `application/xml` and `text/xml` to `accepted_mime_types` |
| XML stored but not processed | `store_only_mime_types` blocking | Remove XML types from `store_only_mime_types` in Organization Group |
| Value mapping fails | Used on non-string/non-enum field | Value mapping only works with `string` and `enum` types |
| Double commas in address | Missing conditional separator logic | Use the `substring(', ', 1 div string-length(...))` pattern |

### XPath Debugging Tips

- Use `local-name()` to avoid namespace issues: `/*[local-name()='Invoice']` instead of `/Invoice`
- Test selectors locally with the Python script before deploying
- If default namespace present (`xmlns="..."`), remove it from test XML manually
- Multiple selectors are tried in order — put the most likely match first
- For line items, child selectors are relative to the parent element found by the top-level selector
