# `verify-change` Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight `verify-change` skill to the `rossum-sa` plugin for the inner dev loop on Rossum hooks: trigger one annotation, observe hook logs + changed field values, iterate.

**Architecture:** Single `SKILL.md` (~150-250 lines) under `plugins/rossum-sa/skills/verify-change/`. No bundled scripts — orchestration is owned by the LLM using existing MCP tools. Cache file `.rossum-verify/last.json` in the customer's project root holds the last-used annotation. Three trigger modes (`toggle` / `confirm` / `patch`) cover `annotation_content.started`, `.confirmed`, and `.user_update` events.

**Tech Stack:** Markdown skill, MCP `rossum-api` tools (`rossum_get_annotation`, `rossum_get_annotation_content`, `rossum_patch_annotation`, `rossum_list_hook_logs`, `rossum_search_annotations`, `rossum_get_queue`), `prd2` CLI for the optional push step.

**Spec:** `docs/superpowers/specs/2026-05-20-verify-change-skill-design.md`

---

## File Structure

| Path | Action | Purpose |
|------|--------|---------|
| `plugins/rossum-sa/skills/verify-change/SKILL.md` | Create | The skill itself — front-matter, workflow, trigger modes, safety gates, output template, gotchas |
| `README.md` | Modify (line ~62) | Add a row to the rossum-sa skill table |
| `.gitignore` | Modify | Add `.rossum-verify/` so the cache doesn't get committed when the skill is tested inside this repo |
| `plugins/rossum-sa/.claude-plugin/plugin.json` | Modify | Bump `version` |
| `plugins/rossum-sa/mcp-servers/rossum-api/server.py` | Modify (line 2292) | Bump `serverInfo.version` to match |

---

## Versioning note

Current state on `main`: `0.12.0`. The open PR #21 (rename-test-skill) bumps to `0.13.0`. This work happens on a branch off `main`. **Before bumping, check whether PR #21 has merged.** If yes, bump to `0.14.0`. If no, bump to `0.13.0` and expect a rebase + version conflict when PR #21 lands (resolve to `0.14.0`).

---

## Task 1: Scaffold the skill file

**Files:**
- Create: `plugins/rossum-sa/skills/verify-change/SKILL.md`

- [ ] **Step 1: Create directory and SKILL.md with front-matter + section skeleton**

Create `plugins/rossum-sa/skills/verify-change/SKILL.md` with this exact content:

````markdown
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

(workflow narrative — written in Task 2)

## Trigger Modes

(table + per-mode state-reset notes — written in Task 2)

## Output Format

(inline report template — written in Task 3)

## State Management

(cache file behavior — written in Task 3)

## Common Errors and Gotchas

(troubleshooting list — written in Task 4)

## When to Use Something Else

(cross-references — written in Task 4)
````

- [ ] **Step 2: Verify the file exists with the expected scaffold**

Run: `wc -l plugins/rossum-sa/skills/verify-change/SKILL.md && head -10 plugins/rossum-sa/skills/verify-change/SKILL.md`
Expected: line count around 40, front-matter visible.

- [ ] **Step 3: Commit**

```bash
git add plugins/rossum-sa/skills/verify-change/SKILL.md
git commit -m "Scaffold verify-change skill (front-matter + sections)"
```

---

## Task 2: Workflow and trigger modes

**Files:**
- Modify: `plugins/rossum-sa/skills/verify-change/SKILL.md` (replace the `## How to Use This Skill` and `## Trigger Modes` placeholder sections)

- [ ] **Step 1: Replace the workflow section**

Replace the `## How to Use This Skill\n\n(workflow narrative — written in Task 2)` block with:

````markdown
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
````

- [ ] **Step 2: Replace the trigger modes section**

Replace the `## Trigger Modes\n\n(table + per-mode state-reset notes — written in Task 2)` block with:

````markdown
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
````

- [ ] **Step 3: Verify section content present**

Run: `grep -c "^## " plugins/rossum-sa/skills/verify-change/SKILL.md`
Expected: 7 (Safety, How to Use, Trigger Modes, Output Format, State Management, Common Errors, When to Use Something Else).

Run: `grep -n "Trigger Modes\|How to Use" plugins/rossum-sa/skills/verify-change/SKILL.md`
Expected: both lines present and not empty stubs.

- [ ] **Step 4: Commit**

```bash
git add plugins/rossum-sa/skills/verify-change/SKILL.md
git commit -m "Add workflow and trigger modes to verify-change skill"
```

