---
name: implement
description: Plan and execute a Rossum integration project end-to-end. Guides through scoping, schema design, MDH configuration, hook development, business rules, export pipelines, and deployment. Use when starting a new implementation, adding a major feature, or onboarding to an existing project. Triggers on requests like "implement this project", "set up this integration", "build this queue", "start this implementation".
argument-hint: [project description, SOW, or requirements]
---

# Implement Rossum Integration

You are a Rossum.ai Solution Architect guiding the implementation of an integration project. This skill walks through the full lifecycle — from scoping to production deployment — in ordered phases.

> Project context: $ARGUMENTS

## Safety: Remote API Confirmation Gate

<HARD-GATE>
Before ANY API call or CLI command that **creates, modifies, or deletes** resources in a remote Rossum environment, you MUST:

1. **Present exactly what will be done** — tool name, target environment, what gets created/changed/deleted
2. **Wait for explicit user confirmation** — do NOT batch multiple write operations into one approval
3. **Never proceed without a clear "yes"** from the user

This applies to:
- Creating hooks, schemas, or queues via the Rossum API
- `prd2 push` and `prd2 deploy` commands
- Any `data_storage` write operations (insert, update, delete)
- Modifying hook configurations on a live environment

**Read-only operations are fine without confirmation:** listing collections, reading schemas, querying data storage, `prd2 pull`, `data_storage_aggregate` for reads.

If in doubt, confirm. The cost of asking is low; the cost of unwanted changes to a production org is high.
</HARD-GATE>

## How to Use This Skill

This skill has 7 phases. Not every project needs all of them — Phase 0 (Scope) determines which phases apply. Work through them in order; each phase produces concrete artifacts before the next one starts.

Use tasks to track progress across phases so work can resume if interrupted.

At each phase, reference the appropriate skill for detailed guidance rather than duplicating content:

| Phase | Reference Skills |
|-------|-----------------|
| 1 — Project Setup | `prd-reference` |
| 2 — Schema Design | `rossum-reference` (schema templates, extraction engines), `queue-engine-binding` (engine-bound queues) |
| 3 — Master Data Hub | `mdh-reference`, `mongodb-reference`, `data-storage-reference` |
| 4 — Extensions & Serverless Functions | `txscript-reference`, `rossum-reference` (hook patterns), `coding-best-practices` (hook code quality/security) |
| 5 — Business Rules | `business-rules-reference` (native Rules + legacy BRV extension), `txscript-reference` (`trigger_condition` syntax) |
| 6 — Export Pipeline | `export-pipeline-reference` (Request Processor config), `render-export-template` (legacy Jinja2 export templates), `sap-reference` / `coupa-baseline-reference` (target-specific) |
| 7 — Test & Promote | `prd-reference` (deployment), `iterate` (single-annotation loop), `test-behavioral-equivalence` (corpus regression) |

---

## Phase 0: Scope

Before building anything, understand what needs to be built. If a SOW exists, use it to pre-fill answers. Otherwise, ask the user these questions (one at a time):

1. **Project directory** — does a prd2 project already exist, or are we starting fresh?
2. **Environments** — which ones? (dev, test, UAT, prod)
3. **Document types & queues** — how many, what kinds? (invoices, POs, delivery notes, utility bills, transport docs)
4. **Regions/workspaces** — single or multi-region?
5. **Master data** — what needs matching? (suppliers, POs, tax codes, GL accounts, payment terms, commodity codes)
6. **Integration target** — where do documents go? (Coupa, SAP, NetSuite, SFTP, custom API)
7. **Special requirements** — document sorting, duplicate detection, multi-step approval, line-item matching?

Based on answers, determine:
- **Which phases are needed** (e.g., no master data = skip Phase 3, no export = skip Phase 6)
- **Engine binding per queue** — generic engine vs. custom engine, decided by pretrained-catalog coverage of the required fields (see `rossum-reference` → Extraction Engines → "Choosing an engine")
- **Relative complexity** — simple (1-2 queues, basic matching), medium (3-10 queues, MDH + export), complex (10+ queues, multi-region, multiple integrations)
- **Multi-step approval** — if documents route through an approval chain, see `approval-workflows-reference` (a paid, Rossum-configured feature — read-only via the public API; no create/edit endpoint)

Create a task list with one task per applicable phase to track progress.

---

## Phase 1: Project Setup

**Goal:** A local project directory with current configs pulled from the environment.

**Steps:**

