---
name: iterate
description: Iterate on a Rossum deliverable (hook, formula, rule, schema change) against a specific annotation until a stated goal is met. Provides the re-fire primitives via MCP — soft re-fire (start → content/validate → cancel), status toggle, in-place re-extract, re-upload, and export re-fire (status → exporting) — to re-evaluate a document after a code change without leaving Claude Code. Use when finishing a deliverable, when the user says "iterate until you reach the goal", "test this against annotation X", "verify this works on document Y", "re-test the export", "export this document again", or when the user invokes a goal-style prompt. Also use when a formula, schema or rule change must be re-evaluated on a document that already exists — "trigger the recalc", "recompute the formulas", "refresh this document", "the field still shows the old value after my push", "re-run matching on this annotation".
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
- You have just delivered **or updated** a hook/formula/rule/schema change in a `prd2` project and the user has not yet confirmed it works end-to-end on a real annotation. **Whenever you finish a deliverable, proactively offer to verify it — do not wait to be asked** (see "UX entry prompt" below). Always verify against a **sandbox/UAT** annotation, never production.

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
- `rossum_confirm_annotation` (fires real export / approval routing — the least reversible op here)
- `prd2 push` and `prd2 deploy` commands

Read-only operations are fine without confirmation: `rossum_get_annotation` (compact merged view), `rossum_get_annotation_meta`, `rossum_get_annotation_content`, `rossum_list_hook_logs`, `rossum_get_document`.

**Never iterate against a production queue.** If the annotation ID belongs to a `prod` queue, stop and ask the user to provide a sandbox/UAT annotation instead. If unsure which environment the ID belongs to, ask before any write. (Sole exception: the production-remediation carve-out below — which is remediation, not iteration, and carries its own stricter gate.)
</HARD-GATE>

### Production remediation — the one carve-out

Distinct from iteration: the goal is not "does my code work" but "this live document is stuck
and the deployed fix would free it". Permitted only with ALL of:

1. The fix is **already deployed and verified by other means** — never use a production
   document to discover whether code works.
2. **Per-document consent**, naming the annotation and the resulting end state, including any
   status change that will not be reverted.
3. **`mode="validate"` only.** Never `reextract` (destroys human corrections), never `confirm`
   (fires a real export / approval routing).
4. **Read the field back** and report observed vs. expected.

Anything broader than one named document is a batch re-automation request, not this skill.

## UX entry prompt

When you have just finished a deliverable (hook/formula/rule change pushed via `prd2 push`, or any equivalent), ask the user **once**, in a single message:

> Want to verify this against a real annotation? Paste a **sandbox/UAT** annotation ID or URL (or say "skip").

- If they paste an ID/URL → continue with the loop below.
- If they say "skip" or anything dismissive → end the skill, no further prompts.

If the user already provided an annotation ID in their request, skip this prompt and use that ID directly.

**Always test on a sandbox/UAT document.** If the only annotation on offer belongs to a `prod` queue, decline it and ask for a sandbox/UAT equivalent — the loop re-fires (and may confirm), which you must never do on production. If you cannot tell which environment the ID belongs to, ask before any write (see the hard-gate above).

If the user has not yet stated a **goal** ("the field `po_status_match` should resolve to `Approved`"), ask once:

> What is the goal? One sentence describing what you expect to see in the result.

The goal becomes the success criterion for each iteration. Write it into the task list so it survives interruptions.

**Turn the goal into explicit assertions.** Before the first re-fire, restate the goal as a concrete, checkable list against the fields the compact response returns — this is what makes the loop *closed* rather than a vibe-check. For example:

| Assertion | Field | Expected |
|---|---|---|
| 1 | `po_status_match` | `Approved` |
| 2 | `supplier_id` | non-empty (matched) |
| 3 | `blocker.items` | none on `amount_total` |

If the user gave a one-line goal, derive the assertion list yourself and show it to them before iterating ("I'll consider it done when: …"). Each iteration is then a pass/fail against this list, not a judgment call.

