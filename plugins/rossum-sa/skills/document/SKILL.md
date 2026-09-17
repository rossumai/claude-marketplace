---
name: document
description: Document a Rossum.ai implementation — produce a structured technical reference covering architecture, hooks, queues, integrations, formulas, and troubleshooting. Use this skill whenever the user wants to understand, describe, summarize, or write up what an implementation does. Triggers include requests like "document this project", "what does this implementation do", "write up the queues", "summarize this setup", "create a reference doc", or any request to explain or map out a Rossum customer configuration.
argument-hint: [path-to-implementation]
allowed-tools: Read, Grep, Glob, Bash, Agent
context: fork
---

# Document Rossum Implementation

You are a Rossum.ai Solution Architect. Your job is to fully analyze an implementation and produce comprehensive technical documentation suitable for Confluence, project closure, or support handoff.

> Path or context: $ARGUMENTS

## Phase 1: Discover Everything

Follow the full discovery process in `skills/__shared/discovery-checklist.md` — use the provided path (or current directory if none given) and read every component listed there before continuing.

Use an **Explore subagent** to thoroughly read:

| What to Read | Where |
|---|---|
| All hook configs | `sandbox/<env>/hooks/*.json` |
| Workspace/queue structure | `sandbox/<env>/workspaces/*/queues/*/queue.json` |
| Schema definitions | `sandbox/<env>/workspaces/*/queues/*/schema.json` |
| Formula calculations | `sandbox/<env>/workspaces/*/queues/*/formulas/*.py` |
| Utility scripts | Project root (`*.py`, `*.sh`) |

**Subagent prompt template:**
> Explore the codebase to find ALL files related to: hooks, serverless functions, schemas, formulas, queue configs, and utility scripts. For each file, read its contents and summarize: what it does, its configuration, how it fits into the processing pipeline. Look for mentions of: SFTP, XML, TIFF, PDF, flat file, SAP, Coupa, export, MDH, memorization, duplicate, business rules, validation.

Additionally, if the `rossum-api` MCP tools are available, use `data_storage_list_collections` to discover datasets and `data_storage_list_indexes` / `data_storage_list_search_indexes` to understand indexing. This adds context about what master data backs the matching hooks.

Make those calls **in this session, not in the Explore subagent** — a subagent cannot establish the Rossum connection, and the table above is local files anyway. When you need a live object an agent has to read, dump it first (`rossum_get_schema` / `rossum_get_hook` with `out_file_path`) and hand over the path: [`../__shared/fan-out-rules.md`](../__shared/fan-out-rules.md).

Do NOT produce output during this phase. Read everything first.

## Phase 2: Map the Processing Pipeline

From the hook JSON files, extract:
- **Event types**: `annotation_content.initialize`, `updated`, `confirm`, `export`
- **Execution order**: `run_after` dependencies between hooks
- **Conditional gates**: `condition_actions` fields
- **Queue assignments**: which hooks run on which queues

Draw the pipeline as a sequential + parallel flow showing hook execution order.

## Phase 3: Produce the Documentation

Write a single markdown file named `docs/[customer-or-folder-name].md` with this structure. Omit sections that don't apply to the project.

```
# {Project Name}: Technical Documentation

> Project / Platform / Status / Last Updated / Author metadata block

## Table of Contents

## 1. Project Overview
- What the project does (1-2 paragraphs)
- Key workspaces table (name, ID, purpose)

## 2. Architecture & Processing Pipeline
- Document lifecycle (ingestion → extraction → enrichment → review → export)
- Export pipeline diagram showing hook execution order with IDs
- Parallel vs sequential flows

## 3. Workspaces & Queues
- Table per workspace: queue name, ID, purpose
- Number of hooks per queue
- Group queues by use case (document type, region, or business unit)
- If queues share configuration, describe the shared setup once and note differences

## 4-6. Core Processing Hooks (one section each)
For each major serverless function:
- Hook name, ID, type, event, memory, timeout
- What it does (step by step)
- File format specification (if generating files)
- Country/region-specific variations
- Configuration parameters
- Size/performance optimization details

## 7. ERP Integration (Coupa/SAP)
- Authentication method (OAuth, API key, etc.)
- Sequential hook chain with IDs
- Conditional execution gates
- Error handling

## 8. Master Data Hub (MDH)
- Hook details
- Data collections table
- Matching strategies (exact, fuzzy, memorized, fallback)
- Coupa/SAP data import webhooks

## 9. Business Rules & Validation
- Hook table (name, ID, scope)
- Validations performed (bullet list)

## 10. Duplicate Detection
- Matching criteria
- MongoDB query example

## 11. Memorization Hooks
- Table of memorization hooks (name, ID, purpose)

## 12. Schema & Formula Calculations
- Core field groups table
- Python formula code with explanation for each

## 13. External System Integration (SFTP, APIs)
- Connection details table
- Authentication methods
- File types uploaded

## 14. Idempotency & State Management
- State flag values table
- Duplicate prevention mechanisms

## 15. Error Handling & Edge Cases
- Tables per subsystem: issue → handling

## 16. Utility Scripts
- Script name, purpose, usage example

## 17. Configuration & Secrets
- Plaintext settings table
- Encrypted secrets table

## 18. Legacy System Mapping (if replacing legacy)
- Table: legacy feature → new implementation → hook ID

## 19. Troubleshooting Guide
- Common issues with check/fix steps
- Hook execution order debugging
- Logs & debugging tips

## Appendix: Complete Hook Inventory
- Full table: hook name, ID, type, event
```

## Key Details to Capture

For each **serverless function / Lambda hook**, always document:
- Memory and timeout settings
- Third-party library packs used
- Input/output format specs (encoding, delimiters, structure)
- Country/region-specific variations in field ordering or naming
- Size optimization strategies (if applicable)
- Idempotency mechanism (how it prevents duplicate processing)

For each **webhook integration**, always document:
- Auth method and token management
- Request/response format
- Conditional execution gates
- Error parsing and user-facing messages

For **SFTP integrations**, always document:
- Host, port, directory
- Auth options (password vs SSH key)
- File naming conventions
- Remote directory creation behavior

## Output

- Write as a single `.md` file in `docs/` directory
- Use tables extensively for scannable reference
- Include code blocks for queries, XML structures, formulas
- Keep language support-team friendly (not developer-only)
- Add a troubleshooting section with actionable steps
- Omit sections that don't apply to the project (no SAP integration? skip that section; no SFTP? skip those sections; no legacy replacement? skip the mapping table)

## Writing Guidelines

- **Use tables extensively** for scannable reference.
- **Include code blocks** for queries, XML structures, formulas.
- **Keep language support-team friendly** — not developer-only.
- **Lead with purpose, not config.** Say "validates vendor against SAP master data" not "runs MDH matching hook with dataset_id 12345".
- **Skip the obvious.** Don't describe standard Rossum behavior. Only document what's specific to this implementation.
- **Reference file paths** so readers can dig deeper, but don't dump config details into the doc.
- When you infer the "why", say so ("This likely handles..." or "This suggests...").
