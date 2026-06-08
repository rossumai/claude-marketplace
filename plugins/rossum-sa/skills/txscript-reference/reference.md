# Rossum Field-Expression Reference: TxScript, Rules, Formulas

## Overview

Rossum has three surfaces where you write Python-flavored expressions over annotation data:

| Surface | Where | What runs | Use for |
|---|---|---|---|
| **TxScript serverless hook** | `.py` file next to a hook JSON | Full Python 3.12 in AWS Lambda, with the `TxScript` helper class | Complex multi-field validation, API calls, MongoDB lookups, multi-step enrichment, mutating line items |
| **Native Rossum Rule** | `Rule.trigger_condition` (string) + `Rule.actions` (array) on the Rule entity (`POST /v1/rules`) | A boolean Python expression evaluated at validation time | Single-shot validations that show a message, add an automation blocker, or show/hide a field |
| **Schema-field formula** | `formulas/<field_id>.py` next to `schema.json` | A Python expression (single-line or multi-line; last expression returns) that derives the field's value from other fields | Computing one field from others, normalizing values, conditional defaults |

All three share the same Python sublanguage and helpers. They differ in *evaluation context*: hooks run on hook events with full I/O, Rules run at validation time and emit actions, Formulas run whenever inputs change and produce a value.

The TxScript hook section (immediately below) is the original audience of this reference. The Rule and Formula sections are at the bottom of the file.

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

## Applying Labels to Annotations

Labels are **not** applied via the hook response. There's no `labels` field on `hook_response()` and no TxScript helper for them — applying or removing a label requires an explicit HTTP `POST` to the `/v1/labels/apply` endpoint from inside the hook.

The hook payload already carries everything you need: `payload["base_url"]` for the org URL, `payload["rossum_authorization_token"]` for the auth, and `payload["annotation"]["url"]` for the annotation to label.

### Canonical pattern

```python
import requests

def apply_label_operations(
    base_url: str,
    auth_token: str,
    annotation_url: str,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> None:
    """Apply and/or remove labels on an annotation via /v1/labels/apply."""
    resp = requests.post(
        f"{base_url}/api/v1/labels/apply",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={
            "operations": {
                "add":    add_labels or [],
                "remove": remove_labels or [],
            },
            "objects": {"annotations": [annotation_url]},
        },
        timeout=10,
    )
    resp.raise_for_status()
```

Call shape from inside `rossum_hook_request_handler`:

```python
def rossum_hook_request_handler(payload: dict) -> dict:
    base_url       = payload["base_url"]
    auth_token     = payload["rossum_authorization_token"]
    annotation_url = payload["annotation"]["url"]

    LABEL_NEEDS_REVIEW_ID = 3878
    label_url = f"{base_url}/api/v1/labels/{LABEL_NEEDS_REVIEW_ID}"

    if some_condition(payload):
        apply_label_operations(base_url, auth_token, annotation_url,
                               add_labels=[label_url])
    return TxScript.from_payload(payload).hook_response()
```

### Payload shape

| Field | Type | Notes |
|---|---|---|
| `operations.add` | list of label URLs | **Full URL** (`<base_url>/api/v1/labels/<id>`), not bare id |
| `operations.remove` | list of label URLs | Same shape |
| `objects.annotations` | list of annotation URLs | Pass `payload["annotation"]["url"]` directly |

Both `add` and `remove` can be sent in the same call — useful for "swap label A for label B" transitions.

### Gotchas

- **Pass label URLs, not bare IDs.** `"add": [3878]` is silently ignored; `"add": ["https://elis.rossum.app/api/v1/labels/3878"]` works. Build the URL from `payload["base_url"]` so the same hook code works across orgs.
- **The label definition must exist on the queue first.** `/v1/labels/apply` doesn't create labels; it just attaches them. Create label definitions via the queue UI or `POST /v1/labels` before the hook can reference them.
- **Idempotent re-apply is fine.** Re-applying a label that's already on the annotation is a no-op (the endpoint dedupes). To check the current state before mutating, inspect `payload["annotation"].get("labels", [])` — that's the list of already-applied label URLs.
- **Only works while the annotation is mutable.** `to_review`, `reviewing`, and `confirmed` are fine; `exported` / `in_workflow` may reject. Check status before applying if the hook can fire on terminal states.
- **Hard-code label IDs as constants at the top of the hook.** Label IDs are per-org; treat them like queue IDs — a `LABEL_NEEDS_REVIEW_ID = 3878` constant block makes per-environment overrides obvious during prd2 deploys.

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

