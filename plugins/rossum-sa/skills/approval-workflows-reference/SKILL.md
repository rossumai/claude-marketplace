---
name: approval-workflows-reference
description: Rossum approval-workflows reference — how documents route through an ordered chain of approval steps (workflow → ordered workflow_steps → workflow_runs), which endpoints are verified read-only vs. unverified for writes, the real workflow_step fields (name, ordering, type, mode, condition), and the safe prd2-based procedure for changing a workflow. Use whenever the user asks about approval workflows, approval steps or levels, approvers, multi-level sign-off, conditional approval routing, workflow_steps / workflow_runs, or "who approves this document" — even if they don't say the word "workflow".
user-invocable: false
---

# Rossum Approval Workflows Reference

Approval workflows route documents through an ordered chain of decision steps before export. The model is **workflow → ordered `workflow_steps` → `workflow_runs`** (one run per annotation as it moves through the chain).

See [reference.md](reference.md) for the full reference. Consult it when:

- Reading or reporting on approval state (`workflow_runs`, `current_step`, `workflow_status`).
- Authoring or changing a workflow or its steps.
- Deciding which API endpoints are safe to call directly vs. which to drive through `prd2`.

> **Read this first:** only the *read* endpoints are verified against a live instance. Treat every `POST`/`PATCH`/`DELETE` on workflows/steps as **unverified** — prefer `prd2 pull` → edit → `prd2 push` for changes, and confirm against the official API docs before writing direct API calls.

Cross-references:

- `rossum-reference` — platform overview, the Hooks section, and the hook-event decision table (workflow/status transitions fire on `annotation_status.changed`).
- `prd-reference` — the `prd2` pull/push workflow used to change workflows safely.
- `business-rules-reference` — native Rule conditions and actions (a separate surface from workflow-step conditions).
