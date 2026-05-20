# Rossum Transaction Scripts (TxScripts) & Serverless Functions Guide

## Overview

Rossum serverless functions (hooks) run as AWS Lambda functions in a Python 3.12 runtime. The `txscript` module provides a high-level API for interacting with document annotations during processing events.

## TxScript Baseline Pattern

```python
from txscript import TxScript, default_to, is_empty, substitute
import math

def rossum_hook_request_handler(payload: dict) -> dict:
    """
    Entry point for Rossum serverless hook.
    Handles field normalization, validation, and enrichment triggers.
    """
    t = TxScript.from_payload(payload)

    # --- Normalization ---
    if not is_empty(t.field.document_id):
        t.field.document_id = substitute(r"[^A-Za-z0-9]", "", t.field.document_id)

    # --- Validation ---
    rounding = 2
    if not is_empty(t.field.amount_total) and not is_empty(t.field.amount_total_base):
        calc_total = round(
            default_to(t.field.amount_total_base, 0) + default_to(t.field.amount_total_tax, 0),
            rounding
        )
        if not math.isclose(t.field.amount_total, calc_total, abs_tol=0.01):
            msg = f"Discrepancy: Extracted total {t.field.amount_total} != Calculated {calc_total}"
            t.show_warning(msg, t.field.amount_total)
            t.automation_blocker(msg, t.field.amount_total)

    # --- Enrichment ---
    if not is_empty(t.field.sender_name) and is_empty(t.field.supplier_gl_code):
        t.show_info("Triggering MongoDB enrichment for GL codes...")

    return t.hook_response()
```

## Key TxScript API

### Initialization
```python
t = TxScript.from_payload(payload)
```

### Field Access
```python
# Read field value
value = t.field.document_id

# Write field value
t.field.document_id = "INV-12345"
```

### Utility Functions
| Function | Description |
|----------|-------------|
| `is_empty(value)` | Returns True if value is None or empty string |
| `default_to(value, default)` | Returns value if not empty, otherwise default |
| `substitute(pattern, replacement, value)` | Regex substitution on value |

### User Messages
```python
t.show_info("Informational message", t.field.field_id)      # Blue info
t.show_warning("Warning message", t.field.field_id)          # Yellow warning
t.show_error("Error message", t.field.field_id)              # Red error
```

### Automation Control
```python
# Block automation (prevents auto-export)
t.automation_blocker("Reason message", t.field.field_id)
```

### Response
```python
return t.hook_response()
```

## Creating / Replacing Line-Item Rows

Line-item tables (e.g. `line_items_copied`, `line_items`) are multivalue fields. To add or replace rows you return an `operations` list in the hook response — TxScript's `t.field.<table>` is read-only for structural changes.

Each operation has the shape:

```python
{
    "op": "add" | "replace" | "remove",
    "id": <multivalue_id>,        # the table multivalue field ID, NOT the row ID
    "value": {                    # required for add/replace
        "<child_schema_id>": {"content": {"value": "<str>"}},
        ...
    },
    # optional: "tuple_id": <row_id>   # required for replace/remove of an existing row
}
```

Pattern — wipe the table and insert one synthetic row from header values (single-line generator):

```python
def rossum_hook_request_handler(payload: dict) -> dict:
    annotation = payload["annotation"]["content"]
    line_items_mv_id = next(
        f["id"] for f in _walk(annotation)
        if f["schema_id"] == "line_items_copied"
    )

    # 1) remove every existing row
    remove_ops = [
        {"op": "remove", "id": line_items_mv_id, "tuple_id": tup["id"]}
        for tup in _rows_of("line_items_copied", annotation)
    ]

    # 2) insert exactly one row built from header fields
    add_op = {
        "op": "add",
        "id": line_items_mv_id,
        "value": {
            "item_amount_total_copied":  {"content": {"value": str(_header("amount_due", annotation))}},
            "item_description_copied":   {"content": {"value": _header("description", annotation)}},
        },
    }

    return {"operations": remove_ops + [add_op], "messages": []}
```

