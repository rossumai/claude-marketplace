# `verify-change` skill — design

**Date:** 2026-05-20
**Status:** Approved, ready for implementation planning
**Author:** vaclavrut (with Claude)

## Goal

Add a lightweight skill to the `rossum-sa` plugin that supports the inner dev loop for Rossum hook work: edit a hook locally → push → trigger one annotation through it → observe hook logs and changed field values → iterate.

Complements (does not replace) `test-behavioral-equivalence`, which is the heavy pre-promote regression check.

## Why a separate skill

`test-behavioral-equivalence` is a 530-line skill with 5 bundled Python scripts, prescribes two coexisting environments, mandates a corpus, runs Phase 0 hard gates for engine and MDH parity, and produces a structured report. That's the right shape for verifying behavior preservation across an upgrade.

It is the wrong shape for "I just edited `validate_invoice.py`, did the change land and what does the hook say now." A dev iterating on one hook against one document should not need a corpus, a second environment, snapshot directories, or a written report. The two workflows deserve separate skills with names that make their scope obvious.

## Scope

### In scope

- Pick a target annotation (passed, cached, or proposed)
- Optionally push local hook changes via `prd2 push -io` (only when user opts in to solo-iterate mode)
- Capture a before-snapshot of annotation content + status + messages + blockers
- Trigger a hook re-run via one of three modes (see Trigger modes below)
- Wait for the hook chain to settle, with timeout
- Fetch hook logs filtered to this annotation + time window
- Capture an after-snapshot and show the diff (changed fields, messages, blockers only)
- Report inline in chat — no file written

### Out of scope (delegated to `test-behavioral-equivalence`)

- Corpus, multi-annotation runs
- Two-environment source-vs-target comparison
- PATCH-based prod-state synthesis on a target annotation
- Diff classification ladder, cross-corpus clustering
- Written report file
- Engine / MDH dataset / Atlas Search parity hard gates
- Assertion framework (no `--expect`)
- Pre-flight "is my code actually deployed" check (server vs. local diff)
- Re-upload of the document as a documented mode — escape hatch only, on explicit user request

## Canonical workflow

One invocation = one cycle.

1. **Pick target annotation.** Resolution order:
   - Argument provided → use it; write to `.rossum-verify/last.json`.
   - Cache exists → use last ID, surface it ("Re-using annotation 12345 from last run — override?").
   - Neither → ask the user: paste an ID, or let Claude search (e.g., recent annotations in the queue holding the hook being edited via `rossum_search_annotations`).
