# init-claude-md skill — Design

**Date:** 2026-06-08
**Status:** Approved (design phase complete, pending implementation)
**Plugin:** `rossum-sa`

## Problem

When a Rossum SA pulls a prd2 project locally and opens it in Claude Code, the assistant has no idea this is a Rossum implementation. It must rediscover the conventions every session: edit `.py` not the JSON `code` field, never call write APIs without approval, what `prd2 push` does, where queues/hooks/formulas live, which integration target is in play.

Anthropic's docs recommend `CLAUDE.md` as the project-memory entry point — it's auto-loaded at the start of each conversation. Today, pulled prd2 projects don't have one.

## Goal

Provide an invocable skill that inspects a pulled prd2 project and writes a project-specific `CLAUDE.md` so future Claude Code sessions in that directory immediately know:

1. This is a Rossum.ai prd2 implementation project.
2. The hard rules: edit `.py` not JSON, never push/deploy without approval, MDH targets are enums.
3. The actual shape of *this* project: environments, queue count, hooks, integration target.
4. Which `rossum-sa:*` skills to reach for.

## Non-goals

- Generating a generic Rossum template (rejected during brainstorming — output must be project-specific from real config).
- Bundling CLAUDE.md best practices as a separate autoloaded reference pack (rejected — keep the knowledge inside this skill).
- Replacing or wrapping Claude Code's built-in `/init` (different scope: that scans codebases generally; this is Rossum-aware).
- Making remote API calls. Inspection is local-only.
- Auto-running on `prd2 pull` (out of scope; user invokes the skill explicitly).

## Skill identity

| Field | Value |
|---|---|
| **Name** | `init-claude-md` |
| **Path** | `plugins/rossum-sa/skills/init-claude-md/SKILL.md` |
| **Invocation** | `/rossum-sa:init-claude-md` |
| **User-invocable** | `true` |
| **Description** | "Generate a CLAUDE.md for a pulled Rossum prd2 project so future Claude Code sessions immediately understand this is a Rossum implementation. Inspects queues, hooks, schemas, formulas, and integration targets to write a project-specific memory file." |
| **Triggers** | "init claude md", "set up project context for Claude", "onboard this Rossum project to Claude Code", "generate CLAUDE.md", "claude.md for this project" |

## Inspection phase (discovery)

All discovery is local file reading. No remote API calls, no `prd2` invocations.

| Source | Facts extracted |
|---|---|
| `prd_config.yaml` (project root) | Project name, environment names, source/target URLs |
| `<env>/workspaces/Workspace_*/queues/Queue_*/queue.json` | Queue count, queue names, region per queue |
| `<env>/workspaces/.../queues/.../schema.json` | Schema field counts, fields with `ui_configuration.type = "data"` (MDH targets), formula fields, enum fields |
| `<env>/hooks/*.json` | Hook names, hook type (function / business_rules / export / extension), Python runtime declared, `run_after` chain |
| `<env>/hooks/<hook>/*.py` | Detect MDH config presence (`MatchConfig`, query cascades), SFI patterns, Request Processor (export pipeline v2) presence |
| Integration heuristics | Coupa Integration Baseline (CIB) markers in hook names / formula references; SAP IDOC patterns; SFTP export configs; request-processor `call_api` blocks |
| `prd2 --version` (optional, best-effort) | prd2 CLI version if available locally |

### Detection rules for integration target

Run in this order; first match wins, otherwise mark as "unknown / custom":

1. **Coupa (CIB)** — hook or formula references match the CIB baseline (look for hook names like `coupa_*`, `cib_*`, or known formula identifiers from `coupa-baseline-reference`).
2. **SAP** — hook code mentions IDOC, `INVOIC02`, or SAP middleware patterns.
3. **SFTP** — export pipeline includes `file-storage-export` or SFTP destination.
4. **Generic REST** — export pipeline has `call_api` blocks without the above markers.
5. **Unknown** — none of the above; write "integration target not auto-detected — fill in" in the project overview.

## Generation phase (CLAUDE.md output)

The skill writes a `CLAUDE.md` to the project root with these sections, in order. Numbers correspond to the 10-section best-practices structure adapted for Rossum.

### 1. Project Overview
One paragraph describing what this implementation does, derived from inspection. Format:
> "This is a Rossum.ai prd2 implementation project for `<env-name>` environment(s). It contains `<N>` queues across `<M>` workspaces and `<K>` hooks. Primary integration target: `<detected target>`. Optimizes for: accurate document extraction, reliable export to `<target>`, and maintainable hook code."

### 2. Tech Stack
Explicit list:
- Rossum.ai platform
- `prd2` v2 (project deployment CLI)
- Python 3.12 (function hooks via TxScript API)
- MongoDB / Atlas Search (Master Data Hub, Data Storage)
- JSON schema configs (queues, hooks, schemas)

**Do not use:**
- `prd` v1 (deprecated; use `prd2`)
- Editing the `code` field in hook JSON (use `.py` files)
- Editing the `formula` property in `schema.json` (use `formula.py` files)
- Hardcoded credentials in code

### 3. Architecture
Real directory listing of the pulled project, generated dynamically — environments, workspaces, queues, schemas, hooks. Plus "where new things go" rules:
- New hooks → `<env>/hooks/<hook-name>/`
- New formulas → `<env>/workspaces/.../queues/.../formulas/<field>.py`
- Deploy files → project root or `deploy/`

