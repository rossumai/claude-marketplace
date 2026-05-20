---
name: verify-change
description: Inner dev loop for Rossum hook iteration. Trigger one annotation through a deployed hook change, observe hook logs and changed field values, repeat. Use when actively editing a hook and you want a tight cycle. Triggers on "verify this change", "did my hook land", "trigger and check", "iterate on this hook", "re-run this annotation", "test this on one annotation". For pre-promote regression checks across a corpus, use `test-behavioral-equivalence` instead.
argument-hint: [annotation-id-or-url] [--env=<name>] [--trigger=toggle|confirm|patch] [--patch=<schema_id>=<value>] [--timeout=<s>]
allowed-tools: Read, Grep, Glob, Bash, Agent
context: fork
---

# Verify a Rossum Hook Change

You are a Rossum.ai Solution Architect iterating on a hook change. Each invocation triggers one annotation through the current deployed state and shows you what happened: which hooks fired, what they logged, what changed on the annotation. Single annotation, no corpus, no two-environment comparison — that's `test-behavioral-equivalence`'s job.

> Path or context: $ARGUMENTS

## Safety: Remote Write Confirmation

<HARD-GATE>
Three write operations exist in this skill. Each gets an explicit confirmation prompt — never batched.

1. **Status toggle** — `PATCH /annotations/<id>` to change status. Show current → target.
2. **Confirm** — `POST /annotations/<id>/confirm`. Enumerate every hook in the chain that will fire on `confirmed`. Call out export-shaped hooks (webhook to external endpoint, SFTP, email) by name. Refuse if the queue name does not match `test|sandbox|uat|dev` unless the user explicitly overrides.
3. **Optional `prd2 push`** — only if the user asked Claude to iterate solo. Show the file list before executing.

Read-only operations (`rossum_get_annotation`, `rossum_get_annotation_content`, `rossum_list_hook_logs`, `rossum_search_annotations`, `rossum_get_queue`, `rossum_list_hooks`) run without confirmation.
</HARD-GATE>

## How to Use This Skill

One invocation = one cycle. Use TaskCreate to track progress through the steps.

### 1. Pick the target annotation

Resolution order:

- **Argument provided** (`<annotation-id-or-url>`) → use it. Write `{annotation_id, env_name, trigger_mode, ts}` to `.rossum-verify/last.json` for next time.
- **Cache exists** (`.rossum-verify/last.json`) → reuse the last annotation. Surface it: *"Re-using annotation 12345 on env `uat` from last run — override?"* Proceed if no override.
- **Neither** → ask the user. Two options:
  1. Paste an annotation ID or Rossum UI URL.
  2. Let Claude propose a candidate via `rossum_search_annotations` (recent, in the queue holding the hook being edited — derive the queue from local `git diff` against the hook files).

### 2. Optional: push local changes

Skipped by default — the user usually pushes themselves. Two conditions trigger an offer:

- The user explicitly asked Claude to iterate solo on this hook.
- `git diff` shows uncommitted `.py` changes under the project tree on files referenced by hooks in the target env.

When offered, list the files about to be pushed, then ask. On confirmation, run:

```
prd2 push <env-name> -io <file1> <file2> ...
```

See [`prd-reference`](../prd-reference/SKILL.md) for `prd2 push` semantics. Never run without a `yes`.

### 3. Capture the before-snapshot

Read-only. Two API calls:

- `rossum_get_annotation` → status, queue URL, automation_blocker on the annotation.
- `rossum_get_annotation_content` → full content tree (value, message, automation_blocker per datapoint).

Hold the result in memory; do not write it to disk. Next iteration's snapshot replaces it.

Record a `trigger_started_at` timestamp for the hook-log filter in step 6.

### 4. Trigger the hook chain

Pick the trigger mode (default `toggle`). See **Trigger Modes** below for the API sequence per mode. Each write op passes through the safety gate above.

### 5. Wait for hooks to settle

Poll `rossum_get_annotation` every 2 seconds. Settled when status returns to the resting state for the mode (`to_review` for toggle/patch, `confirmed`/`exported` for confirm) AND no pending hook events are reported.

Hard timeout: default 30s. Override via `--timeout=<s>`. On timeout, proceed to step 6 and tag the report with "**hook chain did not settle within timeout** — logs and after-state may be partial".

### 6. Fetch logs and after-snapshot

- `rossum_list_hook_logs` filtered by `annotation_id` and `started_at >= trigger_started_at`.
- `rossum_get_annotation_content` again → after-snapshot.

### 7. Report

Render the inline report (see **Output Format**). No file is written. Two sections: hook logs, then field diff. Done — return to the chat.

## Trigger Modes

| Mode | API sequence | Event fired | Use case |
|------|--------------|-------------|----------|
| `toggle` *(default)* | `PATCH /annotations/<id>` status → `postponed`, then status → `to_review` | `annotation_content.started` | Most validation/extraction hooks. The common case. |
| `confirm` | `POST /annotations/<id>/confirm` | `annotation_content.confirmed` + downstream export hooks | Approval-workflow hooks, export hooks, post-confirm validation. |
| `patch <schema_id>=<value>` | `PATCH /annotations/<id>/content/<dp_id>` with `{"content": {"value": "<value>"}}` | `annotation_content.user_update` | Field-level format/validation hooks. |

### State reset between iterations

The annotation needs to be in the right starting state for each trigger:

- **`toggle`** ends at `to_review` — ready for the next `toggle` run.
- **`patch`** ends at `to_review` — ready for the next `patch` (use a different value or different schema_id).
- **`confirm`** ends at `confirmed` (or `exported`). On a follow-up `confirm` invocation, the skill detects this and offers to `PATCH /annotations/<id>` status → `to_review` first (gated). Without the reset, the next confirm is a no-op and the report will look like "nothing fired" — surface this case explicitly rather than silently producing an empty report.

### Confirm-mode safety detail

Before the first `confirm` on a given annotation in a session:

1. `rossum_get_queue` on the annotation's queue.
2. `rossum_list_hooks` filtered by the queue, then `rossum_get_hook` on hooks whose `events` include `annotation_content.confirmed`.
3. List each such hook by name and type (`function`, `webhook`, etc.). Webhooks pointing to external URLs and any hook named with `export`, `send`, `post`, `submit`, `notify`, `email`, `coupa`, `sap`, `netsuite`, etc. — call out by name.
4. Check the queue name. If it does not match `test|sandbox|uat|dev` (case-insensitive substring), refuse with: *"Queue '<name>' does not look like a test queue. Confirm mode will fire real export hooks — say `override` to proceed anyway."*
5. On `override` or test-queue pass, ask the explicit confirmation: *"Confirm annotation X? This will fire: [hook list]. Are export endpoints safe to fire here? (yes/no)"*

### Patch-mode argument

`--patch=<schema_id>=<value>` is required when `--trigger=patch`. The skill resolves `<schema_id>` to the datapoint ID by looking up `rossum_get_annotation_content` for the current annotation. If the schema_id is not found (typo, wrong queue) → error out before the PATCH.

The patch payload writes value only — page and position are left intact. This is intentional: patch mode is for testing field-level hooks, not for synthesizing prod state (that's `test-behavioral-equivalence`'s job).

## Output Format

(inline report template — written in Task 3)

## State Management

(cache file behavior — written in Task 3)

## Common Errors and Gotchas

(troubleshooting list — written in Task 4)

## When to Use Something Else

(cross-references — written in Task 4)