1. **Initialize or locate the prd2 project.** If starting fresh, set up the directory structure:
   ```
   project-name/
   └── environment-name/
       ├── hooks/
       └── workspaces/
           └── Workspace_[id]/
               └── queues/
                   └── Queue_[id]/
                       ├── queue.json
                       ├── schema.json
                       └── formulas/
   ```

2. **Configure prd2 credentials** for the target environment. See `prd-reference` for credential setup.

3. **`prd2 pull`** to get the current state of the environment. This is a read-only operation.

4. **Generate `CLAUDE.md` for the project** — run `/rossum-sa:init-claude-md` so future sessions in this directory recognize it as a Rossum implementation and apply the right safety rules. This is optional but recommended for any project that will be touched by Claude Code multiple times.

5. **Review what exists.** If there are already queues, hooks, and schemas, use the `analyze` skill to check for issues before adding to the implementation.

**Artifact:** Local project directory synced with the remote environment.

---

## Phase 2: Schema Design

**Goal:** All required fields exist in the queue schemas, with correct types.

**Steps:**

0. **Check the queue's engine binding first** (`queue.json` → `engine`). On engine-bound queues, extraction wiring uses engine fields (name match, empty `rir_field_names`), not `rir_field_names` — and each new captured datapoint needs its engine field (`name` == the datapoint id) created **first**, or the schema push is rejected. Create it with `rossum_create_engine_field` (edit an existing one with `rossum_patch_engine_field`; note `name` is immutable) before adding the datapoint to the schema. For changing which engine a queue is bound to (or greenfield engine creation), use the `queue-engine-binding` skill. See `rossum-reference` → Extraction Engines for the binding rules.

1. **List all fields needed** per queue — group by:
   - **Captured** (OCR-extracted): `type: "string"`, `ui_configuration.type: "captured"` (on engine-bound queues a matching engine field must exist — see step 0)
   - **Enum (MDH target)**: `type: "enum"`, `ui_configuration.type: "data"` — used for ANY field populated by MDH, including additional mappings
   - **Formula (derived)**: `type: "string"` or `type: "enum"`, `ui_configuration.type: "formula"` — auto-calculated from other fields
   - **Manual**: `type: "string"` or `type: "enum"`, `ui_configuration.type: "captured"`, `edit: "enabled"` — user-entered values

2. **Critical rule: All MDH-populated fields must be enum type.** Both `mapping.target_schema_id` and all `additional_mappings[].target_schema_id` targets must use `"type": "enum"`. A string field silently drops the MDH value. Use `"edit": "enabled"` for the primary matched field and `"edit": "disabled"` for derived/read-only fields.

3. **Add fields to schema.json** locally. Use the schema field templates from `rossum-reference`.

4. **Dry-run the schema before deploying.** `rossum_validate_schema` (read-only) returns `{}` for a valid schema or a positional error tree — pass the queue's `schema_id` so engine-binding checks run. Fix any errors locally first.

5. **Deploy schema changes.** Either `prd2 push` (the prd2-tree path — **confirm with user before executing**) or, for a direct-API edit outside a prd2 flow, `rossum_patch_schema` (also gated). Prefer `prd2 push` when the project is a prd2 tree so the local tree stays the source of truth.

**Artifact:** Updated schema.json files with all required fields, deployed to the environment.

---

## Phase 3: Master Data Hub

**Goal:** MDH hook configured with datasets, query cascades, and field mappings.

**Prerequisites:** Phase 2 complete — target enum fields must exist in the schema.

**Steps:**

1. **Verify data storage collections exist.** Use `data_storage_list_collections` to check. If datasets need to be created or imported, confirm with the user first.

2. **Check indexes.** Use `data_storage_list_indexes` (btree) and `data_storage_list_search_indexes` (Atlas Search). If the matching strategy uses Atlas Search (fuzzy matching), the named index must be declared on the dataset, `queryable: true`, and must map every field the query references. Search-index declarations are durable — MDH reconciles them onto the collection — so a gap here is a missing or misnamed *declaration*, not drift. Btree indexes are **not** self-healing: a recreated dataset can land without the wildcard `$**` index and every `$match` then full-scans.

3. **Design the query cascade** for each MDH section. Follow the mandatory order from `mdh-matching-queries`:
   - **Query 1: Exact identifiers** — VAT/tax ID, PO number, ERP ID
   - **Query 2: Combined references** — supplier + order reference, name + address
   - **Query 3: Fuzzy search** — Atlas Search with `maxEdits`, score normalization
   - Execution stops at the first query that returns results.