## Where to test: the original, or a throwaway copy

Re-firing **mutates the annotation you point at** — `validate`/`toggle` recompute and save its datapoints, `confirm` exports it. Sometimes that is fine (the engineer handed you a scratch document and is happy for you to play with it); sometimes the original must stay pristine. **Decide this once, up front — before the first re-fire — not every iteration.**

- **Interactive dev mode** (engineer shares "annotation X is broken, here is my fix") → ask **once**, after you have the annotation + goal:
  > Test on the original directly, or work on a throwaway copy I delete when we're done?
  Use the original only when they confirm it's OK to mutate it. Otherwise make a copy.
- **`/goal` autonomous loop** → you decide the technique. **Prefer the copy** unless the user already said the original is fine.

**How the copy works.** When the original must stay pristine, use `reupload`, not `reextract` — `reextract` re-imports the original in place and replaces its content. `rossum_refire_annotation mode="reupload"` re-uploads the source PDF and returns a **new annotation ID** (`_refire.target_annotation_id`); the original is never touched. Iterate against that new ID from then on. Caveat: re-upload re-runs OCR + extraction from scratch — the copy is a *fresh run of the same document*, not a content-identical clone, so manual corrections on the original are not carried over. For the usual "does my fix make a fresh run come out right?" test that is exactly what you want; if your assertions depend on specific human-entered values, test the original (with confirmation) instead.

