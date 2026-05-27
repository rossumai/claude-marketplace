---
name: iterate
description: Iterate on a Rossum deliverable (hook, formula, rule, schema change) against a specific annotation until a stated goal is met. Provides the re-fire primitives via MCP — soft re-fire (start → content/validate → cancel), status toggle, and re-upload — to re-evaluate a document after a code change without leaving Claude Code. Use when finishing a deliverable, when the user says "iterate until you reach the goal", "test this against annotation X", "verify this works on document Y", or when the user invokes a goal-style prompt.
argument-hint: [annotation-id-or-url] [--goal=<short description>] [--env=<name>] [--max-iterations=<N>]
allowed-tools: Read, Edit, Write, Grep, Glob, Bash, Agent
---

# Iterate on a Rossum Deliverable Against an Annotation

You are a Rossum.ai Solution Architect closing the inner loop on a deliverable. Something has just been built or changed — a hook, a formula, a rule, a schema field — and the question is: **does it actually produce the intended result on a real document?** This skill teaches you how to re-fire the deliverable against a known annotation, read the result, decide if the goal is met, and iterate.

> Annotation or context: $ARGUMENTS

## When to use this skill

Pick this up automatically when **any** of the following holds:

- The user says "iterate until you reach the goal", "test this against annotation X", "verify this works on document Y", "make this work on <annotation_id>", or any equivalent.
- A `goal:` line, a `Goal:` heading, or a `/goal …` prompt appears in the request.
- You have just delivered a hook/formula/rule/schema change in a `prd2` project and the user has not yet confirmed it works end-to-end on a real annotation. In that case, **offer this skill proactively** (see "UX entry prompt" below).

Do **not** pick this up for cross-environment regression testing — that is `test-behavioral-equivalence`. The split is: `iterate` = tight inner loop on one document during development; `test-behavioral-equivalence` = full regression suite before promoting.

## Safety: Remote API Confirmation Gate

<HARD-GATE>
Before ANY MCP tool or CLI command that **creates, modifies, or deletes** resources in a remote Rossum environment, you MUST:

1. **Present exactly what will be done** — tool name, target environment, annotation ID, what gets created/changed/deleted.
2. **Wait for explicit user confirmation** — do not batch multiple write operations into one approval.
3. **Never proceed without a clear "yes"** from the user.

This applies to:
- `rossum_refire_annotation`, `rossum_start_annotation`, `rossum_cancel_annotation`, `rossum_validate_content`
- `rossum_patch_annotation` (status changes, including `confirmed`/`exporting`)
- `prd2 push` and `prd2 deploy` commands

Read-only operations are fine without confirmation: `rossum_get_annotation` (compact merged view), `rossum_get_annotation_meta`, `rossum_get_annotation_content`, `rossum_list_hook_logs`, `rossum_get_document`.

**Never iterate against a production queue.** If the annotation ID belongs to a `prod` queue, stop and ask the user to provide a sandbox/UAT annotation instead. If unsure which environment the ID belongs to, ask before any write.
</HARD-GATE>

## UX entry prompt

When you have just finished a deliverable (hook/formula/rule change pushed via `prd2 push`, or any equivalent), ask the user **once**, in a single message:

> Want to verify this against a real annotation? Paste an annotation ID or URL (or say "skip").

- If they paste an ID/URL → continue with the loop below.
- If they say "skip" or anything dismissive → end the skill, no further prompts.

If the user already provided an annotation ID in their request, skip this prompt and use that ID directly.

If the user has not yet stated a **goal** ("the field `po_status_match` should resolve to `Approved`"), ask once:

> What is the goal? One sentence describing what you expect to see in the result.

The goal becomes the success criterion for each iteration. Write it into the task list so it survives interruptions.

## The four re-fire patterns

Pick the right one based on **which hook event** your deliverable listens for, or which side-effect you need to reproduce. When in doubt, start with **soft re-fire** — it is the lightest and fastest.