Gotchas (each one cost real iterations in past sessions):

- **`id` is the multivalue (table) field ID**, not a row id. Get it from the table field, not from a row.
- **`add` on an existing `tuple_id` returns HTTP 409.** If you're not sure a row exists, use `replace` with `tuple_id`, or `remove` then `add`.
- **`replace` requires `tuple_id`**; `add` must NOT include `tuple_id`.
- **`value` keys are schema IDs of the child fields**, wrapped as `{"content": {"value": ...}}`. Plain strings/numbers will be silently ignored.
- Only ever push **strings** into `content.value` — even for numbers and dates. Coerce with `str(...)` or `f"{x:.2f}"` before emitting.
- The user-facing `line_items` table is AI-managed; for a downstream working copy use a separate `line_items_copied` schema you fully own. Don't mutate `line_items` from a hook.
- For debugging, write a transient `_dbg_op_count` / `_dbg_last_status` field into the schema and set it from the hook — far faster than re-running with extra logs.

## Best Practices

### Code Style
- Use type hints (`dict`, `list`, `str`) for self-documenting functions
- Favor meaningful variable names and modular code
- Always verify `payload["event"]` and `payload["action"]` before execution in raw hooks

### Formula Fields vs Serverless Functions
- **Prefer Formula Fields** for simple text transformations (lowercase, concatenation, etc.) — stored at schema level, copied automatically between queues
- **Use Serverless Functions** for complex logic: API calls, multi-field validation, conditional enrichment, MongoDB lookups

### Validation Patterns
```python
# Face value check
if not math.isclose(t.field.amount_total, calc_total, abs_tol=0.01):
    t.automation_blocker("Total mismatch", t.field.amount_total)

# Required field
if is_empty(t.field.document_id):
    t.automation_blocker("Invoice number is required", t.field.document_id)

# Date range check (use datetime)
from datetime import datetime, timedelta
issue = datetime.strptime(t.field.date_issue, "%Y-%m-%d")
due = datetime.strptime(t.field.date_due, "%Y-%m-%d")
if (due - issue).days > 120:
    t.show_warning("Due date is more than 120 days from issue date", t.field.date_due)
```

### SAP Integration Patterns
- **S4 HANA Public Cloud**: Use standard REST/OData APIs directly
- **S4 HANA Private Cloud / ECC**: Generate IDOCs via MEGA export
  - `INVOIC02` for AP invoices (both FICO non-PO and MIRO PO-backed)
  - `ORDERS05` for AR sales orders
  - Route through customer's middleware (Mulesoft, Azure, BTP, SFTP)

### MongoDB / MDH Enrichment
- Complex MongoDB queries should be handled in dedicated MDH hook configurations
- Use `$search` for fuzzy matching, `$match` for exact matching
- Normalize data before comparison (lowercase, strip spaces)
- For remit-to-address matching: use IBAN/account number last-5-chars pattern, BIC/SWIFT regex, address fuzzy match, fallback to all RTAs

## Schema Field Mapping

Key field conventions used in Rossum schemas:

| Schema ID | Description | Type |
|-----------|-------------|------|
| `document_id` | Invoice number | string |
| `date_issue` | Invoice date | date |
| `date_due` | Due date | date |
| `amount_total` | Total amount | number |
| `amount_total_base` | Net amount (before tax) | number |
| `amount_total_tax` | Tax amount | number |
| `sender_name` | Vendor/supplier name | string |
| `sender_ic` | Vendor tax ID / company ID | string |
| `sender_dic` | Vendor VAT ID | string |
| `sender_address` | Vendor address (full) | string |
| `recipient_name` | Buyer/recipient name | string |
| `iban` | Bank IBAN | string |
| `bic` | Bank SWIFT/BIC code | string |
| `account_num` | Bank account number | string |
| `order_id` | Purchase order number | string |
| `item_description` | Line item description | string |
| `item_quantity` | Line item quantity | number |
| `item_amount_total` | Line item total | number |

The `rir_field_names` attribute in schema maps OCR predictions to internal field IDs.