---

# Native Rossum Rule reference

> **Note — not the same as Business Rules Validation.** The BRV extension covered in `rossum-reference` uses a different `{field}`-brace expression engine (e.g. `has_value({document_id})`). Native Rules use Python-style `field.X` attribute access (e.g. `is_empty(field.document_id)`). The two are independent surfaces; do not mix syntaxes.

A **Rule** (`POST /v1/rules`) defines a single boolean `trigger_condition` that, when evaluated to `True` at validation time, emits one or more `actions` (messages, automation blockers, show/hide toggles).

## Rule JSON shape

```json
{
  "id": <auto-assigned by API; do not rely on client-supplied id>,
  "name": "Human-readable label",
  "description": "Optional free-text",
  "enabled": true,
  "trigger_condition": "<Python boolean expression — fires when TRUE>",
  "actions": [
    { "id": "<arbitrary stable slug>", "enabled": true,
      "type": "show_message",
      "event": "validation",
      "payload": { "type": "error|warning|info",
                   "content": "<message text>",
                   "schema_id": "<field to anchor the message on>" } },
    { "id": "<arbitrary stable slug>", "enabled": true,
      "type": "add_automation_blocker",
      "event": "validation",
      "payload": { "content": "<blocker text>",
                   "schema_id": "<field to anchor the blocker on>" } }
  ],
  "queues": ["https://api.elis.rossum.ai/api/v1/queues/<id>", ...],
  "organization": "https://api.elis.rossum.ai/api/v1/organizations/<id>"
}
```

`action.id` is a non-empty string identifying the action within the rule. The API does not constrain the format — any string unique within the rule works. Use whichever convention is consistent within your project (semantic slugs, indexed pairs, UUIDs all work). Keep ids stable across rule versions if you care about diff readability.

## trigger_condition expression language

A subset of Python. The expression evaluates to a boolean; the rule fires when it is `True`. The expression must NOT have side effects.

**Available built-ins:**

| Helper | Purpose | Example |
|---|---|---|
| `field.<schema_id>` | Read a field value | `field.amount_total` |
| `field.<line_item_id>` | In a line-item rule: the current line's value | `field.item_total_base` |
| `field.<line_item_id>.all_values` | List of values across all lines | `sum(field.item_total_base.all_values)` |
| `is_empty(x)` | True if `x` is `None`, `""`, or `"<not captured>"` sentinel | `is_empty(field.document_id)` |
| `is_set(x)` | Inverse of `is_empty` | `is_set(field.payment_reference)` |
| `default_to(x, default)` | Return `x` if not empty, else `default` | `default_to(field.amount_rounding, 0)` |
| `len(x)` | List/string length | `len(field.line_items)` |
| `sum(x)`, `min(x)`, `max(x)` | Standard aggregations | `sum(field.item_total_base.all_values)` |
| `any(x)`, `all(x)` | Standard boolean aggregations over a list | `any(is_empty(x) for x in field.item_account_segment_1.all_values)` |
| `abs(x)` | Absolute value | `abs(a - b) >= 0.01` |
| `bool(x)`, `str(x)`, `int(x)`, `float(x)` | Type coercion | `bool(re.search(p, str(field.x)))` |
| `re.search(pattern, string)` | Regex search (returns Match or None) | `re.search('^AU.{4,}', str(field.x))` |
| `date.today()` | Today's date for comparisons | `field.date_issue > date.today()` |

`field.<id>` resolves to:
- A scalar (str / int / float / date) for header fields
- Inside a line-item rule context: the current row's scalar
- `field.<line_item_id>.all_values`: a list across all line items (use for header rules that aggregate over the table)

**Line-item rule semantics.** When a rule is attached to a line-item field (e.g. `schema_id: "item_order_id"` in the message payload), the trigger evaluates **once per line item** and fires per row. `field.item_X` is the current row. Aggregations require `.all_values`.

## Polarity: `trigger_condition` is the FIRE predicate

`trigger_condition` is the **fire** predicate — the rule fires (emits actions) when the expression evaluates to `True`. Read the rule's `message` text to confirm intent: the message describes the **problem state**, and `trigger_condition` should be `True` in that state.

