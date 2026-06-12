---
name: approval-workflows-reference
description: Rossum approval-workflows reference — how documents route through an ordered chain of approval steps (workflow → ordered workflow_steps → workflow_runs, logged as workflow_activities). Covers the read-only public API (GET workflows / workflow_steps / workflow_runs / workflow_activities, plus the one write action POST /workflow_runs/{id}/reset), the real workflow_step fields (name, ordering, type, mode any/all/auto, condition), workflow_run statuses (pending/approved/rejected/in_review), how approvers are assigned via an internal assignment rule and surface as workflow_activity.assignees (not on the step), and the fact that workflows are a paid, Rossum-configured feature with no create/edit API. Use whenever the user asks about approval workflows, approval steps or levels, approvers/assignees, multi-level sign-off, conditional approval routing, workflow_steps / workflow_runs / workflow_activities, resetting a run, or "who approves this document" — even if they don't say the word "workflow".
user-invocable: false
---

# Rossum Approval Workflows Reference

Approval workflows route documents through an ordered chain of decision steps before export. The model is **workflow → ordered `workflow_steps` → `workflow_runs`** (one run per annotation as it moves through the chain).

See [reference.md](reference.md) for the full reference. Consult it when:

- Reading or reporting on approval state (`workflow_runs`, `current_step`, `workflow_status`) and the activity trail (`workflow_activities`).
- Inspecting a workflow's steps, modes (`any`/`all`/`auto`), conditions, and ordering.
- Finding who a step routed to — approvers surface as `workflow_activity.assignees` (user URLs), **not** on the step.
- Re-running an annotation through an active workflow via `POST /v1/workflow_runs/{id}/reset`.

> **Read this first:** approval workflows are a **paid feature configured by Rossum**; the public API is **read-only** for workflows, steps, runs, and activities (confirmed via the OpenAPI docs and a live `OPTIONS` probe — `Allow: GET, HEAD, OPTIONS`). The only write action is `POST /v1/workflow_runs/{id}/reset` (requires the annotation to be `in_workflow`). There is **no** create/edit API for workflows, steps, or the assignment rules that pick approvers, and no `workflow_step_users` resource (404) — coordinate workflow changes with Rossum.

Cross-references:

- `rossum-reference` — platform overview, the Hooks section, and the hook-event decision table (workflow/status transitions fire on `annotation_status.changed`).
- `prd-reference` — `prd2 pull` captures workflows as read-only JSON for inspection/versioning.
- `business-rules-reference` — native Rule conditions and actions (a separate surface from workflow-step conditions).