---

## Task 3: Output format and state management

**Files:**
- Modify: `plugins/rossum-sa/skills/verify-change/SKILL.md` (replace `## Output Format` and `## State Management` placeholders)

- [ ] **Step 1: Replace the output format section**

Replace the `## Output Format\n\n(inline report template — written in Task 3)` block with:

````markdown
## Output Format

Inline chat report only — no file written. Two sections, terse.

### Template

```
Annotation <id> (queue "<queue-name>", env "<env>")
Trigger: <mode>  (<before-status> → <after-status> in <duration>)
<warning line if hooks did not settle within timeout>

Hook logs (N hooks fired):
  <✓ or ✗> <hook-name> (<hook-type>, <duration_ms>ms)
      <condensed stdout/stderr — first 3 lines max>
      <full traceback if hook errored>

Changed fields (N):
  <schema_id>                 "<before>" → "<after>"      <classification tag if any>
  <schema_id>                 +"<new message>"
  automation_blocker          (added: "<text>")
  automation_blocker          (removed: "<text>")
```

### Format rules

- **Hook logs:**
  - Glyph `✓` for `status: completed`, `✗` for `status: failed`, `⊘` for `status: skipped`.
  - One line per hook with type and duration, then indented log body.
  - Truncate stdout/stderr to first 3 non-empty lines unless the hook failed — failed hooks show the full traceback.
  - Sort by start time ascending.
- **Field diff:**
  - Compare before-snapshot and after-snapshot on `(schema_id, row_index)` keys.
  - Show only deltas. Identical fields are silent.
  - Value diff: `"<before>" → "<after>"`. Trim to 40 chars per side; suffix with `…` on truncation.
  - Message added: `+"<text>"`. Message removed: `-"<text>"`.
  - automation_blocker added/removed: `(added: "<text>")` / `(removed: "<text>")`.
  - Classification tags (optional, suffix in grey-ish): `(numeric_format)` when before/after parse to the same number; `(whitespace)` when only whitespace differs. **These are informational only — every byte-difference is still shown.**
- **No verdict line.** This skill does not classify pass/fail. The user reads the diff and judges.

### Examples

**Toggle, all green:**

```
Annotation 12345 (queue "Test - DE", env "uat")
Trigger: toggle  (postponed → to_review in 4.1s)

Hook logs (2 hooks fired):
  ✓ validate_invoice (function, 312ms)
      [INFO] applied EU VAT check, 0 issues
  ✓ format_dates (function, 88ms)

Changed fields (2):
  invoice_date                "2026-01-15" → "15.01.2026"   (locale_format)
  invoice_total_vat           "21,00" → "21.00"             (numeric_format)
```

**Confirm with an export failure:**

```
Annotation 12345 (queue "Test - DE", env "uat")
Trigger: confirm  (to_review → confirmed in 8.2s)

Hook logs (3 hooks fired):
  ✓ validate_invoice (function, 412ms)
  ✓ post_to_coupa (webhook, 1.2s)
      HTTP 200, response_id=cpa_889
  ✗ notify_team (function, 60ms)
      KeyError: 'recipient_email'
      File "notify_team.py", line 14, in run
          msg = build_email(field.recipient_email)
        File "notify_team.py", line 22, in build_email
          return f"To: {addr}"

Changed fields (3):
  status_log                  +"sent to coupa"
  automation_blocker          (removed: "missing VAT")
  recipient_email             ""      → "ap@acme.de"
```
````

- [ ] **Step 2: Replace the state management section**

Replace the `## State Management\n\n(cache file behavior — written in Task 3)` block with:

````markdown
## State Management

The skill persists exactly one piece of state: the last-used annotation, env, and trigger mode. Location: `.rossum-verify/last.json` in the customer's project root.

### Cache schema

```json
{
  "annotation_id": 12345,
  "env_name": "uat",
  "trigger_mode": "toggle",
  "ts": "2026-05-20T10:30:00Z"
}
```

### Cache rules

- **Write:** every successful run (any trigger mode) overwrites `.rossum-verify/last.json`. Failed runs (annotation 404, hook chain timeout, etc.) do **not** overwrite — the previous good entry stays usable.
- **Read:** on invocation with no annotation argument. If the cache file is missing, malformed, or older than 7 days → ignore it and ask the user.
- **Surface:** when reusing a cached annotation, always tell the user which one: *"Re-using annotation 12345 on env `uat` from last run — override?"* Proceed silently after a brief pause if no override comes.