Example — a rule that requires `item_order_id` to start with `"AU"`:
```python
# Rule fires (and shows the error) when the ID is non-empty AND does not start with AU
trigger_condition = (
    "not is_empty(field.item_order_id) and "
    "not bool(re.search('^AU.{4,}', str(field.item_order_id)))"
)
# message: "PO number must start with AU"
# actions: show_message(error) + add_automation_blocker on item_order_id
```

Two common ways to get polarity wrong:
- **Porting a check whose source expresses the OK state.** If you have an expression that means "the data is good", invert it before putting it into `trigger_condition`.
- **Problem-indicator fields.** Some schema fields are populated *only when there's a problem* — common naming conventions include `*_tag`, `*_mismatch_tag`, `*_match`, `*_inactive`, `*_indicator`, `*_issue`. For these, the rule fires on `not is_empty(field.X)`, not on `is_empty(...)`. Read the field name and the rule message text together: if the message describes a *problem* (e.g. "Fraudulent supplier detected", "ELE name mismatch") and the field name reads like a positive-detection marker, the fire condition is `not is_empty(...)`.

In either case, the rule's `message` text reads naturally in the fire state — use that as the primary disambiguation when the field-naming convention isn't conclusive.

## Defensive `not is_empty(field.X)` guard convention

Native Rule expressions are evaluated against arbitrary field values, including empty/None. Several common operators raise or produce surprising results on empty input:

| Operator | Empty behavior | Guard needed? |
|---|---|---|
| `re.search(p, x)` | Raises TypeError if `x` is None | **Yes**, wrap in `not is_empty(field.X) and` |
| Numeric `>`, `<`, `+`, `-` | Compares/concatenates `None` → error or surprise | **Yes**, or use `default_to(field.X, 0)` |
| Equality `==` to a non-empty literal | Empty value never equals a non-empty literal — guard is redundant but idiomatic | Optional; canonical conventions include it |
| `is_empty` / `is_set` | Designed for empty inputs | **No**, this *is* the guard |
| `field.X.all_values` aggregations (`sum`, `len`) | `sum([])` = 0, `len([])` = 0 — safe | **No** for length, but guard each value if you'll do arithmetic on it |

Ground-truth Rossum rules consistently include the defensive `not is_empty(field.X)` prefix even when logically redundant (e.g. before `field.X == 'literal'`). Mirror that convention so a downstream reader doesn't have to mentally prove the guard isn't needed.

## Rule.actions — types and payload shape

Three action types are commonly used at validation time:

**`show_message`** — surface a banner on the annotation:
```json
{ "id": "rule-slug-msg", "enabled": true, "type": "show_message", "event": "validation",
  "payload": {
    "type": "error" | "warning" | "info",   // banner severity
    "content": "Free-text message shown to the user",
    "schema_id": "field_to_anchor_on"        // where the banner attaches
  } }
```

**`add_automation_blocker`** — prevent automatic export of the annotation:
```json
{ "id": "rule-slug-block", "enabled": true, "type": "add_automation_blocker", "event": "validation",
  "payload": {
    "content": "Free-text reason; same as the show_message content by convention",
    "schema_id": "field_to_anchor_on"
  } }
```

**`show_hide_field`** — toggle field visibility based on the trigger:
```json
{ "id": "rule-slug-sh", "enabled": true, "type": "show_hide_field", "event": "validation",
  "payload": {
    "schema_id": "primary_field_id",          // legacy single-id form
    "schema_ids": ["field_a", "field_b"]      // newer multi-id form (preferred)
  } }
```
The field(s) listed are **shown** when the rule fires and **hidden** when it does not.

`schema_ids` (plural array) is the canonical payload key — every existing rule with a `show_hide_field` action carries it. `schema_id` (singular) is a legacy key that older rules also carry alongside `schema_ids` (typically with the same primary field name). For new rules, emit only `schema_ids`. When patching a rule that already has both keys, preserve both to avoid an unintended schema-shape change.

### Conventional action pairings

The patterns that recur across well-formed Rossum rules:

