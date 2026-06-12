# Rossum Approval Workflows — Full Reference

Approval workflows route documents through an ordered chain of decision steps before export. The model is **workflow → ordered `workflow_steps` → `workflow_runs`** (one run per annotation as it moves through the chain).

> **Approval workflows are a paid feature configured by Rossum**, not self-service in the app or via the public API. The public API exposes these objects **read-only** (plus a single `reset` action on a run). Turning the feature on for an org makes the endpoints return data; the workflow itself is defined with Rossum's team.

**Sourcing.** Endpoints, methods, and field shapes below are confirmed against the official OpenAPI docs ([workflow](https://rossum.app/api/docs/openapi/api/workflow/), [workflow-step](https://rossum.app/api/docs/openapi/api/workflow-step/), [workflow-run](https://rossum.app/api/docs/openapi/api/workflow-run/)) and a live read-only probe (org 313278, June 2026), including an `OPTIONS` request to read each collection's allowed methods. Items tagged *observed* come from a populated live instance and can vary by configuration.

## Contents
- [Endpoints & methods](#endpoints--methods)
- [Workflow object](#workflow-object)
- [`workflow_step` object](#workflow_step-object)
- [`workflow_run` object](#workflow_run-object)
- [Conditions](#conditions)
- [Changing a workflow](#changing-a-workflow)

## Endpoints & methods

The public API is **read-only** for workflows and steps. A live `OPTIONS` on both collections returns `Allow: GET, HEAD, OPTIONS` — there are no `POST`/`PATCH`/`DELETE` endpoints. The only write action is resetting a run.

| Method | Endpoint | Notes |
|--------|----------|-------|
| GET    | `/v1/workflows` | List workflows. Filters: `id`, `queue`. Ordering: `id`. |
| GET    | `/v1/workflows/{id}` | Retrieve a workflow. |
| GET    | `/v1/workflow_steps` | List steps. Filter: `workflow`. |
| GET    | `/v1/workflow_steps/{id}` | Retrieve a single step (full body). |
| GET    | `/v1/workflow_runs` | List runs. Filters (live-confirmed): `annotation__queue`, `workflow_status`. Each run carries `current_step` and `workflow_status`. |
| GET    | `/v1/workflow_runs/{id}` | Retrieve a run. |
| POST   | `/v1/workflow_runs/{id}/reset` | **The only write action.** Resets the annotation to `to_review` and the run to status `in_review`. Optional body `{"note_content": "..."}`. |
| GET    | `/v1/workflow_activities` | List workflow activity (endpoint exists; returns 200). |

`/v1/workflow_step_users` **does not exist** (live probe: HTTP 404). Approver/assignee assignment is **not exposed** in the public API — the `workflow_step` object carries no user field; assignment is part of the Rossum-side configuration.

## Workflow object

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer (read-only) | |
| `url` | URL (read-only) | |
| `name` | string | Workflow name. |
| `organization` | URL | |
| `condition` | object | Condition that designates whether the workflow is entered. |

*Observed:* a workflow is linked to one or more queues (the `?queue=` filter resolves a queue to its workflow) and exposes its ordered steps; list steps explicitly with `GET /v1/workflow_steps?workflow={id}`.

## `workflow_step` object

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer (read-only) | |
| `url` | URL (read-only) | |
| `name` | string | UI label of the step (e.g. `"District Director"`). It is `name`, **not** `label`. |
| `organization` | URL | |
| `workflow` | URL | Parent workflow. |
| `type` | enum | Only `approval` is currently supported. |
| `mode` | enum | `any` — one assignee's approval is enough; `all` — every assignee must approve; `auto` — automatically approved if the `condition` matches (no assignee needed). |
| `condition` | object | Designates whether the step is entered; when it evaluates falsy the step is skipped. |
| `ordering` | integer | Evaluation order within the workflow; **must be unique per workflow**; lower runs first. |

Fields previously documented in error and **not** present on the step: `label`, `automatic`. There is no `workflow_step_users` resource.

## `workflow_run` object

One run tracks a single annotation through the workflow.

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer (read-only) | |
| `url` | URL (read-only) | |
| `organization` | URL | |
| `annotation` | URL (read-only) | The annotation this run tracks. |
| `current_step` | URL (read-only) | The step the annotation is currently at. |
| `workflow_status` | enum (read-only) | `pending` (in progress), `approved`, or `rejected`. |

## Conditions

Both `workflow.condition` and `workflow_step.condition` are Rossum expression objects evaluated against the annotation. *Observed* shapes from a live instance:

- **Plain map / equality form:** `{"field.approver_threshold_level_1": ""}` — equality match against a schema field.
- **MongoDB-style `$expr`:** `{"$expr": {"$gte": ["$field.item_max_price", 0]}}` — supports `$gte`/`$lte`/`$gt`/`$lt`/`$eq`, etc.

When a step's condition evaluates falsy, the step is skipped. A `mode: auto` step whose condition matches is approved automatically.

## Changing a workflow

Approval workflows are **configured by Rossum** (paid feature). The public API is read-only (confirmed live: `OPTIONS` returns only `GET, HEAD, OPTIONS`), so you **cannot create or edit** a workflow or its steps through `/v1/workflows` or `/v1/workflow_steps`. To change routing, approvers, conditions, or step order, coordinate with your Rossum contact.

`prd2 pull` captures the workflow and its steps as JSON for inspection and version control. Treat workflow definitions as Rossum-managed: because the public API exposes no write methods for these objects, do not assume a local edit can be pushed back — confirm the change path with Rossum first.

To re-run an annotation through the workflow (for example after a correction), use `POST /v1/workflow_runs/{id}/reset` — the one write action the API exposes.