2. **Optional push.** Skipped by default — the user usually pushes themselves. When the user explicitly asks Claude to iterate solo, or when uncommitted local `.py` edits exist for hooks in scope and the user opts in, run `prd2 push <env> -io <files>` with explicit confirmation showing the file list.
3. **Before-snapshot.** `rossum_get_annotation` + `rossum_get_annotation_content`. Capture status, content (value, message, automation_blocker per datapoint), and current automation_blocker on the annotation itself.
4. **Trigger.** One of three modes:

   | Mode | API sequence | Event fired | Use case |
   |------|--------------|-------------|----------|
   | `toggle` *(default)* | PATCH status → `postponed` → PATCH status → `to_review` | `annotation_content.started` | Most validation/extraction hooks |
   | `confirm` | `POST /annotations/<id>/confirm` | `annotation_content.confirmed` + downstream export hooks | Approval-workflow hooks, export hooks, post-confirm validation |
   | `patch <schema_id>=<value>` | PATCH `/content/<dp_id>` with new value | `annotation_content.user_update` | Field-level format/validation hooks |

   Each write op passes through the write-confirmation gate (per the plugin's safety rule). `confirm` mode gets a higher-friction gate that explicitly lists every hook in the chain that would fire on `confirmed`, with export-shaped hooks (webhook to external endpoint, SFTP, email) called out by name.

5. **State reset for next iteration.**
   - After `toggle`: annotation is back at `to_review` — ready.
   - After `patch`: annotation is still at `to_review` — ready.
   - After `confirm`: annotation is at `confirmed` (or `exported`). On the next invocation in `confirm` mode, the skill detects this and offers to PATCH back to `to_review` first (gated). This avoids an obscure "nothing happened" failure where the second confirm is a no-op.
6. **Wait for hooks.** Poll the annotation until status settles and no pending hook events remain, or hit the timeout. Default timeout 30s; overridable.
7. **After-snapshot + logs.** Re-fetch `rossum_get_annotation_content`. Fetch `rossum_list_hook_logs` filtered to this annotation + a time window starting just before the trigger.
8. **Report inline.** Two sections (hook logs, field diff). Format below. No file written.

## Inputs

Conversational, no rigid CLI:

- **Annotation ID** — optional. Falls back to `.rossum-verify/last.json`, then asks/searches.
- **Env name** — optional. Defaults to the project's target env from `prd2` config or last-used.
- **Trigger mode** — `toggle` (default), `confirm`, or `patch <schema_id>=<value>`.
- **Schema-id filter** — optional. Restricts the field-diff section to specific schema_ids.
- **Timeout** — optional. Default 30s for hook settle.

## Persisted state

`.rossum-verify/last.json` in project root. Gitignored. Holds:

```json
{
  "annotation_id": 12345,
  "env_name": "uat",
  "trigger_mode": "toggle",
  "ts": "2026-05-20T10:30:00Z"
}
```

One file, ~50 bytes. No directory structure. No history.

## Output format

Inline chat report, two sections, terse:

```
Annotation 12345 (queue "Test - DE", env "uat")
Trigger: confirm  (to_review → confirmed in 8.2s)

Hook logs (3 hooks fired):
  ✓ validate_invoice (function, 412ms)
      [INFO] applied EU VAT check, 0 issues
  ✓ post_to_coupa (webhook, 1.2s)
      HTTP 200, response_id=cpa_889
  ✗ notify_team (function, 60ms)
      KeyError: 'recipient_email'
      File "notify_team.py", line 14, in run

Changed fields (4):
  invoice_total_vat           "21,00" → "21.00"      (numeric_format)
  recipient_email             ""      → "ap@acme.de"
  status_log                  +"sent to coupa"
  automation_blocker          (removed: "missing VAT")
```

Hook logs cluster by hook with a status glyph, duration, condensed stdout/stderr, and a full traceback on failure. Field diff shows only deltas (value, message, blocker). No structured pass/fail verdict — observation only.

## Safety gates

Three write ops, each with its own explicit confirmation prompt:

1. **Status toggle** — shows current → target status, asks before each PATCH.
2. **Confirm** — separate higher-friction gate that enumerates every hook in the chain that would fire on `confirmed`. Export-shaped hooks (webhook to external endpoint, SFTP, email) are called out. User must explicitly confirm exports are safe to fire here. Skill refuses if the queue looks production-shaped (heuristic: not in a name pattern matching `test|sandbox|uat|dev`).
3. **Optional `prd2 push`** — solo-iterate mode only. Gate shows the file list before executing.

Read ops (`rossum_get_annotation`, `rossum_get_annotation_content`, `rossum_list_hook_logs`) run without confirmation.

## File structure

```
plugins/rossum-sa/skills/verify-change/
└── SKILL.md          # ~150–250 lines, one file
```

No bundled Python scripts. The orchestration is short enough that the LLM owns it directly via MCP tool calls. The skill body is mostly:

- Front-matter (`name`, `description`, `argument-hint`, `allowed-tools`)
- Workflow narrative
- Safety gates
- Output template
- Cross-references to `prd-reference`, `rossum-reference`, `txscript-reference`

## Tooling

MCP tools used:

- `rossum_get_annotation` — status, queue, basic fields (read)
- `rossum_get_annotation_content` — full content tree for snapshots (read)
- `rossum_patch_annotation` — status toggle, content patches (write — gated)
- `rossum_list_hook_logs` — filtered by annotation + time window (read)
- `rossum_search_annotations` — for the "let Claude propose a target" path (read)
- `rossum_get_queue` — queue name and chain for the confirm-gate hook enumeration (read)
- `rossum_list_hooks` / `rossum_get_hook` — hook details for the confirm-gate enumeration (read)

`Bash` is used only for the optional `prd2 push -io` step.

## Cross-skill relationship

| Need | Skill |
|------|-------|
| "I just changed a hook — did it land and what does it do on this one doc?" | `verify-change` |
| "I just finished an upgrade — does the whole implementation still behave the same across a corpus?" | `test-behavioral-equivalence` |
| "I need to push my local changes to UAT" | `prd-reference` (or invoke `verify-change` in solo mode) |

The `verify-change` skill description should explicitly point users at `test-behavioral-equivalence` when they describe corpus-or-comparison-shaped needs, and vice versa.

## Open questions

None blocking. Implementation-time decisions:

- Exact format of the inline report (above is illustrative; final formatting may tighten further).
- Default timeout value (30s is a starting guess; revisit after first real runs).
- Heuristic for "production-shaped queue" in the confirm-gate (name match is the cheapest first cut; may need more if customers don't follow naming conventions).

## Not doing

- A `--mode=batch` for multiple annotations. If you want multiple, use `test-behavioral-equivalence`.
- A `--diff-against=<annotation_id>` mode to compare against another annotation. Out of scope; this is single-annotation observation.
- Persisted history of past runs. The cache is one-line state, not a journal.
- Integration with the `test-behavioral-equivalence` corpus or snapshot files. Independent skills.
