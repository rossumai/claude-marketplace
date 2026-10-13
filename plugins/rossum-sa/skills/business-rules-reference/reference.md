# Rossum Business Rules & Validation — Full Reference

Rossum validates extracted data and blocks automation in two ways, both covered here: the modern **native Rossum Rules** (`POST /v1/rules`) and the **legacy Business Rules Validation Store extension**. Both run at validation time to surface messages and block confirmation/automation of invalid documents. Native Rules can also act on lifecycle events — import, confirm, export and their success/failure outcomes — to send email, label, move, delete or exclude from training; see [Rule events](#rule-events--when-actions-run).

## Contents
- [Overview: two ways to validate](#overview-two-ways-to-validate)
- [Native Rossum Rules](#native-rossum-rules)
- [Legacy Business Rules Validation extension](#legacy-business-rules-validation-extension)
- [Choosing between them](#choosing-between-them)
- [Porting expressions between engines](#porting-expressions-between-engines)

## Overview: two ways to validate

| | Native Rossum Rules | Business Rules Validation extension |
|---|---|---|
| What it is | Platform-native entity (`/v1/rules`) | A Store hook you install and configure |
| Config | `trigger_condition` + `actions[]` | a `checks[]` array |
| Expression dialect | TxScript `field.X` (see `txscript-reference`) | bespoke `{field}`-brace engine (documented below) |
| Status | Modern, recommended for new work | Legacy; still present in older implementations |

Both surfaces evaluate at validation time, emit messages (error/warning/info), and can block automation. They are independent — do not mix the two expression dialects. (Positioning/deprecation reflects current practice; confirm specifics against the official Rossum docs.)

## Native Rossum Rules

A native Rule (`POST /v1/rules`) evaluates a single boolean `trigger_condition`; when it is `True`, the rule emits one or more `actions` (messages, automation blockers, show/hide toggles). Each action names the `event` it runs on — `validation` for the validation-time workhorses, or a lifecycle event (import, confirm, export, and their outcomes) for side-effect actions such as `send_email`, `change_queue` or `add_label`; see [Rule events](#rule-events--when-actions-run). The `trigger_condition` is a TxScript expression using Python-style `field.X` access — for the expression language, helpers, and the defensive `not is_empty(field.X)` guard convention, see the `txscript-reference` skill. This is a different surface from the Business Rules Validation extension's `{field}`-brace engine below; do not mix syntaxes.

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
- **Attaching a rule to a queue — and how it can end up unattached.** The rule↔queue link is a many-to-many relation mirrored on both sides (`rule.queues` ↔ `queue.rules`). **The API field is `queues` — a list of queue URLs.** A create that sends it attaches the rule: verified live, the rule comes back with `queues` populated and immediately appears in `GET /v1/rules?queue=<id>`.

  **`queue_ids` is a wrapper parameter, not an API field.** The MCP tools `rossum_create_rule` / `rossum_patch_rule` take `queue_ids` and expand them into `queues` URLs before sending. A **raw** `POST /v1/rules` carrying a literal `queue_ids` key was observed being accepted — the unknown key ignored — and creating the rule with `queues: []`: attached to nothing, evaluating on no document. The create succeeds, so nothing in the response says the link does not exist.

  A rule also lands unattached when (a) you create it without passing queues at all, or (b) a **prd2 `_[]`-placeholder push** creates it — prd2 does not send the rule-side `queues`, because in a prd2 tree the link is declared on the queue side, so you must add the new rule's URL to `queue.json`'s `rules` array and push the queue.

  **After any create, read `queues` back** from `GET /v1/rules/{id}`. If empty, set it via `PATCH /v1/rules/{id}` (`queues`) / MCP `rossum_patch_rule` (`queue_ids`), or the prd2 `queue.json` route above.

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
The full type list, and which `event` each type accepts, is in
[Rule events](#rule-events--when-actions-run).

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

### Rule events — when actions run

Every action carries an `event`. The `trigger_condition` is shared by the whole rule; the `event`
decides **when** each action may run. One rule can mix events (e.g. an `add_label` on
`annotation_export_succeeded` next to a `send_email` on the same event).

| Event | When it fires | Notes |
|---|---|---|
| `validation` | Every validation of the content (import, user edits, confirm) | The only event for UI-facing actions (`show_message`, blockers, show/hide) |
| `annotation_imported` | Inside the import pipeline, right after extraction | Before the import outcome is known. Also shared with legacy Trigger objects |
| `annotation_confirmed` | On confirm, by a user or by automation | Shared with Trigger objects |
| `annotation_exported` | Inside the export pipeline | Runs **before** the export result is known. `validation` actions also execute here, so a rule `error` message at this step **fails the export** — see below — its actions run **even when the export then fails** (verified live). The Trigger docs limit this event to non-automated annotations with an email; for Rules it also fired on an automated, uploaded document (verified live) |
| `annotation_import_succeeded` | After the import outcome is saved: the annotation reached `to_review`, was automated, or a rule changed its status | Rules only. Sees the final status, so `context.annotation.automated` is meaningful here |
| `annotation_import_failed` | The annotation ended in `failed_import` — extraction or extension error, upload crash, or an upload rejected by an `upload.created` hook | Rules only. Before these events, nothing ran on a failed import. Content is usually empty or absent — see below |
| `annotation_export_succeeded` | The annotation reached `exported` | Rules only. Use for "notify only when export really worked" |
| `annotation_export_failed` | The annotation reached `failed_export` (e.g. an export extension returned an error message) | Rules only. Use for alerting |

The four `*_succeeded` / `*_failed` events are the **outcome events**. They are rule-only: Trigger
objects (`/v1/triggers`) do not accept them.

**Observed order on an automated document** (one upload into a queue with `automation_level:
always`, read from `rules_execution_logs` timestamps):
`validation → annotation_imported → annotation_confirmed → annotation_import_succeeded →
annotation_exported → annotation_export_succeeded`. Note that `annotation_import_succeeded` comes
**after** `annotation_confirmed` when the document is automated.

#### Which action accepts which event

Verified live by creating one rule per action × event (`201` = accepted, `400` = rejected with
`"<event>" is not a valid choice`). This matches the per-action prose of the API docs' Rule page.
The OpenAPI component schemas for `add_label` / `remove_label` still say "`validation` event
only" — that text is stale; trust this table.

| Action type | `validation` | `annotation_imported` | `annotation_import_succeeded` | `annotation_import_failed` | `annotation_confirmed` | `annotation_exported` | `annotation_export_succeeded` | `annotation_export_failed` |
|---|---|---|---|---|---|---|---|---|
| `show_message`, `add_automation_blocker`, `show_hide_field`, `show_field`, `hide_field`, `add_validation_source` | ✅ | — | — | — | — | — | — | — |
| `add_label`, `remove_label`, `add_remove_label` | ✅ | — | ✅ | ✅ | — | — | ✅ | ✅ |
| `send_email` | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `change_queue` | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `exclude_from_training` | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `change_status` | — | ✅ (any method) | — | ✅ (`delete` only) | — | — | — | — |

(`show_field`, `hide_field` and `add_validation_source` are listed from the docs, not probed.)
`change_status` with another method on `annotation_import_failed` is rejected with
`"confirm" is not allowed on the annotation_import_failed event.`

#### Payload shapes for the side-effect actions

- **`change_queue`** — `{"queue": "<queue URL>", "reimport": false}`. The key is **`queue`, a
  URL**. The docs' prose table says `queue_id` (integer); the API rejects that with
  `payload.queue: This field is required.` At most one per rule.
- **`change_status`** — `{"method": "postpone" | "export" | "delete" | "confirm" | "reject"}`.
  At most one per rule.
- **`send_email`** — either `{"email_template": "<email_template URL>", "attach_document": bool}`
  or an inline email `{"to": [...], "subject": "...", "body": "...", "cc": [...], "bcc": [...]}`.
  The API stores the inline form with `email_template: null` and `attach_document: false` added.
  In line-item mode, identical emails are de-duplicated and one action sends at most five emails.
- **`add_label` / `remove_label` / `add_remove_label`** — `{"labels": ["<label URL>", ...]}`.
  `add_remove_label` adds the labels when the condition is `True` and removes them otherwise.
- **`exclude_from_training`** — no payload (the API stores `payload: null`). Sets the
  annotation's `training_enabled` to `false`.

#### What the outcome events change in practice

- **`change_queue` keeps the status on outcome events.** Without `reimport`, a move on
  `annotation_imported`, `annotation_confirmed` or `annotation_exported` puts the annotation into
  `to_review`. On the four outcome events the annotation **keeps its current status**. Verified
  live: a `failed_import` document moved to a quarantine queue stayed `failed_import`, and an
  `exported` document moved to a follow-up queue stayed `exported`.
- **`change_status: delete` on `annotation_import_failed`** cleans spam and unsupported files out
  of the queue automatically. Verified live: a corrupt PDF ended in `deleted`. Deleted documents
  stay recoverable until they are purged (manually or by the queue's `document_lifetime`).
- **Branching on automation.** On `annotation_import_succeeded`, `context.annotation.automated`
  is `True` for an automated document and `False` for one that went to `to_review`. Verified live
  with two rules (`context.annotation.automated` / `not context.annotation.automated`), each
  adding a different label.
- **Email only on real success.** Put `send_email` on `annotation_export_succeeded` (or
  `annotation_export_failed` for alerts), not on `annotation_exported`.

#### Conditions on `annotation_import_failed`

A failed import usually has empty document content, and sometimes none at all. Prefer
`context.*` references or a constant condition:

```python
context.annotation.metadata.get('source') == 'sftp'    # metadata is a dict; .get works (verified)
True                                                     # act on every failed import
```

What happened live with `field.*` on a failed import (corrupt PDF, no content extracted):

- A field that **exists in the schema** read as an empty string: `is_empty(field.order_id)` was
  `True` and the rule fired.
- A field that **does not exist in the schema** raised
  `AttributeError: Field '<id>' is not defined`. The rule was skipped, and the error was written
  to the annotation's `messages`.

The docs also name `context.email` as usable here; that one was not probed.

#### A failing rule does not break the import or export

If a rule fails on an outcome event, the import or export result stands. The error goes to the
annotation's `messages` instead. Verified live: `send_email` on `annotation_export_succeeded`
failed (the org had an email limit of 0 per day); the annotation still ended `exported`, and the
**other actions in the same rule still ran** (its `add_label` succeeded).

#### …but a rule's `error` message CAN fail an export

`validation`-event actions do not run only at validation. Rules are evaluated again on
`annotation_confirmed` and `annotation_exported`, and a `show_message` / `add_automation_blocker`
configured for `validation` **executes there too** — the execution log records it under that
trigger event. If such a `show_message` has `type: "error"`, the annotation gets an error message
inside the export pipeline, and the export ends `failed_export` — exactly as if an export hook had
returned an error. The export hooks themselves all report `completed`.

Seen on a UAT deployment: every export hook completed (warnings only); one second later, on
`annotation_exported`, a rule flagging PO-backed invoices without line items emitted its
`error` message; the next second the annotation was `failed_export` and the
`annotation_export_failed` rules ran. Reproduced on a clean queue with no hooks: a rule whose
condition was `context.annotation.status == 'exporting'` (False at validation, True from confirm on)
let the confirm through and then failed the export with its error message.

How to diagnose a `failed_export` with no failing hook:

1. `GET /v1/annotations/{id}` → `messages`: look for `type: "error"` entries with
   `detail.hook_name: "rules"` and the `rule_id`.
2. `GET /v1/rules_execution_logs?annotation=<id>&trigger_event=annotation_exported` (and
   `annotation_confirmed`): find the rule whose `actions` show a `show_message` of type `error`.
3. Fix the rule's condition, or downgrade the message to `warning` if it should not stop export.

Normally such an error also shows at validation and blocks confirm: an API confirm returns
`HTTP 400 {"detail": "Error during actions evaluation"}`. The export-time failure happens when the
condition only becomes `True` at confirm/export time, or when the document got confirmed despite
the message.

`context.annotation.status` is readable in a condition. During the confirm-time validation it was
`reviewing`; on `annotation_confirmed` and `annotation_exported` (queue without the confirmed state)
it was already `exporting`.

### Debugging rules on lifecycle events

There is no validate response to read for the non-`validation` events. Use these instead:

- **`GET /v1/rules_execution_logs?annotation=<id>`** has one row per rule **per event** — every
  rule attached to the queue is evaluated and logged on every event, even when it has no action
  for that event. Read `actions`:
  `null` = condition `False`; `[null]` = condition `True` but no action on this event;
  `[[{action_type, success, payload, error?}, ...]]` = actions ran. A failed action sets
  `execution_result: "failure"` and puts the reason in `execution_error`
  (e.g. `send_email: Sending email by user None failed: exceeded the limit of 0 emails per day.`).
  `send_email` reports `success: true` once the email is handed off — verified on a second org
  for import-failed, export-succeeded and export-failed emails.
- **A condition that raises is not visible in the log.** The row says `trigger_match: false`,
  `execution_result: "success"`, `execution_error: null`. The real error is only in the
  annotation's `messages` (`detail.rule_id`, `detail.is_exception: true`). Check both.
- **Annotation `messages` do carry rule output for lifecycle events** — condition errors, and a
  generic `"Rules could not be executed for the annotation_export_succeeded event."` (with
  `detail: null`) when an action fails. This differs from `show_message` at `validation`, which
  never lands there (see the next section). The specific reason is in the execution log.
- **The log's `queue_id` is the queue after any `change_queue`.** A log row for a document that
  a rule moved shows the target queue. Filter by `annotation`, not by `queue`, when tracing moves.

### Verifying a rule actually fired

**Do not look for the message on the annotation.** `GET /v1/annotations/{id}` reports
`messages: []` regardless — a fired `show_message` never lands there. (This is about `show_message` at
`validation`. Rule *errors* on lifecycle events do land in `messages` — see
[Debugging rules on lifecycle events](#debugging-rules-on-lifecycle-events).) Reading the annotation is
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
   `True` on this run. It is the most *complete* fired/not-fired signal in principle, because it
   is independent of whether the rule has any *visible* action: a rule whose only action is
   `add_automation_blocker`, or one whose `show_message` is `info`-level, still belongs here.
   **One caveat, from a single observation:** it has been seen coming back empty on a run whose
   `messages[]` did carry entries. The cause was not established, and there is an innocent
   explanation to rule out first — `messages[]` also carries hook-emitted messages, so check
   `detail.hook_name` (`"rules"` for a native Rule) before concluding the array disagrees with
   itself. Until the cause is known: a populated `matched_trigger_rules` is proof the rule fired;
   an empty one with a rule-attributed message in `messages[]` is a discrepancy to investigate, not
   evidence the trigger failed to match.
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

**A message anchored on a hidden field never surfaces — not in the API response, not to the user.**
If a rule's `show_message` payload names a `schema_id` that is hidden at validation time, the
trigger can be firing perfectly and the message still be absent from `messages[]`. Visibility is
**dynamic**: show/hide extensions and `show_hide_field` rule actions change it per document, so the
schema's `hidden` flag is not the whole answer.

The diagnostic that separates "not firing" from "firing but not surfacing", without needing to
establish effective visibility at all: re-anchor the same `show_message` on a field you know is
visible on that document and validate again. A message that appears proves the `trigger_condition`
is fine and sends you to the anchor; one that still does not appear sends you to the condition.
Worth reaching for early — the tag-fire + reveal pairing above anchors messages on fields that are
hidden by default, which is exactly the population this bites.

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

Read both signals together in the OK row. Absence from `matched_trigger_rules` alone is the weaker
half of the evidence (see the caveat above); "no message carrying that `rule_id`" is what makes the
OK case convincing.

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
  annotation, so it survives the validation call that created it. **The blocker is not in the
  `content/validate` response body at all** — the route to it is `GET /v1/annotations/{id}` →
  follow its `automation_blocker` URL, and read the items under **`content`** (not `items`). Some
  blocker kinds, including hidden-field errors, appear there and nowhere else.
- A rule that appears in neither `matched_trigger_rules` nor the logs — of which the logs are the
  load-bearing half — may simply not be attached to the queue: check `GET /v1/rules/{id}` for a
  populated `queues` array (see *Field constraints & create/link gotchas* above) and
  `enabled: true`.

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

### Reading a legacy `checks[]` config

Five behaviours of the legacy engine that are invisible in the config text and change how a check
must be ported. All measured on live implementations; each is stated at the scope it was observed
at, not generalised past it.

**A check can write its message to more than one field.** A native Rule's `show_message` carries a
single `schema_id`, so a one-to-one port silently drops the other anchors, and a faithful port needs
**one action per field the original actually wrote to**. That set is not simply "every field the
expression references" — one observed check referenced two fields and wrote to only the non-empty
one. Determine it empirically, on a document where the check fires: read the emitted messages in full
(`rossum_validate_content` passes them through unprojected as `raw_messages`) and record which field
each one is anchored on.

**`"active": "false"` written as a string does disable the check.** Observed on a live config and
verified the direct way — removing the key from a dormant check made it start firing. The boolean
`false` is the documented form; treat the string form as something to recognise when reading a
config, not as a second supported spelling to emit. The trap is reading a string `"false"` as a
truthy Python value and concluding the check is live.

**`{x} == {x}` — a field compared to itself — is the engine's not-empty idiom, not a typo.** The
engine skips empty values, so a self-comparison is false exactly when the field is empty. Port it as
`not is_empty(field.x)`. Do not "fix" it, and do not port it as a tautology.

**A `regexp()` check does not fire on an empty value**, even when the pattern would reject an empty
string. Port such a check as `not is_empty(field.x) and <the shape test>`, never as a literal
transcription of the regex — the literal version fires on every document with an empty field.

**A hook-level `active: false` makes every check inside it dormant**, regardless of per-check flags.
Migrating a check out of a dormant hook as an *enabled* native Rule **adds** behaviour the customer
did not have. That is a regression, not a migration: check the hook's own `active` flag before
porting anything out of it, and confirm with the customer if the intent is to revive the check.

---

## Choosing between them

Prefer **native Rules** for new work — they're platform-native, versioned with the org config, and don't require installing a Store extension. The **Business Rules Validation extension** persists in older implementations; document and migrate it to native Rules when practical. (Confirm the current deprecation status against the official Rossum docs before asserting it to a customer.)

---

## Porting expressions between engines

Moving an expression from one Rossum engine to another — a BRV `checks[]` entry to a native Rule
`trigger_condition`, a legacy calculation hook to a schema formula, a hook write to a formula — is
common modernisation work — the `upgrade` skill drives the deprecated-extension half of it and
records the result as a `hook_to_rule` / `hook_to_formula` axis in its manifest. This section covers
the part that goes wrong.

**Evidence base.** Two independent engagements — different customers, different engineers, one
porting BRV checks to native Rules and the other legacy calculation hooks to schema formulas —
produced six defects between them. **Every one was a value-semantics mismatch; none was
structural.** In both projects, gating shape, per-row vs. header context, action fan-out and
control flow ported correctly from reading the source config alone; what broke was what the
*values* did once the new engine evaluated them.

Six defects across two engagements is not a platform law, and the taxonomy below is certainly not
exhaustive. It is enough to say where verification effort pays off: on values, not on structure.

### Never port from the expression text alone

The written condition tells you what the author *intended*. Only the data tells you what the engine
*did*. Before porting a check, read the real values of every field it names, on a document where the
source check actually fires. Every defect below survived a careful reading of the source
expression and was caught only by looking at the data.

### The defect taxonomy

Every row was observed in the field. The six defects group into these classes, with the
type-coercion row covering two separate incidents. Field names are generic stand-ins and the
arithmetic is illustrative.

| Class | What happened | What to check before porting |
|---|---|---|
| **Type coercion** | `int(field.x) != field.y` where `y` holds a *string* — the comparison is always true, so the gate fired on every row. Separately, `field.item_tax_rate == 1` against a number-typed enum whose value reads back as the string `"1.00"` — always false, so that gate never fired. | Read the value back off the annotation and check its actual Python type, rather than trusting the schema's declared type. A number-typed field commonly arrives as a formatted string. |
| **Float precision** | An exact `!=` on money. A quantity times a unit price lands on `7.3500000000000005` in binary float against a stored line total of `7.35`, so the rule fired on a correct line. The legacy engine had compared tolerantly. | Port an exact money comparison as a tolerance test — `abs(a - b) >= 0.01`, at the currency's precision. Keep an exact `==`/`!=` only where you have confirmed the source engine was exact too. |
| **Empty sentinel** | A field carried a sentinel string (`'---'`) as a deliberate "no value" marker. The two engines disagreed about whether that counted as empty: the legacy check fired on those documents, and the port's `is_empty()` — which sees a non-empty string — did not. (The legacy engine's exact handling of that sentinel was never established; the disagreement was.) | Enumerate what "empty" means for that specific field in that corpus, from data. Empty string, sentinel string, absent and zero are four distinct states and the engines do not agree about all four. |
| **Empty vs. zero** | `default_to(field.item_quantity, 0)` turned an intentionally-empty quantity into a real `0`, which changed a downstream total. | `default_to(x, 0)` is a value decision, not a safety wrapper. Guard the raw field with `is_empty(field.x)` where empty and zero must stay distinct — see `txscript-reference`. |
| **Raw vs. derived input** | The gate read the raw captured field (empty on these documents) while the arithmetic it guarded read the calculated field of almost the same name (populated). The branch never ran. | Where two fields share a stem (`item_total` and `item_total_base`, `x` and `x_calculated`), confirm on data *which one* the source condition read. Name similarity is not evidence. |
| **Condition that does not gate** | A legacy check's `condition` provably had no effect: the check fired on every row regardless of the gated field's value. The faithful port had to **drop the clause** — the author's intent had never worked in production. | Where a condition looks like it should filter but the source fires everywhere, the observed legacy behaviour is the specification. Port what runs, and raise the dead intent with the customer as a separate decision. |

Two of these — the sentinel and the dead condition — mean a *faithful* port is not always a
*literal* one. Reproducing the source's observed behaviour occasionally requires writing something
the source text does not say, and saying so explicitly in the migration trace
(`upgrade` → *Intentional behavior changes*).

### The verification method

Per check family, against real documents, with the count predicted in advance.

**Where to run it: a sandbox/UAT org with a realistic corpus.** The dual run shows two identical
banners per affected document for its duration. The sharper cost is a blocker the source did not
have: where the ported rule carries `add_automation_blocker` but the legacy check only warned, the
dual run stops those documents automating. (Where the legacy check already blocked, a second blocker
changes nothing.) So give the ported rule a `show_message` action **only** while the source is still
live, and add the blocker once the source is retired.

Running the dual run against production is a customer decision, not a default and not yours to take
— the new rule evaluates for real reviewers the moment it is attached. If the only corpus that fires
the check is in production, say so and get explicit agreement on a window before anything is enabled
there; in a prd2 tree the rules do not exist until the user pushes them anyway.

1. **Find a document where the source check actually fires.** Sample the corpus until you have an
   anchor. Both engines are observable per annotation: run the three calls yourself —
   `rossum_start_annotation` → `rossum_validate_content` → `rossum_cancel_annotation` — and read
   `messages[]`, where `detail.hook_name` attributes each message to the legacy hook or to
   `"rules"`. **Not `rossum_refire_annotation`**: its validate branch keeps only
   `updated_datapoints_count` and discards the response messages, which are the entire measurement
   here. After the fact, `GET /v1/rules_execution_logs?annotation=<id>` records native-rule
   evaluations. If nothing in the corpus fires the check, say so: the port is **unverifiable**, not
   verified. Do not mark it done.
2. **Read the real values** of every field the condition names, on that document — including the
   empty case, explicitly. Empty string, sentinel, absent and zero are four different things and
   the engines disagree about them (see the taxonomy above).
3. **Run source and port simultaneously.** Leave the legacy check enabled, enable the new rule, and
   re-validate the corpus. **Count by `detail.rule_id` / `detail.hook_name`, not by message text** —
   a faithful port usually reproduces the source's wording exactly, so text alone cannot tell the
   two apart. The count should **double on exactly the anchors you predicted** — same documents,
   same fields. Where the port deliberately diverges from the source (a dropped dead clause, a
   sentinel handled differently), predict *that* count instead: the expected number is whatever
   your reading of the data says, not automatically 2×.
4. **Write the expected count down before you read the actual one.** A delta in the right direction
   is not evidence. In one observed run a delta of `+3` was read as success where the correct
   answer was `+63`, and the regression shipped. The prediction is what makes the count
   falsifiable.
5. **Then retire the source** — `active: false` on the legacy check (or on its hook) — and confirm
   the output matches the baseline captured *before* any change, not the doubled state.
6. **Roll back the whole batch on any mismatch.** `enabled: false` on the batch's rules is the
   fastest lever and keeps them available for a second attempt; deleting them loses the work. Do
   not fix forward against a live discrepancy — a second change stacked on an unexplained one makes
   the next count unreadable.

### Batching discipline

- **Port in small batches grouped by check family** (same source fields, same comparison shape). A
  batch whose members share inputs fails in one recognisable way; a mixed batch produces a count
  delta nobody can attribute.
- **Capture the before-baseline first**, across the whole corpus you intend to re-measure, before
  the first new rule exists — step 5 has nothing to compare against otherwise. Budget it: a
  baseline is one soft re-fire per annotation, which fires the full hook chain each time and bumps
  the annotation's timestamps. Size the corpus accordingly rather than reaching for "all documents"
  by reflex.
- **Never create the next batch's rules while a verification run is in flight.** A native Rule
  starts evaluating the moment it is attached and enabled, so rules created mid-run contaminate the
  count of the run in progress. In a prd2 tree, rule creation happens on the *user's* push — so
  this is a handshake, not a self-imposed rule: say explicitly when a run is in flight and when it
  is safe to push the next batch.

For a corpus-wide before/after replay across two environments, with scripted snapshot capture and
diffing, use the `test-behavioral-equivalence` skill. The two are complementary: this count check
verifies one check family in one environment and needs no second org; the replay verifies a whole
implementation and does.

### Mechanics that bite during a port

- **Read messages from the validate response, not the annotation** — see
  [Verifying a rule actually fired](#verifying-a-rule-actually-fired). Counting messages is the
  measurement this whole method rests on, so get the probe right before trusting any count.
- **A message anchored on a hidden field never surfaces**, so a correctly-ported rule reads as not
  firing. Same section, which also gives the diagnostic that separates the two cases.
- **A rule may see a formula's pre-recompute value** when both evaluate in the same validate pass.
  When a ported rule gates on a formula whose inputs just changed, validate twice and read the
  second response — see `txscript-reference` → *A rule can observe a formula's pre-recompute value*.
- **Verify any count you derive from a content walk against a table of known size** before drawing
  a conclusion from it; see `rossum-reference` → *Annotation Content*. A miscount here reads as a
  behavioural difference and has caused a correct migration to be abandoned.
- **A formula-backed field cannot be written over the API**, so the manual-override path of a
  migrated field is not testable headlessly (`txscript-reference` → *Formula constraints*). Report
  that path as untested rather than verified.
