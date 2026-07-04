---
name: analyze
description: Analyze a Rossum.ai implementation to detect common configuration errors and issues. Discovers the full implementation first, then checks for known problems. Use when reviewing a customer's setup for correctness. Triggers on requests like "check this implementation", "find issues", "review this setup", "audit this config", "what's wrong with this project".
argument-hint: [path-to-implementation]
allowed-tools: Read, Grep, Glob, Bash, Agent
context: fork
---

# Analyze Rossum Implementation

You are a Rossum.ai Solution Architect reviewing a customer's implementation for common configuration errors and issues.

> Path or context: $ARGUMENTS

## Phase 1: Discover Everything

Follow the full discovery process in `skills/__shared/discovery-checklist.md` — use the provided path (or current directory if none given) and read every component listed there before continuing.

Do NOT produce output during this phase. Read everything first.

## Phase 2: Check for Common Issues and Produce the Report

With the full picture from Phase 1, check for these issues:

- **Broken schema references** — fields referenced by extensions, formulas, or rules that don't exist in the schema (orphaned references, typos in field IDs)
- **Hardcoded values in extensions** — URLs, IDs, or credentials embedded in hook code that should be in `hook.settings` or `hook.secrets`
- **Undeclared hook secrets** — hooks that use secrets (code reading `payload["secrets"]`, settings referencing `{secrets.*}` / `{payload.secrets.*}`, or credentialed integrations like Coupa/Workday/SFTP) whose `secrets_schema` is absent or still the platform default (no declared `properties`) — whoever rotates the credentials has to guess the key names; recommend declaring them (two shapes — see `rossum-reference` → Hook Object Fields)
- **Missing extension ordering** — extensions without `run_after` when execution order matters (e.g., data enrichment must run before validation)
- **Deprecated extensions** — Copy & Paste or Find & Replace extensions that no longer work correctly
- **Formula field mismatches** — formula files that reference schema fields not present in the queue's schema
- **Broken rule references** — rules referencing field IDs that don't exist in the schema
- **Contradictory rules** — rules where one requires what another forbids
- **Environment drift** — configuration differences between dev/test/prod environments that look unintentional (not just ID differences)
- **Plain-text secrets** — credentials, API keys, or secrets committed in plain text
- **Engine-binding inconsistencies** — for each engine-bound queue (`queue.json` → `engine` non-null): captured datapoints with non-empty `rir_field_names`, with `disable_prediction: true`, or without a matching engine field name (compare schema datapoint ids against `engines/*/engine_fields/*.json` `name` values). The live API rejects these states, so any hit means a stale local tree or a change that will fail on the next push.
- **Data Storage mismatches** — if the `rossum-api` MCP tools are available, verify MDH matching hook configs against live Data Storage:
  - Use `data_storage_list_collections` to check that every collection name referenced in matching configs actually exists
  - For each referenced collection, use `data_storage_list_indexes` and `data_storage_list_search_indexes` to retrieve its indexes
  - Cross-reference the matching query fields (from hook settings) against the available indexes. Flag fields used in `$match`, `$sort`, or `$search` stages that have no supporting index — these cause full collection scans and degrade matching performance
  - Flag collections that have Atlas Search indexes but whose matching config uses a plain `find`/`aggregate` query instead of `$search` (missed optimization), or vice versa
  - Flag duplicate or redundant indexes on the same collection (waste of storage and write overhead)

Only report issues you actually find. Do not report speculative or generic concerns. Ground every finding in specific files and line numbers. For any finding that depends on a hook's behavior, first confirm the hook has `active: true` and the affected queue in its `queues` list — config in inactive or unattached hooks does not run and must not be reported as a live issue (call it out as dormant config instead).

Write a markdown file named `ANALYSIS-[customer-or-folder-name].md`:

```markdown
# Analysis: [Customer/Project Name]

## Summary

One paragraph: what the implementation does and its overall health.

## Issues

| # | Severity | Area | Issue | File |
|---|----------|------|-------|------|

Severity: **Error** (will cause incorrect behavior), **Warning** (likely unintended), **Info** (minor, worth noting).

For each row, add a short paragraph below the table explaining the issue and how to fix it.
```