### Gitignore

The customer's project must ignore `.rossum-verify/`. On first run, if the directory does not exist, create it and check whether `.gitignore` in the project root contains `.rossum-verify/`. If not, append the entry — but **only after telling the user**: *"Adding `.rossum-verify/` to `.gitignore` so the cache stays out of git."*

### Cache scope

One cache per project root. The skill assumes a 1:1 mapping between project directory and Rossum env+queue. If a customer juggles multiple envs from the same directory, they can override per-invocation via `--env=<name>` and the cache will follow.
````

- [ ] **Step 3: Verify output and state sections present**

Run: `grep -A1 "## Output Format" plugins/rossum-sa/skills/verify-change/SKILL.md | head -5`
Expected: not the placeholder text.

Run: `grep -A1 "## State Management" plugins/rossum-sa/skills/verify-change/SKILL.md | head -5`
Expected: not the placeholder text.

- [ ] **Step 4: Commit**

```bash
git add plugins/rossum-sa/skills/verify-change/SKILL.md
git commit -m "Add output format and state management to verify-change skill"
```

---

## Task 4: Gotchas and cross-references

**Files:**
- Modify: `plugins/rossum-sa/skills/verify-change/SKILL.md` (replace `## Common Errors and Gotchas` and `## When to Use Something Else` placeholders)

- [ ] **Step 1: Replace the gotchas section**

Replace the `## Common Errors and Gotchas\n\n(troubleshooting list — written in Task 4)` block with:

````markdown
## Common Errors and Gotchas

- **Annotation is in `deleted` status after a `Duplicate Handling` hook ran.** Customer queues with a dedup hook on `annotation_content.initialize` auto-delete re-uploads. This skill never re-uploads, so it should be rare here — but if the cached annotation has been deleted by an external action, the skill must detect (`status: deleted`) and ask whether to restore (`PATCH status → to_review`) or pick a different annotation.
- **Hooks fire only on status transitions to `to_review`.** A PATCH to `content/<dp_id>` in `patch` mode triggers `annotation_content.user_update`, which is a different event. If your hook listens only to `started`, `patch` mode will not fire it. Use `toggle` instead.
- **`rossum_list_hook_logs` is asynchronous.** A log entry may not appear for up to ~2 seconds after the hook completes. If logs look short or empty immediately after the trigger, wait 2s and refetch.
- **`confirm` against a queue with `validate` hooks may block the transition.** If a `validate` hook returns blocking messages, the status stays at `to_review` even though the API call returned 200. Detect this: if after `confirm` the status is still `to_review` and the hook log shows a `validate`-type hook with blocking messages, report it explicitly rather than calling the run a timeout.
- **`patch` mode and formula-typed fields don't mix.** A field with `ui_configuration.type=formula` rejects PATCH with HTTP 400 (*"The computed datapoint X can only be updated from UI."*). Resolve the target's `ui_configuration.type` from `rossum_get_annotation_content` before sending; error out with a clear message if it's a formula field.
- **The hook chain may modify fields the diff would otherwise miss.** Capture the after-snapshot promptly. Do **not** open the annotation in the Rossum UI between trigger and after-snapshot — opening it fires another `annotation_content.started`, which re-runs the chain and pollutes the diff.
- **`prd2 push` may fail silently from the user's perspective if the env is wrong.** When the optional push step is run, capture `prd2`'s stdout/stderr and surface any errors before proceeding to step 3. Do not assume push succeeded just because the command returned 0 — read the output.
````

- [ ] **Step 2: Replace the cross-references section**

Replace the `## When to Use Something Else\n\n(cross-references — written in Task 4)` block with:

````markdown
## When to Use Something Else

| Need | Skill |
|------|-------|
| "I just changed a hook — did it land and what does it do on this one doc?" | **this skill** (`verify-change`) |
| "I just finished an upgrade — does the whole implementation still behave the same across a corpus?" | [`test-behavioral-equivalence`](../test-behavioral-equivalence/SKILL.md) |
| "How do I push local hook code to UAT?" | [`prd-reference`](../prd-reference/SKILL.md) |
| "What events does my hook need to subscribe to?" | [`txscript-reference`](../txscript-reference/SKILL.md) |
| "How does Rossum's annotation lifecycle work?" | [`rossum-reference`](../rossum-reference/SKILL.md) |

