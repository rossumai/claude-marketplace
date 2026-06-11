# Rossum Approval Workflows — Full Reference

Approval workflows route documents through an ordered chain of decision steps before export. Each step has its own approvers, condition, type, and mode. The model is **workflow → ordered `workflow_steps` → `workflow_runs`** (one run per annotation as it moves through the chain).

> **Verification note:** the read endpoints below were confirmed against a live Rossum instance (organization 113187, workflow 126). The write endpoints (POST/PATCH/DELETE) and `workflow_step_users` are *unverified* — they have NOT been observed working in real probes, and `/v1/workflow_step_users` returned **404** in one probe. **Confirm against the official API docs at <https://rossum.app/api/docs/openapi/guides/getting-started/#introduction> or with `prd2 pull` before writing code against them.**

## Contents
- [Verified (read-only) endpoints](#verified-read-only-endpoints)
- [Unverified endpoints — confirm before use](#unverified-endpoints--confirm-before-use)
- [`workflow_step` real fields](#workflow_step-real-fields)
- [Changing a workflow safely](#changing-a-workflow-safely)

## Verified (read-only) endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET    | `/v1/workflows/{id}` | Retrieve workflow (includes ordered `steps` URLs and linked queues) |
| GET    | `/v1/workflow_steps?workflow={id}` | List steps for a workflow |
| GET    | `/v1/workflow_steps/{id}` | Retrieve a single step (full body) |
| GET    | `/v1/workflow_runs?annotation__queue={qid}` | List workflow runs (per annotation) — includes `current_step`, `workflow_status` (`approved` / `rejected` / in progress) |

## Unverified endpoints — confirm before use

| Method | Endpoint | Status |
|--------|----------|--------|
| POST / PATCH / DELETE | `/v1/workflows` and `/v1/workflows/{id}` | not confirmed against a live instance |
| POST / PATCH / DELETE | `/v1/workflow_steps` and `/v1/workflow_steps/{id}` | not confirmed; prefer `prd2 pull` + edit + `prd2 push` until verified |
| any | `/v1/workflow_step_users` | **returned 404** in one probe — likely does not exist; approver assignments may live on the step object itself or via a different resource |

## `workflow_step` real fields

From a verified GET response:

- `id` — integer
- `url` — self URL
- `organization` — organization URL
- `workflow` — parent workflow URL
- **`name`** — UI label of the step (e.g. `"District Director"`). NOT `label`.
- `ordering` — integer; lower runs first
- `type` — observed value: `"approval"`
- `mode` — observed values: `"all"` (every assigned approver must approve), `"any"` (likely — confirm)
- `condition` — Rossum expression object evaluated against the annotation. Two observed shapes:
  - Plain map form: `{"field.approver_threshold_level_1": ""}` — equality match against a schema field
  - MongoDB-style: `{"$expr": {"$gte": ["$field.item_max_price", 0]}}` — supports `$gte`/`$lte`/`$gt`/`$lt`/`$eq` etc.
  When the condition evaluates falsy the step is skipped.

Fields *not* observed in any GET response and previously documented in error: `label`, `automatic`. Do not rely on these without verifying against the OpenAPI spec.

## Changing a workflow safely

Until POST/PATCH on workflow_steps is verified end-to-end, the safe path is:

1. `prd2 pull` the source environment to get the workflow + steps as JSON.
2. Edit locally (rename, reorder, change condition).
3. `prd2 push --indexed-only -f` after explicit user approval.

Direct API writes are possible but unverified — probe with a throwaway workflow on a non-production environment first.
