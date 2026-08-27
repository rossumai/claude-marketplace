---
name: queue-engine-binding
description: Bind Rossum queues to custom extraction engines via the public API. Four modes — convert a generic-engine queue to a new custom engine, create a new engine-bound queue (greenfield), attach a queue to an existing engine, or revert to the generic engine. Use when changing which extraction engine a queue uses, when extraction needs fields outside the pretrained catalog, or when the user says "convert this queue to a custom engine", "create an engine for this queue", "attach this queue to engine X", "detach the engine".
argument-hint: "[convert|greenfield|attach|revert] [queue-id]"
allowed-tools: Read, Write, Grep, Glob, Bash
---

# Queue ↔ Engine Binding

> Mode and target: $ARGUMENTS

Reconciles schema datapoints ↔ engine fields ↔ queue binding, then sets `queue.engine`. Background and binding rules: `rossum-sa:rossum-reference` → Extraction Engines.

> **Scope**: this skill covers bind-level mode changes (convert/greenfield/attach/revert). For incrementally adding, adjusting, or removing a single field on an ALREADY engine-bound queue, use the MCP tools directly instead — `rossum_create_engine_field` → `rossum_patch_schema` to add (engine field first; dry-run the schema edit with `rossum_validate_schema` + schema id), the reverse order to remove (`rossum_delete_engine_field` 409s while the datapoint exists), `rossum_patch_engine_field` to adjust.

## Safety

- **Every mode defaults to dry-run.** Show the user the printed plan (engine fields to create, schema changes) and get explicit confirmation before re-running with `--execute`. Never skip this for production queues.
- Convert/attach/revert write a pre-state snapshot with `--snapshot-dir` — always pass it; the snapshot is the revert path.
- A 403 on engine creation means the org/token lacks the permission — stop and advise the user to contact Rossum support; record which org/role combination failed.

## Running

```
export ROSSUM_TOKEN=<token>
python3 ${CLAUDE_PLUGIN_ROOT}/skills/queue-engine-binding/engine_binding.py \
  convert --base-url https://<org>.rossum.app --queue-id <id> \
  --snapshot-dir ./engine-binding-snapshots
```

| Mode | Required args | What it does |
|---|---|---|
| `convert` | `--queue-id` | Generic-bound queue → new engine: derive engine fields from the schema (pretrained-catalog seeding), clean the schema (`rir_field_names: []`, normalize `ui_configuration`, strip `disable_prediction`), create engine + fields, flip the queue. |
| `greenfield` | `--schema-file --queue-name --workspace-url` | New queue born engine-bound from a local schema JSON. |
| `attach` | `--queue-id --engine-id` | Bind to an existing engine; creates only the missing engine fields, then cleans + flips. |
| `revert` | `--queue-id --generic-engine-url` | Detach to generic engine and restore `rir_field_names` from `pre_trained_field_id` mappings (or restore the snapshot schema manually for exact pre-state). Find the `--generic-engine-url` value in the convert snapshot's `pre_queue.json` under `generic_engine`, or in any sibling generic-bound queue's `queue.json`. |

If the schema is shared by multiple queues, `convert` stops and tells you to copy the schema first (POST /v1/schemas with the same content, point this queue at the copy) — it does not copy automatically.

Schema cleanup normalizes every engine-extracted datapoint to `ui_configuration: {"type": "captured", "edit": "enabled"}`. A captured-but-read-only field (`edit: "disabled"`) is therefore flipped to `"enabled"`; this matches the platform's own conversion behavior. Fields with `ui_configuration.type` `formula`/`data`/`manual`/`reasoning` are left untouched.

## Interpreting failures

- Queue-flip 400s list ALL remaining violations in `non_field_errors` — read them verbatim; each names a field and the exact rule.
- Schema-write 400s while bound are per-field, under `content`.

## Aftermath (always)

1. If the org is managed as a prd2 project: `prd2 pull` to refresh the local tree (engines/, engine_fields/, queue.json, schema.json all changed).
2. Set expectations: pretrained-seeded fields extract at catalog quality immediately; custom fields (`pre_trained_field_id: null`) start cold and learn from confirmed annotations in `training_queues`.
3. For behavior comparison before/after, see `rossum-sa:test-behavioral-equivalence`.