1. **Message + blocker pair.** When a rule warrants an automation blocker (`automation_blocker: true` on the source), it almost always also has a `show_message` with the same `content` and `schema_id`. The user sees the banner; export is also halted. If the source check carries an `automation_blocker: true`, emit both actions — don't pick one.
2. **Tag-fire + reveal pair.** When the trigger is `not is_empty(field.<X>_tag)` (a "tag" field populated by MDH or another hook only when there's a problem), pair the `show_message` with a `show_hide_field` revealing `<X>_tag` and related context fields. Tag fields are conventionally hidden by default and become visible when populated — the `show_hide_field` action is what makes them visible.
3. **Document-type → hidden-section.** When the trigger gates on `field.document_type == '...'` (e.g. credit note vs. invoice), pair it with a `show_hide_field` revealing the section relevant to that document type.

A single rule may combine pairings (e.g. a tag-fire rule with a message+blocker+reveal triplet). Pair conventions are additive — pick whichever apply.

---

# Schema-field formula expressions

A schema-field formula derives a field's value from other fields. It's a Python expression (or multi-expression block) that evaluates whenever its inputs change.

## Where formulas live

Locally:
```
queues/<Queue Name>_[<id>]/
├── schema.json                 # the schema — `formula` property per field is stripped on pull
└── formulas/
    └── <field_id>.py           # the formula source — edit THIS file
```

`prd2 pull` extracts `schema.json[fields].formula` strings into `formulas/<field_id>.py`. `prd2 push` merges them back. **Never edit the `formula` property inside `schema.json` directly.**

## Formula shape

Single-expression formula (most common):
```python
default_to(field.payment_reference, "")
```

Multi-expression formula — intermediate `name = …` bindings; the **last expression** is the field's value:
```python
ds = default_to(field.po_line_description_match, field.supplier_default_item_desc_match)
if not is_empty(field.item_order_item_match):
    ds
elif field.item_prepaid == "Yes":
    ds + " - " + " - ".join(p for p in [str(field.item_date_prepaid_start)[:7],
                                          str(field.item_date_prepaid_end)[:7]] if p)
else:
    ds
```

Last-expression-returns means the formula reads like a normal Python REPL session: name your intermediates, return the final value.

## Header vs. line-item formulas

| Schema-field type | Evaluation context | `field.X` semantics |
|---|---|---|
| Header field formula | Once per annotation | `field.X` = the annotation-level value |
| Line-item field formula | Once per line item | `field.item_X` = current row's value; `field.X` (header field) still resolves |
| Header formula reading line items | Per annotation, aggregating | Use `field.item_X.all_values` for the list, then `sum()` / `len()` / list-comp |

The `len(field.line_items) > 0` guard is the canonical "any rows present?" check before computing a line-item-derived header value.

## Helper inventory

(Same helpers as Rule trigger_condition — they share the runtime.)

| Helper | Purpose | Example |
|---|---|---|
| `default_to(x, default)` | Return `x` if non-empty, else `default` | `default_to(field.item_quantity, 1)` |
| `is_empty(x)` / `is_set(x)` | Empty / non-empty test | `is_empty(field.payment_reference)` |
| `len(x)` | List/string length | `len(field.line_items)` |
| `sum`, `min`, `max`, `any`, `all` | Standard aggregations | `sum(field.item_total_base.all_values)` |
| `str(x)`, `int(x)`, `float(x)`, `bool(x)` | Type coercion | `str(field.item_date_prepaid_start)[:7]` |
| `re.search`, `re.sub`, `re.match` | Regex | `re.sub(r'[^0-9]', '', field.iban)` |
| `date.today()`, `timedelta(...)` | Date arithmetic | `field.date_issue + timedelta(days=30)` |

## The "absorb" pattern

When a helper formula field is consumed by **exactly one** downstream formula, you can **inline** its definition into the consumer to reduce schema field count. Pattern:

```python
# Before:  separate field `desc_start` with its own formula, then `description_export` reading field.desc_start
# After: define `ds` locally in `description_export` itself

ds = default_to(field.po_line_description_match,
                field.supplier_default_item_desc_match,
                field.sender_match if is_set(field.sender_match) else None)
# … then use `ds` directly in the rest of this formula
```

The absorbed helper field can be deleted from the schema. The trade-off is readability vs. schema cardinality: absorb when the helper is short and used in one place; keep separate when the helper is reused or non-trivial.

This pattern is named "absorb" because the consumer formula absorbs the producer's definition. It's not a Rossum-specific construct — it's a refactoring move enabled by formulas being arbitrary Python expressions.

## Multi-variant formulas across queues

A schema field can have different formulas across queues (when queues share a schema family but diverge in details — e.g. an IT/FR variant of a date-aggregation formula). Each queue's local `formulas/<field_id>.py` is independent.

There is **no** built-in "switch on queue.id inside a formula" — queue identity isn't a value the formula language exposes. If the divergence is data-driven (depends on a field value), inline the branching inside one formula; if it's policy-driven (depends on which queue this is), emit per-queue formula files.

