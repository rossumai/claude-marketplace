# Rossum Approval Workflows — Full Reference

Approval workflows route documents through an ordered chain of decision steps before export. The model is **workflow → ordered `workflow_steps` → `workflow_runs`** (one run per annotation as it moves through the chain), with every transition logged as a **`workflow_activity`**.

> **Approval workflows are a paid feature configured by Rossum**, not self-service in the app or via the public API. The public API exposes these objects **read-only** — the only write action is `POST /v1/workflow_runs/{id}/reset`. Turning the feature on for an org makes the endpoints return data; the workflow, its steps, and its approver assignment rules are defined with Rossum's team.

**Sourcing.** Confirmed against the official OpenAPI docs ([workflow](https://rossum.app/api/docs/openapi/api/workflow/), [workflow-step](https://rossum.app/api/docs/openapi/api/workflow-step/), [workflow-run](https://rossum.app/api/docs/openapi/api/workflow-run/)) **and** a live probe (org 313278, workflow 212, June 2026) — including an `OPTIONS` request per collection and a document pushed through a real run to capture run/activity shapes, assignees, and `reset`. Where docs and live behaviour differed, live wins and is noted.

## Contents
- [Endpoints & methods](#endpoints--methods)
- [Workflow object](#workflow-object)
- [`workflow_step` object](#workflow_step-object)
- [Approvers & assignment rules](#approvers--assignment-rules)
- [`workflow_run` object](#workflow_run-object)
- [`workflow_activity` object](#workflow_activity-object)
- [Conditions](#conditions)
- [Resetting a run](#resetting-a-run)
- [Changing a workflow](#changing-a-workflow)

## Endpoints & methods

The public API is **read-only** for workflows, steps, runs, and activities. A live `OPTIONS` on `/v1/workflows` and `/v1/workflow_steps` returns `Allow: GET, HEAD, OPTIONS` — no `POST`/`PATCH`/`DELETE`. The only write is the run `reset`.

| Method | Endpoint | Notes |
|--------|----------|-------|
| GET    | `/v1/workflows` | List workflows. Filters: `id`, `queue`. Ordering: `id`. |
| GET    | `/v1/workflows/{id}` | Retrieve a workflow. |
| GET    | `/v1/workflow_steps` | List steps. Filter: `workflow`. |
| GET    | `/v1/workflow_steps/{id}` | Retrieve a step (detail == list body). |
| GET    | `/v1/workflow_runs` | List runs. Filters (live-confirmed): `annotation`, `annotation__queue`, `workflow_status`. |
| GET    | `/v1/workflow_runs/{id}` | Retrieve a run. |
| POST   | `/v1/workflow_runs/{id}/reset` | **The only write action** — see [Resetting a run](#resetting-a-run). |
| GET    | `/v1/workflow_activities` | List activity log. Filter (live-confirmed): `workflow_run` (also `annotation`). |

`/v1/workflow_step_users` **does not exist** (live: HTTP 404). There is no `/v1/assignment_rules` endpoint either, and `/v1/rules/{django_pk}` for an assignment rule returns 404 (the public `/v1/rules` list contains only user-authored business rules). See [Approvers & assignment rules](#approvers--assignment-rules).

## Workflow object

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer (read-only) | |
| `url` | URL (read-only) | |
| `name` | string | Workflow name. |
| `organization` | URL | |
| `condition` | object | Whether the workflow is entered at all (see [Conditions](#conditions)). |
| `relevant_schema_ids` | string[] | Schema field IDs the workflow's conditions depend on (live: `[]` when unset). |

The workflow object does **not** embed a `steps[]` array or a `queues` list. List its steps with `GET /v1/workflow_steps?workflow={id}`. The **queue→workflow link lives on the queue**: a queue carries `workflows: [{"url": ".../workflows/{id}", "priority": <int>}]`.

## `workflow_step` object

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer (read-only) | |
| `url` | URL (read-only) | |
| `name` | string | UI label of the step (e.g. `"Level 1 review"`). It is `name`, **not** `label`. |
| `organization` | URL | |
| `workflow` | URL | Parent workflow. |
| `type` | enum | Only `approval` is currently supported. |
| `mode` | enum | `any` — one assignee's approval is enough; `all` — every assignee must approve; `auto` — auto-approved when the `condition` matches (no assignee needed). |
| `condition` | object | Whether the step is entered; when it evaluates falsy the step is skipped. |
| `ordering` | integer | Evaluation order; **unique per workflow**, lower runs first. **Not necessarily contiguous** (a real workflow had steps ordered `0, 1, 3`) and is **0-based**. |

The step body carries **no approver/user/assignee field** (confirmed live, including with `sideload`/`expand` — both ignored). Fields previously documented in error and not present: `label`, `automatic`.

## Approvers & assignment rules

Approvers are **not** set on the step object. Each step routes to approvers through an internal **Assignment rule** (a Rossum `Rule`-type object; visible in Django as e.g. *"Assignment rule: rule [778]"*). This assignment rule is **not exposed in the public API** — its Django PK is not a `/v1/rules` id, and there is no `/v1/assignment_rules` endpoint.

What *is* observable: when a step with a matching assignment rule is entered, the resulting **`workflow_activity` carries the resolved approvers in `assignees[]`** as a list of user URLs (live: `["https://…/v1/users/386147"]`). So to see who a step routed to, read the run's activities — not the step.

**No assignment rule = automatic rejection.** If a step is entered but no assignment rule matches (e.g. a `mode: all` step with no assignees), the workflow **auto-rejects** the run. Live, this produced a `rejected` activity with `note: "Automatically rejected as no assignment rule matched."` and set `workflow_status: rejected`.

## `workflow_run` object

One run tracks a single annotation through the workflow.

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer (read-only) | |
| `url` | URL (read-only) | |
| `organization` | URL | |
| `annotation` | URL (read-only) | The annotation this run tracks. |
| `current_step` | URL (read-only) | The step the annotation is currently at (`null` once the run completes/resets). |
| `workflow_status` | enum (read-only) | `pending` (in progress, awaiting approval), `approved`, `rejected`, and **`in_review`** (set by `reset`; not in the OpenAPI enum but returned live). |

The run object carries **no approver field** — approvers appear only on activities. An annotation in an active run has status **`in_workflow`**.

## `workflow_activity` object

The append-only audit trail of a run. Read-only; list with `GET /v1/workflow_activities?workflow_run={id}`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer (read-only) | |
| `url` | URL (read-only) | |
| `organization` | URL | |
| `annotation` | URL | |
| `workflow` | URL | |
| `workflow_run` | URL | |
| `workflow_step` | URL or `null` | `null` for workflow-level events (e.g. `workflow_started`). |
| `action` | string | Observed: `workflow_started`, `step_started`, `step_completed`, `rejected`, `pushed_back`, `workflow_completed`. |
| `assignees` | URL[] | Approver user URLs for the step (populated on `step_started` when an assignment rule matched; `[]` otherwise). |
| `created_by` | URL or `null` | The acting user; `null` for system-generated events. |
| `created_at` | datetime | |
| `note` | string | Free text, e.g. the auto-reject reason, or a `reset`'s `note_content`. |

A typical pending run logs `workflow_started` → `step_started` (with `assignees`). An auto-rejected run logs `workflow_started` → `step_started` → `rejected` → `step_completed`.

## Conditions

`workflow.condition` and `workflow_step.condition` are Rossum expression objects evaluated against the annotation. Two confirmed shapes:

- **Plain map / equality form:** `{"field.document_type": "20"}` — equality match against a schema field's value.
- **MongoDB-style `$expr`:** `{"$expr": {"$gte": ["$field.amount_total", 1000]}}` — supports `$gte`/`$lte`/`$gt`/`$lt`/`$eq`, etc.

An **always-true** condition (useful so a step always applies) is just a tautological `$expr`, e.g. `{"$expr": {"$gte": ["$field.amount_total", 0]}}`. When a step's condition evaluates falsy the step is skipped; a `mode: auto` step whose condition matches is approved automatically. Note the equality form matches the field's **stored value** — for an enum field that is the option value (e.g. `"tax_invoice"`), not a numeric code, so condition values must match the actual schema options.

## Resetting a run

`POST /v1/workflow_runs/{id}/reset` is the single write action. **Precondition:** the annotation must currently be `in_workflow` — calling it on a terminal run returns `400 {"non_field_errors": ["Annotation must be in 'in_workflow' state."]}`.

On success (live) it returns `200` with `{"annotation_status": "to_review", "workflow_status": "in_review"}`, sets the annotation back to `to_review`, sets the run to `in_review` with `current_step: null`, and appends `pushed_back` → `step_completed` → `workflow_completed` activities. Optional body: `{"note_content": "..."}` (recorded on the activity).

Use it to send a document back for re-review after a correction.

## Changing a workflow

Approval workflows are **configured by Rossum** (paid feature). The public API is read-only (live `OPTIONS` → `GET, HEAD, OPTIONS`), so you **cannot create or edit** a workflow, its steps, their conditions/modes/ordering, or the assignment rules through the API. To change any of that, coordinate with your Rossum contact.

`prd2 pull` captures the workflow + steps as read-only JSON for inspection and version control. Because the API exposes no write methods for these objects, do not assume a local edit can be pushed back — confirm the change path with Rossum first.

To re-run an annotation through an active workflow, use `reset` (above) — the one write the API exposes.