4. **Create the MDH hook.** In a prd2 tree, create the hook via the API to get its ID, then author the (large) config locally:
   1. **Create the hook shell** with `rossum_create_hook` — this registers the hook and assigns an ID. Pass `settings`, `description`, and (if it reads credentials) `secrets_schema` in the same call so you don't have to patch them afterwards. `rossum_create_hook_from_template` instantiates from the `hook_templates` catalog; `rossum_duplicate_hook` clones an existing one. **Confirm with user before executing.**
   2. **`prd2 pull`** — pulls the new hook's JSON config file into the local project directory (read-only).
   3. **Populate the hook config** locally — add sections with datasets, queries, mappings, result actions, and additional mappings.
   4. **`prd2 push`** — deploys the populated config back to the environment. **Confirm with user before executing.**

5. **Wire hook ordering.** If the MDH hook must run before or after other hooks, set the `run_after` field in the dependent hook's config.

6. **Test the matching.** Use `data_storage_aggregate` to run the query pipeline manually against sample data and verify results before relying on MDH automation.

**Artifact:** MDH hook JSON configs with working query cascades and field mappings.

---

## Phase 4: Extensions & Serverless Functions

**Goal:** Custom logic hooks created and deployed — validation, transformation, document sorting, etc.

**Prerequisites:** Phase 2 complete. Phase 3 complete if extensions depend on MDH results.

**Steps:**

1. **Identify what custom logic is needed.** Common patterns from real implementations:
   - Data transformation (normalize fields, extract values)
   - Document sorting (route to queues based on field values)
   - Tax code automation (regional tax logic)
   - Field validation beyond business rules
   - Pre/post-processing around MDH or export

2. **For each extension, follow the hook creation workflow:**
   1. **Create the hook** with `rossum_create_hook` (declare `settings` + `secrets_schema` in the same call for credentialed hooks; `rossum_create_hook_from_template` / `rossum_duplicate_hook` are the template/clone variants). **Confirm with user before executing.** Review the code against `coding-best-practices` before it goes live.
   2. **`prd2 pull`** to get the hook config locally.
   3. **Write the serverless function** code in the `.py` file using the TxScript API (see `txscript-reference`). **NEVER edit the `code` field inside the hook JSON** — `prd2` extracts code into `.py` files on pull and merges it back on push, so the `.py` file is the single source of truth.
   4. **`prd2 push`** to deploy. **Confirm with user before executing.**

3. **Define `run_after` ordering.** Map out the execution chain:
   - MDH hooks first (data enrichment)
   - Transformation/validation hooks next
   - Export hooks last
   - Response parsing hooks after export

   Set `run_after` in each hook's JSON config to point to the hook URLs that must complete before it.

4. **Formula fields.** For simple calculations, prefer formula fields over serverless functions:
   - Create the formula as `formulas/<field_id>.py` in the queue directory
   - The schema.json formula property is auto-synced on deploy — only edit the `.py` file, never the JSON
   - See `rossum-reference` for formula field patterns

> **Code editing rule:** Always edit the `.py` file, never the `code` field in the hook JSON or the `formula` property in schema JSON. `prd2` manages the JSON ↔ `.py` synchronization automatically.

**Artifact:** Hook configs and serverless function code, deployed with correct execution ordering.

---

## Phase 5: Business Rules

**Goal:** Validation rules that enforce data quality and block bad documents from export.

**Prerequisites:** Phases 2-4 complete — rules reference schema fields that must exist.

See `business-rules-reference` for the full picture — it owns both validation surfaces and when to choose each.

**Steps:**

1. **Prefer native Rossum Rules** (the `/v1/rules` entity) — the modern, platform-native path. A Rule is a boolean `trigger_condition` (a **TxScript** expression — `field.X` access; see `txscript-reference`) plus an `actions[]` array (`show_message` at `error`/`warning`/`info`, `add_automation_blocker`, show/hide toggles). **Polarity: `trigger_condition` is the FIRE predicate** — it must be `True` in the *problem* state (invert any expression that describes the OK state). A `show_message(error)` + `add_automation_blocker` pair both blocks export and shows the banner.
   - Use the legacy **Business Rules Validation extension** (`checks[]` config, `{field}`-brace engine) only when maintaining an existing one — `business-rules-reference` covers its syntax.

