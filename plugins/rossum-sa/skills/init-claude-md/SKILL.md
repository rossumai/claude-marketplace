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

If `$ARGUMENTS` is a path, use it. Otherwise use the current working directory. Pass this directory straight to the inspector (Step 2) — the inspector detects the deployment tool via its marker file and decides whether the project is supported, so don't pre-gate on specific files here. (The single "not supported" stop lives in Step 2.)

### Step 2: Run the inspector

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/init-claude-md/inspect.py <project_dir>
```

Capture stdout as JSON. Check the `tool` field first:

- If `tool` is `"prd2"`, proceed — the JSON also contains `project_name`, `environments`, `workspace_count`, `queue_count`, `queues[]` (each with `name`, `workspace`, `environment`, `schema_field_count`, `engine`), `hook_count`, `hooks[]`, and `integration_target`.
- If `tool` is anything else (e.g. `{"tool": "unknown", "supported": false}` — also returned for a degenerate `prd_config.yaml` with no usable directory entries), **stop** and tell the user: "This doesn't look like a usable prd2 project — there's no `prd_config.yaml`, or it has no usable directory entries (each needs an `org_id` or `api_base`). `init-claude-md` currently supports prd2 projects; other deployment tools/formats aren't supported yet." Do not write a CLAUDE.md.

### Step 3: Render the template

Read `${CLAUDE_PLUGIN_ROOT}/skills/init-claude-md/template.md`. Replace placeholders:

| Placeholder | Value source |
|---|---|
| `{{project_name}}` | `facts["project_name"]` |
| `{{generated_date}}` | Today's date, `YYYY-MM-DD`. |
| `{{environments_joined}}` | `", ".join(facts["environments"])` |
| `{{environments_block}}` | One bullet per entry in `facts["directories"]`: `- <name>: org_id <org_id>, api_base <api_base>` (write `unknown` for an empty value). Final line: ``- credentials in `<env>/credentials.yaml` (gitignored, along with secrets & hook_sync_configs)`` |
| `{{queue_count}}` | `facts["queue_count"]` |
| `{{workspace_count}}` | `facts["workspace_count"]` |
| `{{hook_count}}` | `facts["hook_count"]` |
| `{{integration_target}}` | `facts["integration_target"]` (if `"unknown"`, append a "fill in — integration target not auto-detected" note) |
| `{{tree_listing}}` | Output of `tree -L 4 -I '__pycache__|node_modules' <project_dir>` truncated to ~40 lines. If `tree` is not installed, fall back to `find <project_dir> -maxdepth 4 -type d \| sort \| head -40`. |
| `{{conditional_skills_block}}` | See Step 4 |
| `{{deployment_workflow_block}}` | The full contents of `${CLAUDE_PLUGIN_ROOT}/skills/init-claude-md/fragments/<tool>.md` (today `fragments/prd2.md`), inserted verbatim — the deployment-tool-specific workflow, layout, commands, and safety rules. |

`{{tree_listing}}` is filled as before; `{{deployment_workflow_block}}` is filled by reading `fragments/<tool>.md` for the `tool` the inspector reported.

If any queue has a non-null `engine`, add an invariant to the generated CLAUDE.md immediately after the `{{deployment_workflow_block}}` substitution (i.e., between the deployment workflow section and `## Coding Conventions (Rossum platform)`):

> **Engine-bound queues:** <comma-separated `name` list of queues with non-null `engine`> use a custom extraction engine. Their schema datapoints bind to engine fields by name match — engine-extracted fields must keep `rir_field_names: []`, must not set `disable_prediction: true`, and a matching engine field must exist before a new captured datapoint is pushed. See rossum-sa:rossum-reference → Extraction Engines.

### Step 4: Build the conditional skills block

Always include the three baseline references already mentioned in the Recommended Skills section above the placeholder. Then, based on the inspector's signals, append:

- If `integration_target == "Coupa"` → `- \`rossum-sa:coupa-baseline-reference\` — Coupa Integration Baseline (CIB)`
- If `integration_target == "SAP"` → `- \`rossum-sa:sap-reference\` — SAP integration patterns`
- If `integration_target == "SFI"` → `- \`rossum-sa:sfi-reference\` — Structured Formats Import`
- If `integration_target == "SFTP"` → `- \`rossum-sa:export-pipeline-reference\` — Request Processor (file-storage-export / SFTP destination)`
- If `integration_target == "Generic REST"` → `- \`rossum-sa:export-pipeline-reference\` — Request Processor (REST export)`
- If any hook name matches `/mdh|master.data|matching/i` OR any hook `.py` file contains `MatchConfig` → `- \`rossum-sa:mdh-reference\` — Master Data Hub matching`
- If any hook `.py` or JSON contains `"call_api"` (and `integration_target` did not already trigger the Request Processor line above) → `- \`rossum-sa:export-pipeline-reference\` — Request Processor`

If no conditional skills apply, write a single line: `_No conditional references — none of Coupa, SAP, SFI, SFTP, Generic REST, MDH, or Request Processor signals were detected._`

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
- Suggested follow-up: "Open `CLAUDE.md` and fill in the Project-Specific Notes section with anything unique to this implementation. Then commit the file."

Do **not** auto-commit. The user decides when to commit a memory file.

## Notes

- The inspector is local-only — no Rossum API calls, no `prd2` invocations.
- The skill itself only reads/writes local files. No network.
- Re-running on a project that already has a generated CLAUDE.md only refreshes the marked section; user notes below stay intact.
- If the integration target comes back `unknown`, the generated file says so explicitly — review and fill in if you know the answer.

## Extensibility

This skill supports prd2 today. It is structured so support for additional deployment tools/formats can be added without touching the core: add a branch to `detect_tool` in `inspect.py`, a sibling inspector function for that tool's layout, and a `fragments/<tool>.md` carrying its workflow/commands/safety/layout. The CLAUDE.md skeleton (`template.md`) and integration-target detection are tool-independent and reused as-is.