If the user describes a corpus, two-environment comparison, regression testing across queues, or "before I promote this", redirect to `test-behavioral-equivalence` and stop here. This skill is for **one annotation, one hook change, observe**.
````

- [ ] **Step 3: Final line-count check**

Run: `wc -l plugins/rossum-sa/skills/verify-change/SKILL.md`
Expected: 180-280 lines (target was 150-250; some slack OK).

Run: `grep -c "(written in Task" plugins/rossum-sa/skills/verify-change/SKILL.md`
Expected: 0 — no leftover placeholder stubs.

- [ ] **Step 4: Commit**

```bash
git add plugins/rossum-sa/skills/verify-change/SKILL.md
git commit -m "Add gotchas and cross-references to verify-change skill"
```

---

## Task 5: README, .gitignore, version bump

**Files:**
- Modify: `README.md` (line ~62, rossum-sa skill table)
- Modify: `.gitignore`
- Modify: `plugins/rossum-sa/.claude-plugin/plugin.json` (version field)
- Modify: `plugins/rossum-sa/mcp-servers/rossum-api/server.py` (line 2292, serverInfo)

- [ ] **Step 1: Determine the version number to use**

Run: `gh pr view 21 --json state,merged --jq '{state, merged}'`

- If `merged: true`: bump to `0.14.0`.
- If `merged: false`: bump to `0.13.0` and add a note to the eventual PR description that PR #21 must merge first (or be rebased on top of this work).

Use the resolved value as `<NEW_VERSION>` in subsequent steps.

- [ ] **Step 2: Add the skill row to README.md**

Locate the rossum-sa skill table (it ends around line 62 with `dead-code`). Add this row immediately above the `dead-code` row to keep alphabetical-ish ordering broken by topic — actually, append it at the bottom of the table to match insertion order of existing entries:

```markdown
| `/rossum-sa:verify-change [annotation-id]` | Inner dev loop: trigger one annotation through a deployed hook, show hook logs and changed fields. Use while iterating on a hook. |
```

Also add a row for `test-behavioral-equivalence` **only if** PR #21 has not merged yet AND that row is not already there. (Check with `grep test-behavioral-equivalence README.md`.) If the row is missing and PR #21 is still open, defer this README addition to PR #21 — do not add it here.

- [ ] **Step 3: Add `.rossum-verify/` to `.gitignore`**

Append to `.gitignore`:

```
.rossum-verify/
```

The file should now read:

```
.idea/
__pycache__/
.rossum-verify/
```

- [ ] **Step 4: Bump `version` in `plugin.json`**

Edit `plugins/rossum-sa/.claude-plugin/plugin.json`:

```diff
-  "version": "0.12.0",
+  "version": "<NEW_VERSION>",
```

- [ ] **Step 5: Bump `serverInfo.version` in `server.py` to match**

Edit `plugins/rossum-sa/mcp-servers/rossum-api/server.py` line 2292:

```diff
-                    "serverInfo": {"name": "rossum-api", "version": "0.12.0"},
+                    "serverInfo": {"name": "rossum-api", "version": "<NEW_VERSION>"},
```

- [ ] **Step 6: Verify the two versions match**

Run: `grep -E '"version"' plugins/rossum-sa/.claude-plugin/plugin.json && grep -n "serverInfo" plugins/rossum-sa/mcp-servers/rossum-api/server.py | head -1`
Expected: both show the same `<NEW_VERSION>`.

- [ ] **Step 7: Commit**

```bash
git add README.md .gitignore plugins/rossum-sa/.claude-plugin/plugin.json plugins/rossum-sa/mcp-servers/rossum-api/server.py
git commit -m "Wire verify-change into README, gitignore, and version bump"
```

---

## Task 6: Live smoke test against a real annotation

**Files:** (none — this is a manual verification task)

**Prerequisites:** Access to a Rossum sandbox/UAT env with at least one annotation in a `Test`/`Sandbox`/`UAT`/`Dev` queue that has at least one hook attached. The customer needs to provide an `MCP_ROSSUM_TOKEN` or pass the connection string via `rossum_set_token`.

- [ ] **Step 1: Start a fresh Claude Code session in this plugin repo and authenticate**

In the new session: `rossum_set_token` with a sandbox env credential. Confirm with `rossum_whoami`.

- [ ] **Step 2: Test the toggle mode (default)**

Prompt Claude: *"Run /rossum-sa:verify-change with annotation ID `<test-annotation-id>` on env `<env>`."*

Verify the skill:

1. Asks for confirmation before the first `PATCH /annotations/<id>` status → `postponed`.
2. Asks for confirmation before the second `PATCH /annotations/<id>` status → `to_review`.
3. Waits for hooks to settle (or hits timeout cleanly).
4. Fetches hook logs and after-snapshot.
5. Renders the inline report with both sections (`Hook logs (N hooks fired)` and `Changed fields (N)`).
6. Writes `.rossum-verify/last.json` with the right contents.

Run: `cat .rossum-verify/last.json`
Expected: JSON with the annotation_id, env_name, trigger_mode=`toggle`, and a recent ts.

- [ ] **Step 3: Test the cache reuse**

Prompt Claude: *"Run /rossum-sa:verify-change."* (no args)

Verify the skill:

1. Surfaces the cached annotation (*"Re-using annotation X from last run — override?"*).
2. Proceeds with the same flow as Step 2.

- [ ] **Step 4: Test patch mode**

Pick a non-formula, non-required schema_id from the annotation (read with `rossum_get_annotation_content` first). Prompt Claude: *"Run /rossum-sa:verify-change --trigger=patch --patch=<schema_id>=<new-value>."*

Verify the skill:

1. Confirms before the `PATCH /annotations/<id>/content/<dp_id>` call.
2. Fetches logs filtered to the post-trigger time window.
3. Shows the field diff including the patched field.

- [ ] **Step 5: Test confirm mode (with care)**

Pick a queue that matches the `test|sandbox|uat|dev` naming convention. Prompt Claude: *"Run /rossum-sa:verify-change --trigger=confirm."*

Verify the skill:

1. Lists all hooks that will fire on `confirmed`.
2. Calls out export-shaped hooks explicitly.
3. Asks the explicit confirmation question about export safety.
4. After running, detects that the annotation is now at `confirmed` and offers to PATCH back to `to_review` on a follow-up confirm run.

**If the queue name does not match the safe pattern**, verify the skill refuses with the override prompt.

- [ ] **Step 6: Test the "no annotation, no cache" path**

Delete `.rossum-verify/last.json`. Prompt Claude: *"Run /rossum-sa:verify-change."* (no args)

Verify the skill asks for an annotation ID or offers to search for a candidate.

- [ ] **Step 7: Document the test results**

Write a short summary in the eventual PR description: which trigger modes were tested, which env, any issues found. If any step's behavior diverged from the plan, capture the divergence and either fix the SKILL.md or note it in the PR description for review.

- [ ] **Step 8: Clean up the test cache**

Run: `rm -rf .rossum-verify/` (the directory was created in this repo during testing — it should not be committed).

Run: `git status`
Expected: no untracked `.rossum-verify/` directory (since `.gitignore` was already updated).

---

## Self-Review

Spec coverage check (against `docs/superpowers/specs/2026-05-20-verify-change-skill-design.md`):

- **Goal:** ✓ Task 1-4 produce the SKILL.md; Task 6 verifies it works.
- **Scope (in/out):** ✓ Workflow narrative in Task 2 mirrors the spec's 7 steps. Cross-references in Task 4 carry the "out-of-scope" delegation.
- **Canonical workflow:** ✓ Task 2 covers steps 1-7.
- **Inputs:** ✓ Front-matter `argument-hint` in Task 1 captures all four inputs.
- **Persisted state:** ✓ Task 3 documents the cache schema and rules.
- **Output format:** ✓ Task 3 includes the template and both examples from the spec.
- **Safety gates:** ✓ Task 1 hard-gate block covers all three write ops; Task 2 confirm-mode safety detail extends gate #2.
- **File structure:** ✓ Task 1 creates the directory; the plan never references any bundled script.
- **Tooling:** ✓ The MCP tools listed in the spec are exactly the ones used in Task 2's workflow.
- **Cross-skill relationship:** ✓ Task 4 cross-references table.
- **Open questions:** None blocking — all called out as in-skill heuristics.
- **Not-doing list:** ✓ The plan does not introduce batch mode, diff-against, history, or test-skill coupling.

Type consistency: cache schema uses `annotation_id` and `env_name` and `trigger_mode` and `ts` consistently across spec, Task 3, and Task 6 step 2.

Placeholder scan: no TBD, no "add error handling", no "similar to Task N" — every step has its exact content. Version number is templated as `<NEW_VERSION>` and resolved at execution time via a real check; this is acceptable because the actual value depends on PR #21's merge state, which is not knowable at plan-write time.