**Cleanup.** When the loop ends, if you created a copy, **offer to delete it** (don't auto-delete): use `rossum_delete_annotation` (soft-delete by default; add `purge=true` for a permanent, irreversible purge) — a write, so it passes the hard-gate. For a simple status flip without purging, `rossum_patch_annotation` with `status="deleted"` still works. Record the copy's ID in the task list so it doesn't get orphaned. If the copy came from a `rossum_upload_document` call and you need to track the async upload state, `rossum_get_task` polls the task object directly (no-follow GET, surfaces the status behind the `/tasks` 303 redirect).

Either way: **sandbox/UAT only**, never production.

## The six re-fire patterns

Pick the right one based on **which hook event** your deliverable listens for, or which side-effect you need to reproduce. When in doubt, start with **soft re-fire** — it is the lightest and fastest.

| Pattern | MCP tool | Fires hooks on | Use when |
|---|---|---|---|
| **Soft re-fire** ⭐ default | `rossum_refire_annotation` `mode="validate"` | `user_update`, `started` (per actions list) | Iterating on validation rules, MDH matching, field-update hooks, formulas. Returns updated datapoints inline plus the full compact annotation view. |
| **Status toggle** | `rossum_refire_annotation` `mode="toggle"` | `annotation_status.changed` + any status-listening hooks (see the ⚠️ below on `annotation_content.started`) | You need status-transition side-effects. **Not** the lever for a started-only content hook — send `actions=["user_update", "started"]` to soft re-fire instead. |
| **Re-extract** ⭐ for initialize | `rossum_refire_annotation` `mode="reextract"` | `annotation_content.initialize`, full OCR, doc-type detection | Iterating on `initialize` hooks or OCR-adjacent logic. Re-imports **in place**: same annotation ID, no duplicate document. **Replaces extracted content.** |
| **Re-upload** | `rossum_refire_annotation` `mode="reupload"` | `annotation_content.initialize`, full OCR, doc-type detection | Same events as re-extract, but when the original must survive untouched. **Produces a new annotation ID** (returned in the response). |
| **Direct edit + re-fire** | `rossum_update_annotation_content` → `rossum_refire_annotation` `mode="validate"` `actions=["user_update"]` | `user_update` | Testing a hook that reacts to one specific datapoint change. The content edit itself needs **no** start/cancel lock. |
| **Export re-fire** | `rossum_patch_annotation` `status="exporting"` | the export leg only — export hook / connector, `annotation_status.changed` | Iterating on an export pipeline, export template, or connector against an annotation that has **already** exported. One PATCH, no lock, no re-upload. Does not re-run confirmation or approval routing. |

### Re-extract vs re-upload — both fire `initialize`

`reextract` is the API call behind the UI's **Re-extract** button:

```
PATCH /v1/annotations/{id}   {"status": "importing", "rir_poll_id": null, "messages": []}
```

Prefer it for `initialize` work. It keeps the annotation ID stable — so your assertions, task list
and cached payloads all keep pointing at the same document — and it does not upload a second copy
of the PDF. Measured at roughly a minute on a 25-page document; the tool polls past `importing`
for you (`poll_timeout`, default 180 s).

**It replaces extracted content.** That makes it the wrong tool for a document carrying human
corrections you need to keep — see *Where to test* above. `reupload` remains correct there: it
leaves the original completely untouched and gives you a fresh annotation to work on instead.

### Soft re-fire — the canonical path

```
rossum_refire_annotation(annotation_id=<id>, mode="validate", actions=["user_update", "started"])
```

What it does, atomically:

1. `POST /annotations/{id}/start` — locks to caller.
2. `POST /annotations/{id}/content/validate` — fires the hook chain **for the requested actions**. `user_update` fires off the back of *changed datapoints*, so a validate that changes nothing can legitimately produce zero hook runs.
3. `POST /annotations/{id}/cancel` — **in a try/finally**, so the lock is always released even on the error path.
4. Fetches annotation, content, automation_blocker, and recent hook logs.
5. Returns the **compact merged view** (same shape as `rossum_get_annotation`) plus a `_refire` section showing what was done, and writes the raw payload to `.rossum-cache/annotations/<aid>.json`.

> **All three calls return 200/204 and bump `modified_at` whether or not any hook ran.** A clean status transition is not evidence of execution — see [Prove the hook ran](#prove-the-hook-ran--before-you-interpret-the-result) before you read anything into the result.

**Action selection.** Two combinations are known to work: `["user_update"]` and
`["user_update", "started"]`. `started` is **not** accepted on its own. (The other
`annotation_content` action names — `updated`, `initialize`, `export` — were not probed; there is
no reason to send them to `validate`.)

- `["user_update"]` — fastest, but ONLY when you are also editing a datapoint. `user_update`
  fires off the back of *changed* datapoints, so on a pure recalc (no edit) it emits nothing
  and you get zero hook runs — a silent no-op that reads as "my fix didn't work".
- `["user_update", "started"]` — **the default.** Use it for a pure recalc (no edit), when the
  hook listens on `annotation_content.started` (lazy lookups, one-time info messages), or
  whenever you are iterating on a chain that mixes patterns.
- `["started"]` alone is **rejected by the API** — `HTTP 400
  {"actions":["Selected actions: [HookActions.started] not allowed."]}`. To reach a
  `started`-only hook you must still send `user_update` alongside it; the extra action is
  harmless (with no datapoint change it emits nothing of its own).

Measured against a live environment, on the same annotation:

| `POST /content/validate` body | result |
|---|---|
| `{}` | 200 — **0** `annotation_content` runs |
| `{"actions": ["user_update"]}` | 200 — **0** runs (no datapoint was edited) |
| `{"actions": ["started"]}` | **400** `Selected actions: [HookActions.started] not allowed.` |
| `{"actions": ["user_update", "started"]}` | 200 — **full extension chain fires** |

> **A bare `POST /annotations/{id}/start` fires no `annotation_content` event at all** — only
> `annotation_status.changed`. It is the natural thing to reach for ("open the document, let the
> hooks run"), and the resulting silence looks exactly like a broken hook subscription. `start`
> only takes the review lock; `content/validate` is what runs the content chain.

**Reading the result.** The compact response has:
- `fields` — flat `{schema_id: {value, ocr?, normalized?, src, score?}}`. `src` is one of `human/formula/connector/rules/data_matching/score/NA`.
- `tables` — `{schema_id: {count, rows: [{cell_schema_id: {...}}, ...]}}`.
- `blocker.items` — resolved automation_blocker items (type, level, schema_id, content).
- `recent_hooks` — last N hook log entries with `took_ms`.
- `_refire` — `{mode, source_annotation_id, target_annotation_id?, actions, ...}`.
- `_meta.full_payload_cache` — path to the raw JSON if you need positions, OCR coords, raw RIR text, etc.

### Forcing a formula recalc after a schema push ⭐ common

A formula/schema `prd2 push` does **not** touch documents that already exist — they keep their
old computed values until something re-runs the content pipeline. To re-evaluate one:

    rossum_refire_annotation(annotation_id=<id>, mode="validate",
                             actions=["user_update", "started"])

**`actions` must include `started`.** A pure recalc changes no datapoint, so `user_update`
emits nothing on its own.

**`POST /content/validate` is what does the work.** Measured on annotation 22429840
(2026-08-18), across the four calls a soft re-fire makes:

| call | events fired | recomputes? |
|---|---|---|
| `PATCH {"status":"to_review"}` | `annotation_status.changed` ×6, all self-skipped | **no** |
| `POST /start` | `annotation_status.changed` ×6, all self-skipped | **no** |
| `POST /content/validate` | **`annotation_content.started` ×9** — full extension chain | **yes** — 263 datapoints |
| `POST /cancel` | `annotation_status.changed` ×6, all self-skipped | **no** |

A status toggle alone is therefore the wrong lever — `annotation_status.changed` is not a
recompute trigger. Do not reach for `mode="toggle"` for formulas.

**Proof to demand before believing it worked:** `_refire.updated_datapoints_count > 0`, plus a
fresh `annotation_content.*` hook-log entry timestamped after the call. Then read the target
field back and assert the new value. A 200 is not a result.

### Status toggle

```
rossum_refire_annotation(annotation_id=<id>, mode="toggle", wait_seconds=15)
```

PATCH status `postponed → to_review`, wait, then read. Slower than validate (one round-trip per status PATCH + the wait). It fires `annotation_status.changed` and any status-listening hooks. Engine re-extraction is **not** triggered.

> ⚠️ **Whether a status toggle also emits `annotation_content.started` is unresolved.** This
> section has long claimed it does, but the measured table above records a status PATCH emitting
> `annotation_status.changed` only, with **no** recompute. That row does not record which status
> the annotation came *from*, so it may not be the `postponed → to_review` transition the toggle
> performs — which is exactly why neither claim is settled. Until someone probes
> `postponed → to_review` on a live annotation and reads the hook log, **do not
> reach for toggle to re-run the content chain** — soft re-fire with
> `actions=["user_update", "started"]` is the primitive that is measured to do it.

### Re-upload

```
rossum_refire_annotation(annotation_id=<id>, mode="reupload", poll_timeout=180)
```

Fetches the source PDF, uploads it to the same queue, polls past `importing`, and defensively auto-restores from `deleted` if a custom customer dedup hook transitioned the new annotation (see Gotchas — the stock Duplicate Handling extension does not delete). **Returns a new annotation ID** in `_refire.target_annotation_id` — record the mapping in your task list. Use only when your change touches OCR or `annotation_content.initialize` hooks.

### Direct edit + re-fire

When a hook reacts to one specific datapoint change, you can mutate that datapoint and then re-fire `user_update` to make the hook see the change. Two-step:
1. `rossum_update_annotation_content` — writes datapoint values via the content-operations endpoint. **No review lock needed**: the endpoint edits `to_review`/`postponed` annotations directly and leaves the status untouched — do not wrap it in start/cancel. (A `reviewing` annotation is allowed only when the session is your own; the tool refuses foreign sessions.) **Caveat:** the bulk endpoint returns HTTP 200 but silently no-ops on enum-typed and `ui_configuration.type=manual` fields (e.g. `document_type`) — read back with `fields=[...]` to confirm the edit landed; for those fields use the per-datapoint `PATCH /annotations/{id}/content/{datapoint_id}` instead.
2. `rossum_refire_annotation` `mode="validate"` with `actions=["user_update"]` — the re-fire (unlike the edit) DOES need the review session; bare `rossum_validate_content` on a non-started annotation returns HTTP 409.

For most iteration loops you will NOT need this — soft re-fire on the saved annotation already exercises the hook chain.

### Confirm — the export / approval-routing trigger

When the goal is about what happens **at confirmation** (export payload, approval-workflow routing, `annotation_content.confirmed` hooks), re-firing `validate` is not enough — you must actually confirm. Use `rossum_confirm_annotation` (`POST /annotations/{id}/confirm`): it transitions the annotation to `exported`/`exporting` (or `confirmed`, or `in_workflow`) and **fires the downstream export / approval routing** — a real, not-easily-reversible side effect, so it passes through the hard-gate like any write, and **never on prod**. Confirm the *correct* way (this endpoint), not by patching status to `confirmed` directly — a status PATCH skips the very confirmation logic you are usually trying to test. (The one exception is re-firing the export leg of an annotation that has *already* confirmed and exported, where a status PATCH is the only primitive available — see **Export re-fire** below.) **Precondition:** the annotation must be in `reviewing` (i.e. started) first — call `rossum_start_annotation`, then confirm; confirming a `to_review` annotation returns HTTP 409. (Shipped via the `rossum_confirm_annotation` tool; if your server predates it, that tool may be absent — fall back to the UI for the confirm leg rather than patching status.)

### Export re-fire — re-testing the export leg

Once an annotation has exported, `rossum_confirm_annotation` cannot re-fire the export: confirmation is a one-time transition and the annotation is already past it. To re-run **just the export leg** — the export hook, the Request Processor pipeline, the export template, the connector — patch the status back to `exporting`:

```
rossum_patch_annotation(annotation_id=<id>, status="exporting")
```

That is the entire loop. Entering `exporting` is itself what triggers the export leg, so there is no start/cancel lock to take, no content to touch, and no re-upload. Edit the hook config or template, patch the status, read the hook log. This makes it by far the fastest way to iterate on export output.

What it does and does not do:

- **Fires** the export hook / connector and `annotation_status.changed`.
- **Does not** fire `annotation_content.confirmed`, re-run approval-workflow routing, or re-evaluate validation rules. If the goal depends on confirmation-time logic rather than the export itself, use **Confirm** above against a fresh annotation instead.
- **Outcome states.** A successful export returns the annotation to `exported`; a failure lands it in `failed_export`. An annotation that sits in `exporting` means the connector never reported back — read the hook log rather than re-patching, since a second PATCH while the first run is still in flight can double-fire the export.

Because this re-sends whatever the export target is, treat it exactly like `confirm` for safety: it passes the hard-gate, and **never on prod** — a re-fire against a live connector re-delivers a real document downstream.

### Re-running automation in place — `reautomate` (internal, not available via SA tokens)

Rossum has `POST /api/v1/internal/annotations/reautomate` — a batch endpoint that re-runs the **initialize + automation** pipeline on existing annotations *without re-uploading* (status → `importing`, content preserved, fires `annotation_content.initialize`, then the automation decision → `to_review` or `confirmed`/`exporting`; with `if_modified: try_to_confirm` it simulates open→Confirm for API-modified annotations; non-`to_review` annotations are skipped). It is the natural primitive for *"master data changed — re-run matching/automation on these N documents."*

**It is Rossum-staff-only.** Verified live (NXP sandbox, 2026-06-18): even an `organization_group_admin` token returns **HTTP 403 `permission_denied`**. No SA/customer token can call it, so it is intentionally **not** wrapped as an MCP tool. Do not reach for it. For an in-place re-run accessible to SAs, use **status toggle** (re-runs the hook chain) or **re-upload** (true re-extraction, new annotation id) above. If you genuinely need batch re-automation, that is a Rossum-staff / feature request, not an SA-token operation.

## Prove the hook ran — before you interpret the result

**A hook that never ran and a hook that ran and skipped look identical from outside.** Both leave the field unchanged; both return 200/204; both bump `modified_at`. Skip this step and you will eventually "diagnose" a logic bug in code that never executed — and every fix you then invent to explain the non-effect is unfalsifiable.

Establish execution *first*, in this order of preference:

1. **A hook log entry timestamped after your re-fire** — `recent_hooks` in the re-fire response, or `rossum_list_hook_logs(hook=<id>, annotation=<id>)`. A *fresh* timestamp is proof; an entry from an earlier run is not.
2. **`rossum_test_hook`** — returns `{generated_payload, hook_result}`, where `hook_result.log` is the hook's own output and `stacktrace` its failure. It needs no log endpoint, which makes it the harness of record when logs are unavailable (see Gotchas). It is a dry-run: it does not mutate the annotation.
3. **A UI open** — genuinely fires `annotation_content.started`. Fine as a cross-check, but it overwrites your last-seen state (see Gotchas).

**Never infer execution from the absence of an effect.** If none of the three is available, say so and stop — do not build a root-cause hypothesis on a guess about whether the code ran.

### "The payload is missing field X" has a real answer — don't hand-build one

When the hypothesis is *"the hook ran, but its gate skipped because field X isn't in the payload"*, do not construct a payload by hand and delete X from it. That shows only that the gate **could** skip on such a payload — never that the live payload looks that way. Ask the platform instead:

```
rossum_generate_hook_payload(hook_id=<id>, event="annotation_content", action="user_update", annotation_id=<id>)
```

It returns the payload the platform would actually send for that event/action against that annotation (credentials redacted), read-only, without executing the hook. Two facts worth knowing before you go looking:

- **`annotation.queue` is always present** — it is a required field on the annotation serializer, so no code path omits it — and it is a **URL string** (`.../api/v1/queues/<id>`), never an int. `payload["annotation"]["queue"] == 1234` is therefore always false; parse the id out with `int(url.rsplit("/", 1)[1])`. A queue gate comparing the raw value to an integer fails silently and looks exactly like a missing field.
- **Full queue objects** (`name`, settings) appear in a top-level `queues[]` array only when the hook's `sideload` list asks for them.

Formulas and Rule `trigger_condition` expressions are a different matter: queue identity is not exposed to the formula language at all (see `txscript-reference`), so gate those on a field value, not on the queue.

## The iteration loop

Repeat until the goal is met or the user stops you:

1. **Edit local code.** Modify the `.py` file under the prd project's `formulas/`, `hooks/`, or `rules/` directory. **Never edit the `formula` field inside `schema.json` or the `code` field inside hook JSON** — `prd2 push` syncs `.py` files into JSON automatically. (Project rule, see `CLAUDE.md`.)
2. **Push, gated.** Stage only the modified files and run `prd2 push <env> -io`. Confirm the file list with the user before executing.
3. **Re-fire via `rossum_refire_annotation`** in the right mode. The default `validate` mode is correct for most cases.
4. **Prove the hook ran.** Confirm a hook log entry timestamped after this re-fire, or fall back to `rossum_test_hook` (see [Prove the hook ran](#prove-the-hook-ran--before-you-interpret-the-result)). A 200/204 is not execution. If you cannot establish it, stop here and say so — everything after this step is worthless without it.
5. **Read the result.** Use the compact response's `fields`, `blocker.items`, and `recent_hooks` sections. If you need raw positions or OCR coordinates, `Read` the cache file at `_meta.full_payload_cache`.
6. **Check the assertions — emit a PASS/FAIL table.** Evaluate each assertion from the goal list against the compact response and print a row per assertion: `assertion · field · expected · observed · ✅/❌`. The loop is **green only when every row passes**. If any fail → check `recent_hooks` for failures or `rossum_list_hook_logs(annotation=<id>)` for older logs; modify the code; loop. Do not declare success on a partial pass or a "looks right" — every assertion must be ✅, or you state which are still ❌ and keep going.
7. **Bound the loop.** Default `--max-iterations=5`. After 5 unsuccessful iterations, stop and present the current state with the root-cause hypothesis — do not silently keep trying. The user decides whether to keep going.

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

**When log retrieval itself fails, switch harness — do not start guessing.** Log endpoints are not available in every deployment/token combination; `GET /hooks/{id}/logs` and the `/logs` and `/hook_logs` variants can all return 404. When that happens, `rossum_list_hook_logs` can tell you nothing about whether the hook fired, and "no logs" is **not** the same signal as "no log entry for this hook". Fall back to `rossum_test_hook`, which returns `log` and `stacktrace` inline in its response and so does not depend on any log endpoint.

## Gotchas

- **A 200/204 and a bumped `modified_at` are not proof a hook ran.** `start`, `content/validate` and `cancel` all succeed and walk the status through the transition without necessarily emitting a content event that any hook listens for — a `validate` with no datapoint change can produce zero hook runs. Establish execution explicitly (see [Prove the hook ran](#prove-the-hook-ran--before-you-interpret-the-result)).
- **Hook log retrieval can be unavailable per deployment.** `GET /hooks/{id}/logs` (and the `/logs`, `/hook_logs` variants) may 404 depending on environment and token. Then no-run and ran-and-skipped are externally identical, and `rossum_list_hook_logs` cannot break the tie — use `rossum_test_hook`, whose `log` comes back inline.
- **`show_info` messages do not reliably surface on the annotation.** Don't use them as a debug channel: a missing message is not evidence that a branch was skipped. Read the `log` from `rossum_test_hook` instead.
- **The start/cancel lock is for `content/validate` and `confirm` only — NOT for content edits.** `POST /content/operations` (i.e. `rossum_update_annotation_content`) succeeds without a review session on `to_review` and `postponed` annotations and preserves the status; wrapping it in start/cancel just adds two pointless status transitions and takes a lock you do not need. `content/validate` and `confirm`, by contrast, return HTTP 409 unless the annotation is in `reviewing`.
- **`cancel` is automatic in `mode="validate"`** — the MCP tool wraps cancel in try/finally. If you ever call `rossum_start_annotation` standalone, you MUST call `rossum_cancel_annotation` afterwards (the start tool's success message includes a reminder). Cancel restores the **pre-start** status — a `postponed` annotation returns to `postponed`, not `to_review`.
- **`content/validate` actions must include the trigger your hook listens on — and `started` cannot travel alone.** If the hook only listens on `started` and you send `actions=["user_update"]`, the hook will not fire. The fix is `actions=["user_update", "started"]`, **not** `actions=["started"]` — that returns `HTTP 400 {"actions":["Selected actions: [HookActions.started] not allowed."]}`. Cross-check the hook's `events` array against the actions list.
- **`POST /start` on its own fires nothing a content hook can hear.** It emits `annotation_status.changed` only — no `annotation_content.*` event. Taking the lock is not running the chain; `content/validate` is.
- **Custom dedup hooks may auto-delete re-uploads (defensive).** Some customer queues have a custom hook on `annotation_content.initialize` that PATCHes `status: deleted` for duplicate documents. The **stock Rossum Duplicate Handling extension does NOT do this** — its valid actions are `fill_field`, `forward_annotation`, `mark_duplicate`, `show_message`, `stop_automation`, `apply_label`; none transition status. (`mark_duplicate` flags the annotation but leaves it in `to_review`.) For customer-custom delete patterns, `mode="reupload"` defensively detects `status: deleted` after upload and restores via PATCH. There is now a `rossum_upload_document` tool for uploading a local file to a queue (modern `/uploads` API); note it performs the upload but **not** the dedup auto-restore, so on a queue with a custom delete-on-duplicate hook, replicate the `status: deleted` → `to_review` check yourself.
- **The `reviewing` lock scopes validate/confirm — raw content writes bypass it.** Between start and cancel the annotation is locked to the calling user: `content/validate` and `confirm` from another caller 409. Raw `content/operations` from another caller still lands, which is worse — it mutates values under the reviewer's live session. That is why `rossum_update_annotation_content` refuses a `reviewing` annotation unless the session is the caller's own.
- **Engine re-extraction is not triggered by status toggle.** Only the hook chain re-runs. If your change touches OCR or extraction itself, use `mode="reupload"` — toggle will not produce different captured values.
- **Hook outputs are unstable on re-open.** If you open the annotation in the Rossum UI between re-fires, that itself fires `annotation_content.started` again and may overwrite your last-seen state. Capture immediately after each re-fire.
- **`.rossum-cache/` should be gitignored.** The MCP server writes the raw merged payload there on every `rossum_get_annotation` / `rossum_refire_annotation` call. Add `.rossum-cache/` to the project's `.gitignore` when you start using `iterate`.
- **`failed_export` (and `exported`/`deleted`) cannot be re-fired — `/start` 409s.**
  `POST /start` returns `HTTP 409 conflict_status: "Assignment is allowed only for documents in
  states: to_review, reviewing, postponed, confirmed"`. A failed-export document therefore needs
  `PATCH {"status":"to_review"}` first. **Flag the consequence before doing it:** `cancel`
  restores the *pre-start* status, which is now `to_review` — the document leaves the
  failed-export bucket **permanently** and does not return to it. AP teams may track rejected
  exports by that bucket, so this needs its own consent, separate from consent for the re-fire.
- **`annotation_status.changed` is not a recompute trigger.** Hooks on that event typically gate
  themselves to confirmed/exported statuses and log a skip. A status PATCH bumps `modified_at`
  and changes nothing computed.
- **Correction — content hooks CAN be re-fired via the API, but only by one endpoint.** Older
  notes claim `/start`, `/cancel` and `/validate` "all succeed and bump `modified_at` but emit no
  `annotation_content.*` event". That is **right about `/start` and `/cancel`** — they emit
  `annotation_status.changed` only. It is **wrong about `POST
  /annotations/{id}/content/validate`**, which with an explicit `actions` list emitted 9 content
  events and re-ran the whole chain. (The endpoint that emits nothing is almost certainly the
  *different* `POST /annotations/{id}/validate`.) So: do not skip a recalc on the strength of the
  old claim — but do not expect the lock calls to do the work either.

## When to stop and hand off

- **All assertions green** → confirm with user, end the loop. If you tested on a throwaway copy, offer to delete it now (`rossum_delete_annotation`, gated; default is soft-delete, add `purge=true` for permanent) — don't leave it orphaned.
- **Max iterations reached without success** → stop, present current state + root-cause hypothesis, let user decide.
- **The deliverable needs cross-environment verification** → hand off to `test-behavioral-equivalence` for a full corpus regression. `iterate` confirms one document; equivalence confirms the population.
- **Goal turns out to be wrong / ambiguous** → stop and ask the user to clarify before another iteration.

## Important

- Never iterate against a production queue. Sandbox or UAT only. (Sole exception: the production-remediation carve-out under Safety.)
- Every write op passes through the hard-gate, every time.
- `mode="validate"` is the default — start there; reach for toggle/reupload only when you need them.
- Edit local `.py` files only; let `prd2 push` sync into JSON.
- Bound the loop. 5 iterations max by default; stop and surface state if not met.
- If you made a throwaway copy, offer to delete it when the loop ends — on **any** exit path (success, max-iterations, or abandon), not just success. Offer, don't auto-delete.