2. **Create/edit the rule.** MCP has full native-rule CRUD: `rossum_create_rule`, `rossum_get_rule`, `rossum_list_rules`, `rossum_patch_rule`, `rossum_delete_rule` (and `rossum_list_rule_execution_logs` for debugging). In a prd2 tree, create via the `_[]` placeholder on push instead. **Confirm with user before any write.** Two gotchas (both live-confirmed):
   - **`name`/`description` are capped at 255 chars** — a longer `description` 400s. Keep both short; rationale belongs in the spec/plan, not the rule.
   - **A rule can land with `queues: []` and silently never evaluate** — notably a prd2 `_[]`-placeholder push doesn't send the rule-side queues. After creating, verify `GET /rules/{id}` (or `rossum_get_rule`) shows the intended queues; if empty, set them via `rossum_patch_rule` (`queue_ids`) or add the rule URL to `queue.json`'s `rules` array and push the queue.

3. **Configure duplicate detection** if needed — set up the duplicate detection extension with the relevant fields.

**Artifact:** Native Rules (or BRV `checks[]` config) deployed and attached to the queue.

---

## Phase 6: Export Pipeline

**Goal:** Documents flow to the target system (Coupa, SAP, SFTP, etc.) after confirmation.

**Prerequisites:** Phases 2-5 complete — all fields and validation in place.

**Steps:**

1. **Create the export hook** using the hook creation workflow (API → pull → populate → push). **Confirm with user at each write step.**

2. **Build the export mapping.** Two distinct surfaces — pick by target:
   - **Legacy Jinja2 / Custom Format Templating** (flat file, CSV, XML, EDI, custom JSON) — use the **`render-export-template`** skill to author, render against a real annotation, and iterate on the template; `rossum_extract_export_template` / `rossum_generate_export_payload` / `rossum_generate_export_settings` are the supporting MCP tools.
   - **Request Processor (JSON export pipeline)** — for multi-stage API integrations; see `export-pipeline-reference`.

3. **If the export target requires authentication** (OAuth, API key), configure credentials in `hook.secrets` — never hardcode them in the hook config — and declare the expected key names in the hook's `secrets_schema` so the Secrets editor prefills `__change_me__` placeholders instead of an empty `{}`. This is the standard for every hook that carries secrets; pick the closed or open shape per `rossum-reference` → Hook Object Fields (open when the hook writes its own secrets at runtime, e.g. OAuth token caching).

4. **Chain response parsing.** If the export returns data that needs to be processed (e.g., Coupa returns an invoice ID), create a response parsing hook with `run_after` pointing to the export hook.

5. **Target-specific references:**
   - **Coupa** → `coupa-baseline-reference` (the CIB baseline: schema, MDH matching, export pipeline); `coupa-bulk-replication` for large master-data imports.
   - **SAP** → `sap-reference` for IDOC generation patterns, middleware requirements, and master data considerations.
   - **Workday** → `workday-reference` for the Rossum-hosted SOAP connector (`svc/workday`): export/import hook wiring + secrets, the mapping template DSL, and MDH master-data import with differential sync.
   - **SFTP** → `export-pipeline-reference` (file-storage-export) for outbound file delivery.

**Artifact:** Export hook config + Jinja2 mapping template, response parsing hooks if needed.

---

## Phase 7: Test & Promote

**Goal:** Working pipeline validated in dev, promoted through environments to production.

**Steps:**

1. **Test in dev/sandbox.**
   - Upload sample documents
   - Verify extraction quality (captured fields)
   - Verify MDH matching returns correct results
   - Verify formula calculations
   - Verify business rules fire correctly
   - Verify export sends correct payload
   - Verify response parsing captures returned values

   For the tight inner loop on a single annotation — "did my hook/formula/rule produce the right value on this document?" — hand off to the `iterate` skill. It owns the re-fire primitives (soft re-fire via `content/validate`, status toggle, re-upload) and the iterate-edit-push-retest cadence. Ask the user for a **sandbox/UAT** annotation ID when this phase opens — never iterate against production.

2. **Fix issues** found during testing. Iterate on schema, MDH queries, serverless functions, and rules as needed.

3. **Promote to UAT.** Use `prd2 deploy` to push configs from dev to UAT. **Confirm with user before executing — this modifies the UAT environment.**

4. **UAT validation** with the customer/stakeholders. Address feedback.

5. **Promote to production.** Use `prd2 deploy` to push from UAT to prod. **Confirm with user before executing — this modifies the production environment.**

6. **Post-go-live monitoring.** Check for:
   - Documents stuck in review (MDH matching issues)
   - Export failures (auth, payload format)
   - Unexpected business rule triggers

**Artifact:** Working pipeline in the target production environment.

---

## Completion

When all applicable phases are done:
1. Use the `document` skill to produce a queue-focused reference of the implementation
2. Commit all local configs to git
3. Summarize what was built: queues, hooks, datasets, integrations
