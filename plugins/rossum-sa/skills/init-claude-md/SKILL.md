---
name: init-claude-md
description: Generate a project-specific CLAUDE.md for a pulled Rossum prd2 project so future Claude Code sessions immediately recognize the directory as a Rossum implementation and apply the right safety rules. Inspects environments, queues, hooks, schemas, and integration targets locally — no remote API calls. Triggers on "init claude md", "set up project context for Claude", "onboard this Rossum project to Claude Code", "generate CLAUDE.md", "claude.md for this project".
argument-hint: [path-to-prd2-project]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Initialize CLAUDE.md for a Rossum prd2 Project

> Path or context: $ARGUMENTS

## What this skill does

Generates a `CLAUDE.md` at the root of a pulled prd2 project so future Claude Code sessions know it's a Rossum implementation and apply the right safety rules (edit `.py` not JSON, never push/deploy without approval, MDH targets are enums).

The auto-generated portion is wrapped in `<!-- BEGIN/END rossum-sa:init-claude-md auto-generated -->` markers. Re-running this skill rewrites only the marked block; the "Project-Specific Notes" section below the markers is preserved.

## Why a project-specific CLAUDE.md

Anthropic recommends `CLAUDE.md` for project memory — it is auto-loaded at the start of each Claude Code session. Without one, every session has to rediscover Rossum conventions (edit `.py` not JSON, prd2 commands, safety rules, where queues/hooks/formulas live). A pre-filled file removes that friction.

Best-practices sections (from the source article that motivated this skill):
1. Project Overview — what the implementation is, what it optimizes for
2. Tech Stack — explicit list of what's in use and what to avoid
3. Architecture — real directory layout, where new things go
4. Coding Conventions — hard rules (not vague "write clean code")
5. Commands — exact commands, not paraphrases
6. Safety Rules — confirmation gates for destructive ops
7. File Placement Rules — when to create new files vs. edit existing
8. Testing & Quality — how "done" is validated
9. Recommended Skills — pointers to relevant `rossum-sa:*` skills
10. Project-Specific Notes — human-curated area

## Workflow

### Step 1: Resolve the project directory

If `$ARGUMENTS` is a path, use it. Otherwise use the current working directory. Confirm it looks like a prd2 project:

- Required: `prd_config.yaml` at the root.
- Strongly suggested: at least one environment subdirectory listed in that file with `workspaces/` underneath.

If `prd_config.yaml` is missing, stop and tell the user: "This does not look like a prd2 project (no `prd_config.yaml` found). Run `prd2 pull` first or pass the project path explicitly."

### Step 2: Run the inspector

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/init-claude-md/inspect.py <project_dir>
```

Capture stdout as JSON. The inspector returns:

```
{
  "project_name": "...",
  "environments": ["dev-env", "prod-env"],
  "workspace_count": N,
  "queue_count": M,
  "queues": [{"name": "...", "workspace": "...", "environment": "...", "schema_field_count": K}, ...],
  "hook_count": H,
  "hooks": [{"name": "...", "type": "...", "runtime": "...", "environment": "...", "queue_count": Q}, ...],
  "integration_target": "Coupa" | "SAP" | "SFI" | "SFTP" | "Generic REST" | "unknown"
}
```

### Step 3: Render the template

Read `${CLAUDE_PLUGIN_ROOT}/skills/init-claude-md/template.md`. Replace placeholders:

| Placeholder | Value source |
|---|---|
| `{{environments_joined}}` | `", ".join(facts["environments"])` |
| `{{queue_count}}` | `facts["queue_count"]` |
| `{{workspace_count}}` | `facts["workspace_count"]` |
| `{{hook_count}}` | `facts["hook_count"]` |
| `{{integration_target}}` | `facts["integration_target"]` (if `"unknown"`, append a "fill in — integration target not auto-detected" note) |
| `{{tree_listing}}` | Output of `tree -L 4 -I '__pycache__|node_modules' <project_dir>` truncated to ~40 lines. If `tree` is not installed, fall back to `find <project_dir> -maxdepth 4 -type d \| sort \| head -40`. |
| `{{conditional_skills_block}}` | See Step 4 |

### Step 4: Build the conditional skills block

Always include the three baseline references already mentioned in section 9 above the placeholder. Then, based on the inspector's signals, append:

- If `integration_target == "Coupa"` → `- \`rossum-sa:coupa-baseline-reference\` — Coupa Integration Baseline (CIB)`
- If `integration_target == "SAP"` → `- \`rossum-sa:sap-reference\` — SAP integration patterns`
- If `integration_target == "SFI"` → `- \`rossum-sa:sfi-reference\` — Structured Formats Import`
- If any hook name matches `/mdh|master.data|matching/i` OR any hook `.py` file contains `MatchConfig` → `- \`rossum-sa:mdh-reference\` — Master Data Hub matching`
- If any hook `.py` or JSON contains `"call_api"` → `- \`rossum-sa:export-pipeline-reference\` — Request Processor`

If no conditional skills apply, write a single line: `_No conditional references — none of Coupa, SAP, SFI, MDH, or Request Processor signals were detected._`

### Step 5: Write or merge the file

Check whether `<project_dir>/CLAUDE.md` exists:

| Case | Action |
|---|---|
| File does not exist | Write the rendered template. Confirm with the user the path of the written file. |
| File exists and contains both `<!-- BEGIN rossum-sa:init-claude-md auto-generated -->` and the matching END marker | Replace only the content between the markers. Preserve everything else verbatim. |
| File exists, no markers | Show the user a diff of the proposed additions (the full template). Ask: overwrite / append the auto-generated block to the end / abort. Default suggestion: **append**. Wait for explicit choice. |

Never silently overwrite hand-written content.

### Step 6: Show the result and recommend next steps

After writing, print:

- Path written
- One-paragraph summary of what landed in CLAUDE.md (env names, queue count, hook count, detected integration target)
- Suggested follow-up: "Open `CLAUDE.md` and fill in section 10 (Project-Specific Notes) with anything unique to this implementation. Then commit the file."

Do **not** auto-commit. The user decides when to commit a memory file.

## Notes

- The inspector is local-only — no Rossum API calls, no `prd2` invocations.
- The skill itself only reads/writes local files. No network.
- Re-running on a project that already has a generated CLAUDE.md only refreshes the marked section; user notes below stay intact.
- If the integration target comes back `unknown`, the generated file says so explicitly — review and fill in if you know the answer.