### 4. Coding Conventions
Hard rules pulled from the existing skill safety guidance:
- Edit `.py` files; never edit `code` field in hook JSON or `formula` property in `schema.json` — `prd2 push` syncs `.py` into JSON.
- MDH-populated fields must be `type: "enum"` with `ui_configuration.type: "data"`. A string field silently drops MDH values.
- Use TxScript API (`TxScript`, `field`, automation blockers) for function hooks; see `rossum-sa:txscript-reference`.
- Never call write APIs (`rossum_create_*`, `rossum_patch_*`, `rossum_delete_*`, `data_storage_*` writes, `prd2 push`, `prd2 deploy`) without explicit user approval.

### 5. Commands
Real commands using the project's actual paths:
- Pull current state: `prd2 pull`
- Push staged changes (non-interactive): `prd2 push --indexed-only -f`
- Deploy to another environment: `prd2 deploy -f <deploy-file>.yaml`
- Purge: `prd2 purge`

### 6. Safety Rules
Copy the SA-grade confirmation gate:
- Read-only operations are fine without confirmation (`prd2 pull`, all `rossum_list_*` / `rossum_get_*`, `data_storage_find` / `_aggregate`, `whoami`).
- All writes require explicit user "yes" before execution.
- Never batch multiple write operations into one approval.

### 7. File Placement Rules
- Reuse existing hooks before creating new ones.
- One formula per file under `formulas/`.
- Keep schema field IDs stable across pushes (changing them breaks annotations).
- Deploy files in `deploy/` or root, named `deploy_<source>_to_<target>.yaml`.

### 8. Testing & Quality
- After any change intended to be behavior-preserving: run `rossum-sa:test-behavioral-equivalence`.
- For audits: `rossum-sa:analyze`.
- For pruning unused configs: `rossum-sa:dead-code`.
- Verify naming conventions: `rossum-sa:evaluate-namings`.

### 9. Recommended Skills
Always-relevant pointers (adjust based on detection):
- `rossum-sa:rossum-reference` — platform reference
- `rossum-sa:prd-reference` — prd2 CLI
- `rossum-sa:txscript-reference` — function hook API
- `rossum-sa:mdh-reference` (if MDH detected)
- `rossum-sa:export-pipeline-reference` (if Request Processor detected)
- `rossum-sa:coupa-baseline-reference` (if Coupa detected)
- `rossum-sa:sap-reference` (if SAP detected)
- `rossum-sa:sfi-reference` (if SFI detected)

### 10. Project-Specific Notes
Empty section with a header and a "Add project-specific notes here" placeholder. This is the human-curated part the user maintains.

## Conflict handling

When the skill runs, it checks for an existing `CLAUDE.md`:

| Existing state | Behavior |
|---|---|
| No `CLAUDE.md` | Write the generated file. Done. |
| `CLAUDE.md` exists, no Rossum context | Show a diff of the proposed additions and ask: overwrite / merge into a `## Rossum Implementation Context` appendix / abort. Default suggestion: merge. |
| `CLAUDE.md` exists and already contains a Rossum-context section (detect via a marker comment `<!-- rossum-sa:init-claude-md -->`) | Show what changed in the project since last init (e.g. new hooks/queues) and offer to refresh only the auto-generated section. Preserve hand-written sections verbatim. |

Never silently overwrite. The skill is idempotent only inside the marked auto-generated section.

## Idempotency marker

The auto-generated portion is bracketed with HTML-comment markers:

```markdown
<!-- BEGIN rossum-sa:init-claude-md auto-generated -->
... auto-generated sections 1–9 ...
<!-- END rossum-sa:init-claude-md auto-generated -->

## 10. Project-Specific Notes
<!-- Human-edited below — auto-generation will not touch this -->
```

The skill only rewrites content between the BEGIN/END markers on re-runs.

## Skill file structure

```
plugins/rossum-sa/skills/init-claude-md/
├── SKILL.md          # Trigger, workflow, inline best-practices knowledge
└── template.md       # The CLAUDE.md template with {{placeholders}} (optional, if SKILL.md grows long)
```

The 10-section structure + best-practices rationale (from the source article the user shared) lives in `SKILL.md`. If it grows beyond ~300 lines, split the template into `template.md`.

## Cross-references

After this skill exists, update:
- `plugins/rossum-sa/skills/implement/SKILL.md` Phase 1 — add a step "Generate project CLAUDE.md via `/rossum-sa:init-claude-md` after `prd2 pull`."
- `README.md` (project-level) — add `init-claude-md` to the invocable skills list.
- `README-internal.md` — note the new skill.

## Out of scope (explicitly)

- Auto-running on `prd2 pull` (could be a follow-up via prd2 plugin hook, but not now).
- Modifying `prd2` itself.
- Generating a per-queue or per-hook CLAUDE.md (just one at project root).
- Generating `.cursorrules` / `GEMINI.md` / `AGENTS.md` variants (could follow same pattern later).

## Success criteria

1. Running `/rossum-sa:init-claude-md` in a pulled prd2 project produces a `CLAUDE.md` at the root within seconds.
2. The generated file mentions the real environment name, real queue count, real hook names, and the detected integration target.
3. A fresh Claude Code session in that directory, with no other context, correctly recognizes "this is a Rossum prd2 project" and applies the safety rules (edit `.py` not JSON, confirm before pushes).
4. Re-running the skill in a project that already has a `CLAUDE.md` does not destroy hand-written content.
