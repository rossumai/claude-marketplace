---
name: document-processing
description: Extract structured data from invoices, purchase orders, and receipts using only Claude — no Rossum API. Validates totals/tax/currency, flags anomalies by severity, and outputs ERP-ready JSON plus a batch summary. Use when the user pastes or uploads transactional documents and wants extraction, validation, and routing without a Rossum queue.
---

# SKILL: Document Processing for Transactional Workflows

## Purpose
Extract structured data from invoices, purchase orders, receipts, and other transactional documents. Validate, transform, and route the data to downstream systems.

## Instructions
- Accept uploaded documents (PDF, image, email attachment) and extract all key fields
- For invoices: extract vendor name, invoice number, date, line items, amounts, tax, currency, PO reference, payment terms, bank details
- For purchase orders: extract buyer, supplier, line items, quantities, unit prices, delivery dates
- For receipts: extract merchant, date, items, totals, payment method
- Cross-validate extracted fields (e.g., line item totals vs. grand total, tax calculations)
- Flag anomalies: duplicate invoice numbers, amounts exceeding thresholds, missing fields
- Output structured JSON matching the user's ERP schema
- Support multi-language documents (infer language automatically)
- Learn user corrections: if a user fixes a field, remember that vendor's format for next time
- Provide a summary of each batch: documents processed, exceptions flagged

## Output Format

Return exactly two blocks per execution: a JSON code block with the extraction result, then a markdown summary table.

### Extraction Result Schema

Every document produces one JSON object with this exact structure. All keys are required. Use `null` for missing values — never omit a key.

```json
{
  "document_type": "invoice | purchase_order | receipt",
  "language": "<ISO 639-1 code>",
  "metadata": {
    "invoice_number": "<string | null>",
    "order_number": "<string | null>",
    "issue_date": "<YYYY-MM-DD | null>",
    "due_date": "<YYYY-MM-DD | null>",
    "payment_terms": "<string | null>"
  },
  "vendor": {
    "name": "<string>",
    "address": "<string | null>",
    "country": "<ISO 3166-1 alpha-2 | null>"
  },
  "customer": {
    "name": "<string>",
    "address": "<string | null>",
    "country": "<ISO 3166-1 alpha-2 | null>"
  },
  "line_items": [
    {
      "line": "<int, 1-indexed>",
      "quantity": "<number | null>",
      "unit": "<string | null>",
      "description": "<string>",
      "vat_rate": "<string, e.g. '10%' | null>",
      "amount": "<number, negative for credits>",
      "currency": "<ISO 4217 code>",
      "note": "<string explaining ambiguity | null>"
    }
  ],
  "totals": {
    "subtotal": "<number | null>",
    "subtotal_currency": "<ISO 4217 code | null>",
    "vat_breakdown": [
      {
        "rate": "<string, e.g. '10%'>",
        "amount": "<number>",
        "currency": "<ISO 4217 code>"
      }
    ],
    "total_vat": "<number | null>",
    "total_with_tax": "<number | null>",
    "total_with_tax_currency": "<ISO 4217 code | null>"
  },
  "validation": {
    "line_items_sum_check": "PASS | FAIL",
    "tax_calculation_check": "PASS | FAIL",
    "currency_consistency_check": "PASS | FAIL",
    "totals_consistency_check": "PASS | FAIL",
    "duplicate_invoice_check": "PASS | FAIL | SKIPPED"
  },
  "anomalies": [
    {
      "severity": "CRITICAL | HIGH | MEDIUM | LOW | INFO",
      "field": "<dot-path to field, e.g. 'line_items[2].amount'>",
      "issue": "<string describing the problem>"
    }
  ]
}
```

### Field Rules

- **Dates**: always `YYYY-MM-DD`.
- **Amounts**: always numeric. Interpret European comma decimals (`1000,2` → `1000.20`), accounting parentheses (`(1000)` → `-1000`), and `CR` suffix as negative.
- **Currencies**: always ISO 4217 (e.g. `USD`, `EUR`, `JPY`).
- **Countries**: always ISO 3166-1 alpha-2 (e.g. `CZ`, `US`).
- **Anomalies**: ordered by severity descending (CRITICAL first), then by field path.
- **Validation**: `SKIPPED` only when a check is not applicable (e.g. `duplicate_invoice_check` with a single document).
- **`note`** on line items: only present when the raw value required interpretation (e.g. unusual notation). Set to `null` otherwise.

### Batch Summary Table

After the JSON block, output a markdown table with one row per document:

| Document | Type | Issue Date | Subtotal | Total w/ Tax | Anomalies | Highest Severity |
|---|---|---|---|---|---|---|
| `<title or filename>` | invoice | YYYY-MM-DD | 0.00 | 0.00 | N | LEVEL |

Follow the table with a numbered list of critical/high findings across all documents.

## Integrations
User can paste or upload documents. Output JSON can be copied to clipboard or saved for ERP import.
