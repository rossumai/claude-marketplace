# Rossum Business Rules & Validation — Full Reference

Rossum validates extracted data and blocks automation in two ways, both covered here: the modern **native Rossum Rules** (`POST /v1/rules`) and the **legacy Business Rules Validation Store extension**. Both run at validation time to surface messages and block confirmation/automation of invalid documents.

## Contents
- [Overview: two ways to validate](#overview-two-ways-to-validate)
- [Native Rossum Rules](#native-rossum-rules)
- [Legacy Business Rules Validation extension](#legacy-business-rules-validation-extension)
- [Choosing between them](#choosing-between-them)

## Overview: two ways to validate

| | Native Rossum Rules | Business Rules Validation extension |
|---|---|---|
| What it is | Platform-native entity (`/v1/rules`) | A Store hook you install and configure |
| Config | `trigger_condition` + `actions[]` | a `checks[]` array |
| Expression dialect | TxScript `field.X` (see `txscript-reference`) | bespoke `{field}`-brace engine (documented below) |
| Status | Modern, recommended for new work | Legacy; still present in older implementations |

Both surfaces evaluate at validation time, emit messages (error/warning/info), and can block automation. They are independent — do not mix the two expression dialects. (Positioning/deprecation reflects current practice; confirm specifics against the official Rossum docs.)

## Native Rossum Rules

A native Rule (`POST /v1/rules`) evaluates a single boolean `trigger_condition` at validation time; when it is `True`, the rule emits one or more `actions` (messages, automation blockers, show/hide toggles). The `trigger_condition` is a TxScript expression using Python-style `field.X` access — for the expression language, helpers, and the defensive `not is_empty(field.X)` guard convention, see the `txscript-reference` skill. This is a different surface from the Business Rules Validation extension's `{field}`-brace engine below; do not mix syntaxes.

### Rule JSON shape

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

### Polarity: `trigger_condition` is the FIRE predicate

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

### Rule.actions — types and payload shape

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

#### Conventional action pairings

The patterns that recur across well-formed Rossum rules:

1. **Message + blocker pair.** When a rule warrants an automation blocker (`automation_blocker: true` on the source), it almost always also has a `show_message` with the same `content` and `schema_id`. The user sees the banner; export is also halted. If the source check carries an `automation_blocker: true`, emit both actions — don't pick one.
2. **Tag-fire + reveal pair.** When the trigger is `not is_empty(field.<X>_tag)` (a "tag" field populated by MDH or another hook only when there's a problem), pair the `show_message` with a `show_hide_field` revealing `<X>_tag` and related context fields. Tag fields are conventionally hidden by default and become visible when populated — the `show_hide_field` action is what makes them visible.
3. **Document-type → hidden-section.** When the trigger gates on `field.document_type == '...'` (e.g. credit note vs. invoice), pair it with a `show_hide_field` revealing the section relevant to that document type.

A single rule may combine pairings (e.g. a tag-fire rule with a message+blocker+reveal triplet). Pair conventions are additive — pick whichever apply.

## Legacy Business Rules Validation extension

The Business Rules Validation extension is a Store hook that validates extracted data using its own expression engine. It runs at the end of the extension chain to prevent confirmation/automation of invalid documents. Configuration is a `checks[]` array; each check has a `rule` (the `{field}`-brace expression), a `message`, a `type` (`error`/`warning`/`info`), an `automation_blocker` flag, optional `queue_ids` scoping, and an optional `condition`. A single check can only work with one table.

**Configuration**:
```json
{
  "checks": [
    {
      "rule": "has_value({document_id})",
      "message": "Invoice number must not be empty",
      "type": "error",
      "automation_blocker": true,
      "active": true,
      "queue_ids": [],
      "condition": ""
    }
  ]
}
```

### Expression Engine Syntax

**Operators**: `+`, `-`, `/`, `//`, `*`, `%`, `and`, `or`, `xor`, `==`, `!=`, `<`, `>`, `<=`, `>=`

**Data types**: integer, float, string, date. Auto-cast order: float → integer → date → string.

**Manual casting**: `int()`, `float()`, `date()` (requires `YYYY-MM-DD`), `str()`

**Empty checks**: `has_value({field})`, `is_empty({field})` (do NOT use `== ''`)

**Aggregation**: `all()`, `any()`, `sum()`, `min()`, `max()`, `len()`, `unique_len()`, `first_value()`

**Filter**: `filter({column}, [0, None])` — removes specified values

**Defaults**: `{value, default=0}` or `{value, default=value('other_field')}`

**Date functions**: `today()`, `timedelta(days=N)`, `timedelta(years=N, months=N)`

**String functions**: `substring(search, value)`, `regexp(pattern, value, ignore_case=True)`, `similarity(value, search)` (Levenshtein), `list_contains(column, search)`

**Examples**:
```
{issue_date} > "2023-01-01"
{item_price} * {item_amount} == {item_total}
sum({item_total}) == {total_price}
today() + timedelta(days=2) > {due_date}
```

**Limitation**: One rule can only work with one table.

---

## Choosing between them

Prefer **native Rules** for new work — they're platform-native, versioned with the org config, and don't require installing a Store extension. The **Business Rules Validation extension** persists in older implementations; document and migrate it to native Rules when practical. (Confirm the current deprecation status against the official Rossum docs before asserting it to a customer.)
