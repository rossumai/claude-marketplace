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

### Field constraints & create/link gotchas

- **`name` and `description` are capped at 255 characters.** A `POST`/`PATCH /v1/rules` with a longer `description` returns **HTTP 400 `description: Ensure this field has no more than 255 characters.`** (`name` follows the same platform charfield limit). This bites repeatedly because rule descriptions naturally grow into paragraphs. Keep both short. Long *implementation rationale* does not belong in the rule at all — not in `description`, and **not** in the action `payload.content` (that is the reviewer-facing banner shown in the validation UI; keep those messages short and actionable). Put rationale in the deliverable's spec/plan or a comment on the `trigger_condition`. Sanity-check before pushing a locally-authored rule: `python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d['name']), len(d.get('description','')))" rule.json`.
- **Attaching a rule to a queue — and how it can end up unattached.** The rule↔queue link is a many-to-many relation mirrored on both sides (`rule.queues` ↔ `queue.rules`). The `POST /v1/rules` API *does* attach the rule when you pass `queues`/`queue_ids` — verified live: a rule created with `queue_ids:[<id>]` comes back with `queues:[<id>]` populated and immediately appears in `GET /v1/rules?queue=<id>`. It ends up unattached (`queues: []`, so it never evaluates on any document) in two cases: (a) you create it without passing queues; (b) a **prd2 `_[]`-placeholder push**, which creates the rule but does *not* send the rule-side `queues` — in a prd2 tree the link is declared on the queue side, so you must add the new rule's URL to `queue.json`'s `rules` array and push the queue. After any create, verify `GET /v1/rules/{id}` shows the intended `queues`; if empty, set them via `PATCH /v1/rules/{id}` (`queues`) / MCP `rossum_patch_rule` (`queue_ids`), or the prd2 `queue.json` route above.

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

Three action types are commonly used at validation time. **The list below is not the complete
set** — `actions[]` accepts more types than these (notably `change_queue`, which moves the
annotation to another queue; its `reimport` flag decides whether the target re-extracts the
document or inherits the existing content, and `reimport: false` is the case where the target
schema's formulas do **not** re-evaluate — see `txscript-reference` → *A queue move does NOT
recompute formulas*). Treat the three below as the validation-time workhorses, not an inventory.

**`show_message`** — surface a message in the validation UI (**not** on the annotation object;
see [Verifying a rule actually fired](#verifying-a-rule-actually-fired)):
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

### Verifying a rule actually fired

**Do not look for the message on the annotation.** `GET /v1/annotations/{id}` reports
`messages: []` regardless — a fired `show_message` never lands there. Reading the annotation is
the natural first move and it produces a false negative every time, which then gets misdiagnosed
as a broken `trigger_condition`.

A rule's output exists only in the **response body of the validation call** that evaluated it:

```
POST /v1/annotations/{id}/content/validate
{"actions": ["user_update", "started"]}
```

(The annotation must be in `reviewing` first — `POST /annotations/{id}/start` — or the call
returns HTTP 409. `["started"]` alone is rejected, so send both actions. **From Claude Code, use
`rossum_start_annotation` → `rossum_validate_content` → `rossum_cancel_annotation`**, not
`rossum_refire_annotation`: the refire wrapper is the better iteration primitive in general, but
its validate branch keeps only `updated_datapoints_count` and discards the messages, which are
the thing you are here for.)

Two places in that response answer "did it fire?":

1. **`matched_trigger_rules`** — an array of the rules whose `trigger_condition` evaluated
   `True` on this run. This is the cleanest fired/not-fired signal, because it is independent of
   whether the rule has any *visible* action: a rule whose only action is
   `add_automation_blocker`, or one whose `show_message` is `info`-level, still appears here.
2. **`messages[]`** — one entry per emitted `show_message`, each carrying a `detail` block that
   names the rule that produced it:

```json
{
  "type": "warning",
  "content": "<the action's payload.content>",
  "detail": {"rule_id": <rule id>, "rule_name": "<the rule's name>", "hook_name": "rules"}
}
```

`detail.rule_id` is what disambiguates *which* rule spoke when several rules anchor messages on
the same `schema_id` — matching on `content` text alone is unreliable once two rules share
wording.

> **Two different `detail` envelopes — do not reuse a parser across them.** The shape above is the
> one in the **validate response's** `messages[]`, where `detail` is an *object*. The
> **automation-blocker** payload nests its own `detail` under
> `content[].samples[].details.detail`, and there it is a *list* — code that walks blockers
> indexes `detail[0]`. Same key, different container; a walker written for one silently misreads
> the other.

**Check polarity in both directions.** A rule that fires when it should is only half the
evidence; a `trigger_condition` inverted or missing its `is_empty` guard often fires on
*everything*, which looks like success if you only ever test the problem case. Verify against
two annotations (or two states of one) — reading `matched_trigger_rules` from the raw response,
or `raw_messages` if you are going through `rossum_validate_content`:

| case | expectation |
|---|---|
| data **in the problem state** | rule id present in `matched_trigger_rules`; message emitted; blocker present if the rule adds one |
| data **in the OK state** | rule id **absent** from `matched_trigger_rules`; no message from that `rule_id` |

Only both rows together prove the predicate, not just its true branch.

**Corroborating signals**, when you need more than the validate response:

- `GET /v1/rules_execution_logs?annotation=<id>` — the per-evaluation record of every rule run
  against that annotation. The endpoint carries `trigger_condition_values` (what the expression
  actually saw) and the resolved `actions`, which is what you want when a condition fired on
  input you did not expect. The MCP wrapper `rossum_list_rule_execution_logs` compacts each row
  to `{rule_id, rule_name, queue_id, annotation_id, trigger_event, execution_result,
  execution_error, created_at, request_id}` — enough for "did it run and did it error", **not**
  enough to see the values; for those, read the endpoint. Either way this is the route for
  after-the-fact triage on a document you are not re-validating.
- `GET /v1/automation_blockers?annotation=<id>` (readable through `rossum_get`) — the durable
  side of an `add_automation_blocker` action. Unlike the message, a blocker *does* persist on the
  annotation, so it survives the validation call that created it.
- A rule that appears in neither `matched_trigger_rules` nor the logs may simply not be attached
  to the queue: check `GET /v1/rules/{id}` for a populated `queues` array (see
  *Field constraints & create/link gotchas* above) and `enabled: true`.

> **From Claude Code:** the MCP wrappers project the validate response down.
> `rossum_validate_content` surfaces the messages as `raw_messages`, so `detail.rule_id` — the
> signal you need in practice — **is** readable. `rossum_refire_annotation` reports only
> `updated_datapoints_count`. Neither passes `matched_trigger_rules` through today, so that one
> field is **not reachable from Claude Code**: use `raw_messages` plus the rule execution logs,
> and report the gap rather than open-coding a request around the missing projection.

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