| Pattern | MCP tool | Fires hooks on | Use when |
|---|---|---|---|
| **Soft re-fire** ⭐ default | `rossum_refire_annotation` `mode="validate"` | `user_update`, `started` (per actions list) | Iterating on validation rules, MDH matching, field-update hooks, formulas. Returns updated datapoints inline plus the full compact annotation view. |
| **Status toggle** | `rossum_refire_annotation` `mode="toggle"` | `annotation_content.started` + any status-listening hooks | Hook listens **only** to `started`, or you need full content re-render side-effects. |
| **Re-upload** | `rossum_refire_annotation` `mode="reupload"` | `annotation_content.initialize`, full OCR, doc-type detection | Iterating on `initialize` hooks, OCR-adjacent logic, anything depending on fresh extraction. **Produces a new annotation ID** (returned in the response). |
| **Direct PATCH + validate** | `rossum_patch_annotation` (field PATCH) → `rossum_validate_content` `actions=["user_update"]` | `user_update` | Testing a hook that reacts to one specific datapoint change. Note: schema field PATCH may require a different endpoint — confirm with the user. |

### Soft re-fire — the canonical path

```
rossum_refire_annotation(annotation_id=<id>, mode="validate", actions=["user_update", "started"])
```

What it does, atomically:

1. `POST /annotations/{id}/start` — locks to caller.
2. `POST /annotations/{id}/content/validate` — fires the hook chain.
3. `POST /annotations/{id}/cancel` — **in a try/finally**, so the lock is always released even on the error path.
4. Fetches annotation, content, automation_blocker, and recent hook logs.
5. Returns the **compact merged view** (same shape as `rossum_get_annotation`) plus a `_refire` section showing what was done, and writes the raw payload to `.rossum-cache/annotations/<aid>.json`.

**Action selection.**
- `["user_update"]` — fastest. Use when iterating on a rule or formula that recomputes on field edits.
- `["started"]` — use when the hook listens on `annotation_content.started` (lazy lookups, one-time info messages).
- `["user_update", "started"]` — default; use both when uncertain or when iterating on a chain mixing patterns.

**Reading the result.** The compact response has:
- `fields` — flat `{schema_id: {value, ocr?, normalized?, src, score?}}`. `src` is one of `human/formula/connector/rules/data_matching/score/NA`.
- `tables` — `{schema_id: {count, rows: [{cell_schema_id: {...}}, ...]}}`.
- `blocker.items` — resolved automation_blocker items (type, level, schema_id, content).
- `recent_hooks` — last N hook log entries with `took_ms`.
- `_refire` — `{mode, source_annotation_id, target_annotation_id?, actions, ...}`.
- `_meta.full_payload_cache` — path to the raw JSON if you need positions, OCR coords, raw RIR text, etc.

### Status toggle

```
rossum_refire_annotation(annotation_id=<id>, mode="toggle", wait_seconds=15)
```

PATCH status `postponed → to_review`, wait, then read. Slower than validate (one round-trip per status PATCH + the wait), but fires `annotation_content.started` and any status-listening hooks. Engine re-extraction is **not** triggered — only the hook chain re-runs.

### Re-upload

```
rossum_refire_annotation(annotation_id=<id>, mode="reupload", poll_timeout=180)
```

Fetches the source PDF, uploads it to the same queue, polls past `importing`, auto-restores from `deleted` (dedup workaround). **Returns a new annotation ID** in `_refire.target_annotation_id` — record the mapping in your task list. Use only when your change touches OCR or `annotation_content.initialize` hooks.

### Direct PATCH + validate

When a hook reacts to one specific datapoint change, you can mutate that datapoint and then re-fire `user_update` to make the hook see the change. Two-step:
1. `rossum_patch_annotation` (or, for content datapoints, a direct content PATCH via Bash + curl — not yet wrapped as an MCP tool).
2. `rossum_validate_content` with `actions=["user_update"]`.

For most iteration loops you will NOT need this — soft re-fire on the saved annotation already exercises the hook chain.

## The iteration loop

Repeat until the goal is met or the user stops you:

1. **Edit local code.** Modify the `.py` file under the prd project's `formulas/`, `hooks/`, or `rules/` directory. **Never edit the `formula` field inside `schema.json` or the `code` field inside hook JSON** — `prd2 push` syncs `.py` files into JSON automatically. (Project rule, see `CLAUDE.md`.)
2. **Push, gated.** Stage only the modified files and run `prd2 push <env> -io`. Confirm the file list with the user before executing.
3. **Re-fire via `rossum_refire_annotation`** in the right mode. The default `validate` mode is correct for most cases.
4. **Read the result.** Use the compact response's `fields`, `blocker.items`, and `recent_hooks` sections. If you need raw positions or OCR coordinates, `Read` the cache file at `_meta.full_payload_cache`.
5. **Diff against the goal.** State explicitly: "Goal was X, observed Y." If they match → goal met, ask the user to confirm and exit. If not → check `recent_hooks` for failures or `rossum_list_hook_logs(annotation=<id>)` for older logs; modify the code; loop.
6. **Bound the loop.** Default `--max-iterations=5`. After 5 unsuccessful iterations, stop and present the current state with the root-cause hypothesis — do not silently keep trying. The user decides whether to keep going.

Update tasks at every step. Each iteration is a task ("iteration 3: try X"), marked completed when the re-fire returns.

## Reading hook logs to root-cause failures

When the goal is not met, the first place to look is the `recent_hooks` block returned by `rossum_refire_annotation` — it includes the last N hook log entries already. For more history:

```
rossum_list_hook_logs(hook=<id>, annotation=<id>, max_results=20)
```

Look for:
- `status: failed` plus a Python traceback in `message` → fix the code.
- `status: succeeded` but unexpected `updated_datapoints` → the hook ran but produced the wrong value. Trace inputs.
- No log entry for the hook you just modified → the hook did not fire. Cross-check the trigger event in the hook JSON against the action you sent.

## Gotchas

- **`cancel` is automatic in `mode="validate"`** — the MCP tool wraps cancel in try/finally. If you ever call `rossum_start_annotation` standalone, you MUST call `rossum_cancel_annotation` afterwards (the start tool's success message includes a reminder).
- **`content/validate` actions must include the trigger your hook listens on.** If the hook only listens on `started` and you send `actions=["user_update"]`, the hook will not fire. Cross-check the hook's `events` array against the actions list.
- **Duplicate Handling auto-delete.** Many customer queues have a `Duplicate Handling` hook on `annotation_content.initialize` that auto-deletes re-uploads of the same PDF. `mode="reupload"` already detects `status: deleted` and restores via PATCH; if you upload manually, replicate that check.
- **`reviewing` lock blocks other writes.** Between start and cancel the annotation is locked to the calling user. Don't try to PATCH content from another caller in that window.
- **Engine re-extraction is not triggered by status toggle.** Only the hook chain re-runs. If your change touches OCR or extraction itself, use `mode="reupload"` — toggle will not produce different captured values.
- **Hook outputs are unstable on re-open.** If you open the annotation in the Rossum UI between re-fires, that itself fires `annotation_content.started` again and may overwrite your last-seen state. Capture immediately after each re-fire.
- **`.rossum-cache/` should be gitignored.** The MCP server writes the raw merged payload there on every `rossum_get_annotation` / `rossum_refire_annotation` call. Add `.rossum-cache/` to the project's `.gitignore` when you start using `iterate`.

## When to stop and hand off

- **Goal met** → confirm with user, end the loop.
- **Max iterations reached without success** → stop, present current state + root-cause hypothesis, let user decide.
- **The deliverable needs cross-environment verification** → hand off to `test-behavioral-equivalence` for a full corpus regression. `iterate` confirms one document; equivalence confirms the population.
- **Goal turns out to be wrong / ambiguous** → stop and ask the user to clarify before another iteration.

## Important

- Never iterate against a production queue. Sandbox or UAT only.
- Every write op passes through the hard-gate, every time.
- `mode="validate"` is the default — start there; reach for toggle/reupload only when you need them.
- Edit local `.py` files only; let `prd2 push` sync into JSON.
- Bound the loop. 5 iterations max by default; stop and surface state if not met.
