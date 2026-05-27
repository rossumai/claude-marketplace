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

#### Writing to date-typed fields

A schema field with `"type": "date"` (e.g. `date_issue`, `date_due`, custom Normalized fields) must receive a **Python `date`/`datetime` object** or a **bare ISO `YYYY-MM-DD` string** — NEVER the schema's display-format string (`DD.MM.YY`, `MM/DD/YYYY`, etc.). Writing a display-format string fails silently: TxScript emits the operation, Rossum can't parse it, the field stays empty, no error surfaces.

```python
from datetime import date, datetime

# ✅ Works — date object
t.field.date_issue_normalized = date(2026, 1, 28)

# ✅ Works — datetime object (time component dropped)
t.field.date_issue_normalized = datetime(2026, 1, 28, 12, 0)

# ✅ Works — ISO yyyy-mm-dd string
t.field.date_issue_normalized = "2026-01-28"

# ❌ FAILS SILENTLY — display-format string, even if it matches the schema's `format`
t.field.date_issue_normalized = "28.01.26"
```

The schema's `format` property (`DD.MM.YY`, `MM/DD/YYYY`, etc.) only controls how Rossum **renders** a stored date in the UI; it does NOT control the format you write. Storage is always ISO.

Copying from one date field to another — use `setattr` and pass the underlying date object (NOT the display string from `content.value`):

```python
src = t.field.date_issue                          # Python date object
setattr(t.field, "date_issue_normalized", src)    # writes the underlying date
```

If you're reading from a raw payload content tree (when `t.field` isn't available), use `content.get("normalized_value")` — which is the ISO string — NOT `content.get("value")`, which is the display variant.

#### Reading datapoint metadata (OCR raw text, confidence, position, etc.)

`t.field.<id>` returns a value object that quacks like the underlying Python type (str, date, float). To reach the OCR metadata, use the `.attr` proxy:

```python
field_val = t.field.date_issue                          # date or NoneValue
raw_ocr   = field_val.attr.rir_raw_text                 # OCR raw text, e.g. "08/01/2026"
confidence = field_val.attr.rir_confidence              # float
field_id   = field_val.attr.id                          # datapoint id
position   = field_val.attr.position                    # [x1, y1, x2, y2] or None
```

`field_val.attr.<name>` resolves against `field.attrs["content"]` first, then `field.attrs`, then the schema. So any OCR-side key (`rir_text`, `rir_raw_text`, `rir_page`, `rir_position`, `rir_confidence`, `ocr_text`, `ocr_raw_text`) is reachable via `.attr.<name>`.

Don't dereference these via an intermediate variable — operations on the value container lose the `.attr` accessor:

```python
# ✅ direct access keeps metadata reachable
raw = t.field.date_issue.attr.rir_raw_text

# ❌ losing the container drops .attr
x = t.field.date_issue
x = x.strip()              # x is now plain str — .attr is gone
raw = x.attr.rir_raw_text  # AttributeError
```

#### Empty / missing field detection

A schema datapoint that's defined but has no extracted value comes back as a `NoneValue` wrapper — **not Python `None`**. Don't rely on naive `is None` checks:

```python
v = t.field.date_due

# ❌ wrong — NoneValue is not None
if v is None:
    ...

# ❌ wrong — str(NoneValue) returns the literal string "None"
if not str(v):
    ...

# ✅ use the helper
from txscript import is_empty
if is_empty(v):
    ...

# ✅ or check against the falsy semantics built into NoneValue
if not v:
    ...
```

Writing the captured value into another field without an empty check leaves you with literal `"None"` strings on the target. Always guard with `is_empty(...)`.

#### Reading vs walking the raw payload content tree

`payload["content"]` is **not guaranteed to be populated** on every event. In particular it's often empty on `annotation_content.updated` and `annotation_content.started`. Hooks that walk the raw tree (`for dp in content_tree:`) end up doing nothing on those events.

**Prefer `t.field.<id>`** for all field reads (and `t.field.<id>.attr.<name>` for metadata). `t = TxScript.from_payload(payload)` populates the field accessor regardless of whether the event payload shipped the full content tree.

```python
# ✅ event-agnostic
for sid in ("date_issue", "date_due"):
    v = getattr(t.field, sid, None)
    if is_empty(v):
        continue
    raw = v.attr.rir_raw_text or ""
    # ... logic ...

# ❌ misses dates on events where payload.content is empty
for dp in get_content_tree(payload):
    if dp.get("schema_id") in ("date_issue", "date_due"):
        ...
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

The second argument is the field the message is attached to (so the reviewer sees a marker on that field in the UI). Either pass a real field reference or omit the argument entirely — **don't pass `None`**, that breaks the call silently and the hook may not visibly run. If you need to inspect whether a hook fired at all, check the per-hook Logs tab in Rossum admin → Extensions; `t.show_info` messages also appear there alongside any `print(...)` output.

### Automation Control
```python
# Block automation (prevents auto-export)
t.automation_blocker("Reason message", t.field.field_id)
```

### Response
```python
return t.hook_response()
```

### Event Triggers (`events:` in hook config)

A serverless function reacts to one or more annotation events. Pick the right set based on when your logic needs to run:

| Event | Fires when | Notes |
|-------|------------|-------|
| `annotation_content.initialize` | First time content lands on the annotation (after initial extraction, or after a re-extract triggered by `PATCH status=importing`) | The reliable "every annotation eventually fires this" event |
| `annotation_content.started` | Annotation enters review (status transitions to `reviewing`) | Use for setup logic that should run when a reviewer opens the doc |
| `annotation_content.updated` | Server-side content updates (e.g. another hook wrote a field) | Catches hook-to-hook chains |
| `annotation_content.user_update` | User edits a field in the UI | **Deprecated as of mid-2025** — prefer `updated`; UI edits still fire `updated` |

To re-fire a hook on an existing annotation (e.g. after changing the hook config), trigger a re-extract via the API:

```http
PATCH /api/v1/annotations/{id}
Content-Type: application/json

{"status": "importing", "rir_poll_id": null, "messages": []}
```

This resets the annotation to `importing`, extraction re-runs, and `annotation_content.initialize` fires again — with the new hook config in effect. Old hook messages on the annotation are NOT auto-cleared by a successful re-run; passing `"messages": []` in the PATCH wipes them so the customer sees a fresh state.

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
