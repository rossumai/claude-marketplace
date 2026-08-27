---
name: test-behavioral-equivalence
description: Verify that changes to a Rossum implementation preserve behavior. Captures a "before" snapshot of annotation outputs, replays the same documents against the upgraded implementation, and diffs the "after" outputs against the snapshot. Use after an upgrade, refactor, or any change intended to be behavior-preserving. Triggers on "test this upgrade", "verify this change", "regression test", "check behavioral equivalence", "did my changes break anything", "test before promoting".
argument-hint: "[path-to-target-impl] [--source-env=<name>] [--target-env=<name>] [--corpus=<ids-file>] [--canary] [--skip-static]"
allowed-tools: Read, Grep, Glob, Bash, Agent
context: fork
---

# Verify Behavioral Equivalence of a Rossum Implementation

You are a Rossum.ai Solution Architect verifying that a change to a customer's implementation preserves behavior. The core goal is **behavioral equivalence**: for a representative corpus of documents, the upgraded implementation should produce the same observable state as the original.

**Prerequisites:** two coexisting Rossum environments — a `source` running the old config (the behavioral ground truth) and a `target` running the upgraded config (the replay target) — plus a local repo holding the upgraded files for Phase 1 static checks and migration-trace extraction. See Phase 0e for setup detail.

> Path or context: $ARGUMENTS

## Safety: Remote API Confirmation Gate

<HARD-GATE>
Before ANY API call or CLI command that **creates, modifies, or deletes** resources in a remote Rossum environment, you MUST:

1. **Present exactly what will be done** — tool name, target environment, what gets created/changed/deleted
2. **Wait for explicit user confirmation** — do NOT batch multiple write operations into one approval
3. **Never proceed without a clear "yes"** from the user

This applies to:
- Creating the test queue via the Rossum API
- Uploading documents to the test queue
- PATCHing annotation fields during replay
- Confirming annotations during replay
- `prd2 push` commands (scoped via `-io` to indexed files only — confirm the file list before executing)

Read-only operations are fine without confirmation: listing hooks/schemas/annotations, `rossum_get_document`, `rossum_get_annotation_content`, `data_storage_find`/`aggregate` for reads.

**Never run replay against a production queue.** The test queue and any replay annotations must live in a dedicated test queue or sandbox environment.
</HARD-GATE>

## How to Use This Skill

Five phases. Phase 0 decides what testing is needed; phases 1-4 produce the test report. Use tasks to track progress.

| Phase | Reference Skills |
|-------|-----------------|
| 0 — Scope | Change manifest from `upgrade`, `__shared/discovery-checklist.md` |
| 1 — Static Validation | `rossum-reference`, `txscript-reference` |
| 2 — Build Corpus | `rossum-reference` (annotation lifecycle), `prd-reference` |
| 3 — Replay | `txscript-reference`, `mdh-reference`, `export-pipeline-reference` |
| 4 — Diff & Report | — |

### Input state

Two Rossum environments are required: `source` (old config — the behavioral ground truth) and `target` (upgraded config — the replay target). The local repo must hold the upgraded config as files for Phase 1 static checks and migration-trace extraction. Common entry points: after the `upgrade` skill (gate on whether the upgraded files have been pushed to `target` yet), after a manual refactor (change manifest comes from `git diff` in Phase 0a instead of an `UPGRADE-*.md`), or pre-promote checks on an already-deployed upgrade.

### Bundled scripts

The deterministic mechanics of phases 1-4 are packaged as Python scripts in `./scripts/` next to this SKILL.md. Use them instead of generating ad-hoc Python each run:

| Script | Purpose | Phase |
|---|---|---|
| `scripts/static_validate.py` | Schema-ref, formula-constraint, hook-chain, rule-URL checks; **with `--source-root`, cross-tree formula-preservation check (1g) catches migration-induced formula loss** | 1 |
| `scripts/capture_snapshots.py` | Fetch annotation+content+document+blocker for each corpus item (blocker URL auto-derived from annotation) | 2c |
| `scripts/normalize_snapshot.py` | Flatten content tree → `<id>.normalized.json` for diffing | 2c, 3 |
| `scripts/build_patch_plan.py` | Map prod snapshot to target dp IDs; emit replace ops with value+page+position; **classify each op as `pre`/`post`-hook based on prod's validation_sources**; surface multivalue row mismatches; filter formula-typed targets when `--target-schema` is passed | 3a |
| `scripts/apply_patch_plan.py` | Apply a patch plan against a target annotation in **two phases with a status toggle in between**: pre-hook ops → status toggle (postpone+restore to fire `annotation_content.started`) → wait → post-hook ops. Also: row ops via `/content/operations`, replace ops via per-datapoint `PATCH /content/<dp_id>`, post-PATCH read-back, dedup auto-restore | 3a |
| `scripts/diff_phase4.py` | Per-annotation diff with classification ladder + cross-corpus clustering | 4a, 4b, 4d |

LLM still owns: scoping decisions, corpus selection strategy, user gates, root-cause hypothesis writing, and report narrative. Scripts handle the mechanics deterministically; the LLM passes them config files (hot_fields, out_of_scope, structural_new) that capture upgrade-specific judgment.

### File naming conventions for runs

For scripts to interoperate, runs use this layout:

```
.test-runs/<timestamp>/
├── corpus.json                # {items: [{annotation_id, document_id, automation_blocker?, ...}]}
├── replay-mapping.json        # {<prod_aid>: {uatv2_annotation_id|target_annotation_id, ...}}
├── diff-config.json           # {hot_fields, out_of_scope, structural_new/_removed, ocr_drift_paths, mode}
├── migration-trace.json       # {F##: [{source_hook_id, source_id, region, ...}]}
├── before/                    # source snapshots — files keyed by SOURCE annotation id
│   ├── <id>.annotation.json
│   ├── <id>.content.json
│   ├── <id>.document.json
│   ├── <id>.blocker.json      # only if blocker present
│   └── <id>.normalized.json   # produced by normalize_snapshot.py
├── after/                     # target snapshots — files keyed by TARGET annotation id
│   ├── <id>.annotation.json
│   ├── <id>.content.json
│   ├── <id>.blocker.json
│   └── <id>.normalized.json
├── documents/                 # downloaded source PDFs for re-upload
│   └── <source_aid>__<filename>.pdf
├── patch-plans/<source_aid>.json   # one per replayed annotation
├── phase1-static.json
├── phase4-diff.json
└── TEST-*.md                  # final report
```

Use these exact filenames so the scripts find each other's outputs.

---

## Phase 0: Scope the Test

**Goal:** Understand what changed, decide which test layers are needed, confirm the target environment, and establish the export architecture.

### 0a. Read the change manifest

If the upgrade came from the `upgrade` skill, read the `UPGRADE-*.md` file it produced — this lists exactly which hooks, formulas, and schemas changed. Otherwise, run `git diff` (or ask the user for the diff) to derive the list.

**Look for migration-traceability hints.** Deactivated hooks often retain their old `settings` with inline comments like `// C00 -> F02 on schema 9579361 (NL)` that map old calculation/transformation IDs to their formula replacements. Load these into a mapping structure — in Phase 4, diff results can be enriched with "this field was migrated from hook X's calculation Y", which dramatically speeds up triage.

### 0b. Classify each change

Decide which test layers apply:

- **Dead code removal only** → static validation is sufficient, skip replay.
- **Refactor of existing hook / hook → formula migration / deprecated hook replacement** → full pipeline: static + replay + report.
- **Hook → native business rules migration** → replay mandatory; **Phase 4 must emphasize message and automation_blocker parity as first-class diff results, not "soft-fail"**. A rule that used to fire an error but now fires nothing is a hard regression even if all field values match.
- **MDH restructure** (splitting a monolithic MDH hook into per-region/per-function hooks) → replay mandatory; verify matching results and additional_mappings outputs.
- **Extraction or OCR-adjacent logic changed** → replay mandatory; flag pre-OCR hooks as in scope.
- **Export pipeline changed** → replay must drive annotations through `confirmed` and compare export outputs (see 0d for export architecture decision).

### 0c. Classify out-of-scope changes

Some changes are deliberately non-equivalent and must be excluded from the equivalence diff. Common examples:

- **Endpoint target cutover** — a hook pointing at a production Peppol/SFTP/API endpoint in `prod` now points at a test endpoint in the upgraded env. The whole point is that it sends to a different target. Phase 3 must not try to diff the response payload.
- **Deliberate behavior improvements** — upgrades that are *supposed* to fix existing wrong behavior (e.g., correcting a rounding bug). These fall outside behavior-preservation testing.

Produce an explicit **out-of-scope changes** section in the scope statement. Items listed there are documented but not diffed in Phase 4.

### 0d. Determine the export architecture

**Ask the user how export works in this customer's implementation.** This is not always visible in Rossum configuration. Three patterns:

1. **In-Rossum hook export** (default assumption) — an export hook in the queue chain posts to the target system (Coupa, SAP, NetSuite, SFTP, etc.) on the `confirmed` event. Visible as a webhook or Request Processor hook in `queue.json`. Phase 3 diffs the serialized export payload.

2. **External-service-driven export** — an external service polls Rossum for confirmed documents, performs the export itself (SFTP upload, API call, etc.), then calls Rossum's API to transition the annotation to `exported`. This service is **invisible in the Rossum repo** — there is no hook to diff, no payload to capture. The only observable signals are: (a) did the status transition to `exported`, (b) how long did the transition take, (c) were any errors recorded on the annotation or in audit logs.

3. **Hybrid** — some queues export via hook, others via external service. Determined per queue.

Based on the answer:
- **Pattern 1** → standard replay with export-payload diff in Phase 3.
- **Pattern 2** → Phase 3 drives to `confirmed` and then **polls for status transition**. Compare status-level signals only: did `confirmed` → `exported` happen, time-to-export, error count. **Do not attempt to diff an export payload — it doesn't exist in Rossum.** Surface in the report: "Export is handled externally; this skill verified only status transition, not export content."

  **Timeout sizing.** The timeout must be at least **2× the external service's polling interval**, plus processing headroom for the export itself (SFTP upload, response handling, Rossum API call back). As a default, use `3 × polling_interval` rounded up (so a 15-minute polling service gets a 45-minute timeout). Ask the user for the polling interval explicitly — don't guess. If the customer reports long export processing times or a queue-backlog behavior on the service, bias the multiplier up to `4×` or `5×` and note the rationale in the scope statement.

  **Distinguish "still pending" from "error".** When the timeout expires without an `exported` transition, check the annotation for error messages and audit logs before declaring failure. A clean timeout (no errors recorded) means the service hasn't picked up the document yet — different signal from a transition attempt that errored out. The report should differentiate these.
- **Pattern 3** → apply the right strategy per queue.

For Pattern 2, also ask whether the external service is pointed at the test environment at all — if it only polls production queues, the test queue won't be picked up and export verification is impossible without infrastructure changes. Confirm before starting Phase 3.

### 0e. Determine the test environment

The skill needs **two coexisting states**: a `source` running the old config (historical ground truth) and a `target` running the upgraded config (replay target). Preference order: separate sandbox org > sibling queue in same org > fresh org. **Never run replay against the source/prod queue itself.** When using a sibling queue, watch out for: hooks referenced by URL (must be duplicated, not re-referenced), MDH write ops hitting prod data, and external export services filtering by org/status rather than queue. A sibling queue can be stood up with the `rossum_duplicate_queue` MCP tool (deep-copies the schema and creates a fresh inbox — but hook attachments copied via `copy_extensions_settings` still point at the SAME hook objects, so the hook-duplication caveat above applies) and torn down afterwards with `rossum_delete_queue` (cascade removes its schema/inbox, skipping anything shared).

**If the upgrade is local-only**, push the upgrade's files to the target before Phase 3. Stage only the modified files (`git add <target-env-dir>/<files-from-UPGRADE-*.md>`), then `prd2 push <target-env> -io` (the `-io` flag pushes only indexed files). Gate the push call with a remote-write confirmation. See `prd-reference` for full `prd2` semantics.

### 0e2. Verify extraction-engine parity between source and target — **HARD GATE**

GET both queues and compare `dedicated_engine`, `generic_engine`, `engine`, `rir_url`, `rir_params`. If source and target use different extraction stacks, the same PDF will produce different captured values — every extracted-field diff becomes engine-attributable rather than upgrade-attributable, and "missing match" clusters can look like hook attachment problems when they're actually input drift.

If engines differ: either align them (attach the source's dedicated engine to the target queue) before running Phase 3, or proceed with the engine-mismatch warning surfaced at the top of the report. Phase 3 always runs in full-overwrite mode (every captured non-formula datapoint synthetically transferred from source) so the replay isolates upgrade-driven differences regardless.

### 0e3. Verify master-data freshness between source and target — **HARD GATE for MDH-touching upgrades**

If the upgrade modifies any MDH hook configuration OR any formula/rule that consumes MDH output, the source and target queues' MDH datasets must hold equivalent data — otherwise diffs in match outputs (`recipient_match`, `sender_match`, `vat_group_match`, `po_line_*_match`, etc.) will look like upgrade regressions but are actually data drift.

How to check (the user will need to do this with admin access; the skill should ask if it can't):

1. **Same dataset names** — read each MDH hook's `source.dataset` (with `{queue_country}` etc. resolved) on source and target. If suffixes differ (e.g., `_prod` vs `_uat`), confirm the rename history with the user. Watch for re-imports that recreate `_prod` collections after a `_prod → _uat` rename.
2. **Same row counts** — `data_storage_aggregate` with `[{"$match": {<filter>}}, {"$count": "total"}]` against the named collection in both envs. Acceptable drift: small absolute deltas (≤1%) per collection. Larger drift suggests stale UAT data.
3. **Spot-check a corpus member's anchor row.** Take a corpus document, find the row its prod snapshot matched against (e.g., for `recipient_match` look up by VAT ID), and compare the MDH-relevant fields (`Active Name`, `Inactive Name`, normalised name fields) between source and target datasets. Common drift patterns:
   - Different normalisation: `"Northwind Logistics B.V."` (with periods) in source vs `"Northwind Logistics BV"` (no periods) in target — exact-match queries will miss but fuzzy-match queries may still hit (see §4 below)
   - `null` vs `""` (empty string) for sentinel values — affects `$ne: ""` filters
   - Newer rows in source that haven't been re-exported to target
4. **Search index parity** — for any MDH query containing `$search` with a named `index` (e.g., `"index": "search_entities_v1"`), the named Atlas Search index must exist on the target collection. Use `data_storage_list_search_indexes` against both source and target collections and compare index names, field mappings, and analyzers. Missing or differently-named indexes cause `$search` stages to silently return no results → fuzzy-match queries fall through → match outputs end up empty. **This is the most common environment-clone drift cause and the hardest to diagnose** — the MDH hook log shows `status: completed` (no error), the data is present, but the result is empty because the index doesn't exist. Always check this when:
   - A corpus annotation's match outputs are empty in target but populated in source
   - Source's MDH config has any `$search` stage
   - Source's snapshot's `recipient_match_query_index` (or analogous) points to a fuzzy-match query (look at the query's `__matched_query_index` addFields stage, not the array index)
5. **Caveat on shared backends.** On the `elis.rossum.ai` cluster, multiple orgs may share a single MDH backend; collections aren't org-scoped. Querying `data_storage_*` from one org's token can return collections that belong to another org's MDH config. If `_prod` and `_uat` collections both exist, check which one each MDH hook references rather than assuming.

If freshness drift is detected, surface it in the scope statement: *"MDH dataset content drift between source and target — match-output diffs may be data-attributable rather than upgrade-attributable."* Offer to refresh the target's `_uat` datasets from current source data before running Phase 3.

### 0f. Identify always-divergent formulas (auto-out-of-scope)

Grep new formulas for identity-bearing references that will always differ between source and target: `field.annotation_id` / annotation URL embeds, hard-coded queue/schema/org IDs, `now()` / current-date builtins, env-specific endpoints. Common targets include `sf_rossum_document_link`, `annotation_id`, `desc_start`. Add these schema_ids to the scope statement's **out-of-scope formula targets** list — Phase 4 suppresses them from hot-surface scoring.

### 0g. Scope output

Produce a scope statement listing: what changed, which layers apply, change classifications, out-of-scope changes (including the always-divergent formula list from 0f), export architecture, target environment (and confirmation that the upgraded config is deployed there, not just local), target corpus size. Create a task list with one task per applicable phase.

---

## Phase 1: Static Validation

**Goal:** Catch config-level breaks before spending API calls on replay. No API calls needed.

**Run:** `python3 scripts/static_validate.py --impl-root <upgraded-config-root> [--source-root <baseline-root>] --out .test-runs/<ts>/phase1-static.json` — single batch over the whole tree.

**Always pass `--source-root` for upgrade tests** — it activates the formula-preservation check (1g) that catches the most insidious migration regression class: a field that was a formula in source becomes plain `data` in target with no `formulas/<sid>.py`, so the field silently stops being computed. Without 1g this defect surfaces only as an empty-value cluster in Phase 4, easy to misclassify as "formula didn't fire on this annotation" rather than "formula doesn't exist at all". Skip 1g only when there is no baseline (e.g. validating a fresh implementation that has no upgrade history).

The sections below describe what the script checks, so the LLM can interpret violations and decide whether to gate Phase 2.

### 1a. Schema reference check

For each new formula file and each modified hook, find `field.<name>` references. A reference resolves if the name exists **as the `id` of any node in the schema tree** — including datapoint, multivalue (line_items, tax_details, etc.), tuple, and section nodes. Do not restrict to datapoints only; formulas legitimately access multivalue containers via `field.line_items.all_values` and similar patterns.

Common defects this catches:
- **Typos** — `field.ditem_ate_prepaid_start` instead of `field.item_date_prepaid_start`. Bulk migration scripts produce these in batches across many queues simultaneously.
- **Dangling references** — formulas that reference a field the upgrade removed.
- **Cross-queue schema drift** — a formula copied from a queue that defines the field to a queue that doesn't.

### 1b. Formula constraints

For each formula `.py` file:
- Under 2000 characters
- Parses as valid Python
- No `return` at the top level
- No import of HTTP libraries (formulas can't make network calls)
- No self-reference (formula must not read `field.<this_field>`)

### 1c. Hook chain integrity

For each `queue.json`:
- Every hook URL in the chain references a hook file that exists
- `run_after` references resolve to valid hook URLs
- No circular `run_after` cycles
- Deprecated hook templates (Copy & Paste, Find & Replace, Value Mapping, Date Calculation) are fully removed from the chain or marked `active: false`

### 1d. MDH references

If the `rossum-api` MCP tools are available:
- Every collection name in matching configs exists (`data_storage_list_collections`)
- Every field used in `$match`, `$sort`, or `$search` has a supporting index

### 1e. Rule references

Every field ID referenced by a rule exists in the schema. Every rule URL attached to a queue resolves to a rule file that exists.

### 1g. Formula preservation across migration (requires `--source-root`)

Match queues between source and target by name (the `<name>_[<id>]` directory basename). For each queue present in both trees, walk the source schema and find every field where `ui_configuration.type == "formula"` AND a non-empty `formula` body exists. For each such field:

- If the same `id` is missing from the target schema → field was removed deliberately; not a regression here.
- If the same `id` exists in target as `ui_configuration.type == "formula"` AND has either an inline `formula` or a `formulas/<sid>.py` file → preserved correctly.
- If the same `id` exists but `ui_configuration.type` is no longer `"formula"` AND no `formulas/<sid>.py` exists → **`1g_formula_dropped` regression**: the field will silently stop computing.
- If `ui_configuration.type == "formula"` in target but no inline body and no `formulas/<sid>.py` → **`1g_formula_body_missing` regression**: schema declares it as formula but the body is gone.

The script emits one violation per (queue, field) pair, including the **full source formula body** in the violation entry — so a fix script (or the AI agent) can write the correct `formulas/<sid>.py` directly from the violation report.

**Why this check matters:** the existing per-queue checks (1a-1c, 1e) cannot detect this defect class. There is no broken reference — the field simply doesn't have a formula. The migration just dropped the formula and demoted the field type. Without 1g the regression surfaces only as Phase 4 empty-value clusters, which are easy to misclassify as input-drift cascades or as "formula didn't fire on this annotation" rather than "formula doesn't exist at all". Measured on a real migration (2026-05-04): 90 instances across 21 fields × 7 queues; missed by Phase 1 in the original run, surfaced only after a manual schema diff against prodbackup.

### 1f. Hard gate on failure

**If any Error-severity check fails, Phase 1 blocks.** Do not proceed to Phase 2 until the user has fixed the defects or explicitly overridden with `--skip-static`. Building a corpus and standing up a test queue is wasted work if the implementation has broken references — the replay itself will fail in ways that mask the real defects.

**Artifact:** Static validation results, pass/fail per check, with file paths and grouped by defect pattern. Attached to the final report.

---

## Phase 2: Build Test Corpus

**Goal:** A set of annotation IDs to replay, stored outside the project tree, with "before" snapshots.

### 2a. Select annotations

If the user passed `--corpus=<file>`, use that list. Otherwise, stratify-select from the production queue's recent history:

- **N automated annotations** (status: exported, automated without user intervention) — covers the happy path
- **N manually reviewed annotations** (status: confirmed or later, with user edits) — covers edge cases
- **Per-vendor sampling** if MDH matching is in scope — one per top-10 vendors by volume
- **Force-include** any annotation that historically triggered each business rule the upgrade touches

Default N = 20 per stratum (tune via `--corpus-size`). Pull via `rossum_search_annotations` or `rossum_list_annotations`.

### 2b. Per-queue coverage rule

For projects with more than one queue in scope, **at minimum one automated annotation and one manually reviewed annotation must be selected per queue**, independent of the per-stratum counts. For projects with more than 5 queues, this per-queue floor replaces the flat N as the dominant constraint — a flat 20-per-stratum corpus cannot cover 23 regional queues. At scale, target a corpus of roughly `queues_in_scope × (5 to 15)` annotations.

Force-include edge cases specific to each queue:
- Any annotation that triggered a business rule the upgrade touched in that queue
- At least one line-item-heavy annotation (≥10 line items) per queue when line-item formulas changed
- Region/variant-specific cases — when the upgrade has region-scoped hooks or formulas (e.g., country-specific regulatory logic, currency-specific rounding), force-include an annotation per regional variant

### 2c. Capture before-snapshots

For each selected annotation, fetch its final state from the source env. The bundled scripts handle this:

```
python3 scripts/capture_snapshots.py --corpus .test-runs/<ts>/corpus.json --token <SOURCE_TOKEN> --out-dir .test-runs/<ts>/before/
python3 scripts/normalize_snapshot.py --dir .test-runs/<ts>/before/
```

Records every field value, validation_source, message, automation_blocker, final status, and export signal where available. Migration-trace extraction from deactivated hook `"//"` comments stays per-customer-specific and is left to the LLM (the patterns vary).

### 2d. Store out-of-repo

Snapshots go outside the project tree. Default: `$PROJECT_ROOT/.test-runs/<timestamp>/` (add `.test-runs/` to `.gitignore`). Fall back to `~/.rossum-test-runs/<project>/<timestamp>/` if the project directory is read-only.

See the **File naming conventions** block at the top of this skill for the exact directory layout the bundled scripts expect (`before/<id>.{annotation,content,document,blocker,normalized}.json`, `after/<id>.{annotation,content,blocker,normalized}.json` keyed by source/target annotation IDs respectively).

**Artifact:** Corpus file + before-snapshots on disk.

---

## Phase 3: Replay

**Goal:** Produce an "after" snapshot per corpus annotation by running the same document through the upgraded implementation.

### 3a. Default mode — Confirmed-annotation replay

For each corpus annotation, run this sequence:

1. **Fetch the document.** Use `rossum_get_document` to get the source document file.
2. **Upload to the test queue.** Post the document via the Rossum upload API. Wait for OCR and `started` / `initialize` hook events. **Confirm with user before the first upload — this is a write op.**
3. **Synthesise prod state on the test annotation.** Apply a full-overwrite patch plan: every captured non-formula datapoint from the source snapshot is transferred to the target annotation, with multivalue row counts reconciled. Each replace op carries the full `{value, page, position}` triple — sending value-only strips the bounding box, leaving the field "ungrounded" in the UI. The bundled scripts handle this end-to-end:

   ```
   python3 scripts/build_patch_plan.py \
     --prod-normalized .test-runs/<ts>/before/<source_aid>.normalized.json \
     --uat-content .test-runs/<ts>/after/<target_aid>.content.json \
     --target-schema <path-to-target-queue-schema.json> \
     --emit-row-ops \
     --out .test-runs/<ts>/patch-plans/<source_aid>.json

   python3 scripts/apply_patch_plan.py \
     --plan .test-runs/<ts>/patch-plans/<source_aid>.json \
     --target-aid <target_annotation_id> \
     --token <TARGET_TOKEN> --base-url <TARGET_BASE_URL>
   ```

   The skill encodes four hard-won API quirks. Implementations that bypass `apply_patch_plan.py` must reproduce all of them:

   - **Row ops** (`remove` / `add_empty_tuple`) → `POST /annotations/<id>/content/operations`. The bulk endpoint is the only path that mutates row counts; per-datapoint PATCH cannot.
   - **Replace ops** → `PATCH /annotations/<id>/content/<dp_id>` with body `{"content": {"value": "...", "page": N, "position": [...]}}`. **Do NOT use the bulk operations endpoint for replace ops** — it returns HTTP 200 but silently no-ops on enum-typed and `ui_configuration.type=manual` fields (e.g., `document_type`, `enforce_draft`). The per-datapoint endpoint works for every field type. `validation_sources` is NOT a PATCH-success signal — verify by reading back `content.value`.
   - **Two-phase apply with status toggle.** Neither PATCH endpoint fires hook events. The hook chain only re-runs on a status transition that lands on `to_review` (firing `annotation_content.started`). Apply ops in two phases: (a) pre-hook = fields with prod val_src ⊆ {`score`, `human`}, the inputs hooks consume; (b) status toggle `to_review → postponed → to_review`, wait ~20s for hooks to settle; (c) post-hook = fields with val_src containing `data_matching` / `rules` / `connector`, the hook outputs. Post-hook ops overwrite whatever the target's hooks just produced, locking the test on prod's ground truth. `build_patch_plan.py` tags each op with `_meta.phase` ∈ {`pre`, `post`}.
   - **Dedup auto-restore (defensive — customer-custom only).** Some customer queues have a **custom hook** on `annotation_content.initialize` that PATCHes `status: deleted` for re-uploaded duplicate documents. The **stock Rossum Duplicate Handling extension does NOT do this** — its valid actions are `fill_field`, `forward_annotation`, `mark_duplicate`, `show_message`, `stop_automation`, `apply_label`; none transition status. For customer-customized delete behavior, detect by GETting the annotation after OCR — if `status: deleted` despite a recent upload, PATCH `{"status": "to_review"}` to restore (dedup does not re-fire on status change). `apply_patch_plan.py` does this in pre-flight.

   **Filter formula-typed targets** in `build_patch_plan.py` via `--target-schema`. Fields promoted to formula-type in the target schema cannot be PATCHed (HTTP 400 "The computed datapoint X can only be updated from UI."). Without the flag, the API will reject the whole batch on the first formula target.

   **Position-copy assumption:** the same PDF bytes render identically across envs, so source coordinates remain valid in the target annotation. This holds for normal uploads; it can break if the target env re-renders or pre-processes the PDF differently. If positions drift visibly in the test UI, fall back to `value`-only PATCH and note it in the report.
4. **Confirm the annotation.** POST the confirm action. This fires `confirmed` hooks and triggers the export pipeline if configured.
5. **Verify export** using the strategy determined in Phase 0d:
   - **Pattern 1 (in-Rossum hook):** capture the export hook's output payload and any response-parsing results.
   - **Pattern 2 (external service):** poll the annotation status with a timeout (sized per Phase 0d — typically 3× the polling interval). Record: did `confirmed` → `exported` transition happen, how long it took, any error messages attached to the annotation, any relevant audit log entries, and whether a clean timeout vs. an errored transition occurred. **Do not capture a payload — there isn't one visible from Rossum.**
   - **Pattern 3 (hybrid):** apply the right strategy per queue.
6. **Capture the after-snapshot.** Same shape as the before-snapshot: field values, messages, automation_blockers, status, and the export signal appropriate to the architecture.
7. **Store** under `after/<annotation_id>.json`.

### Side-effect policy

Before running replay, explicitly configure and **confirm with the user**:

- **MDH calls** — point to a frozen test dataset, or use the prod dataset if queries are read-only. Never mutate prod MDH collections during replay.
- **External API calls (export endpoints, notification webhooks)** — mock, point at a test endpoint, or skip with a flag. Never fire real export calls to prod target systems during replay. This applies equally to Pattern 1 (export hook) and Pattern 2 (external service pointed at the test queue).
- **Email sends** — disable in the test queue's configuration or stub.

**Artifact:** `after/` directory populated with after-snapshots.

---

## Phase 4: Diff & Report

**Goal:** Compare before vs. after, group failures by root cause, and produce the test report.

**Run:**
```
python3 scripts/normalize_snapshot.py --dir .test-runs/<ts>/after/
python3 scripts/diff_phase4.py \
  --before-dir .test-runs/<ts>/before/ \
  --after-dir .test-runs/<ts>/after/ \
  --mapping .test-runs/<ts>/replay-mapping.json \
  --config .test-runs/<ts>/diff-config.json \
  --out .test-runs/<ts>/phase4-diff.json
```
The `diff-config.json` is authored by the LLM in Phase 0/2 and captures upgrade-specific judgment (`hot_fields`, `out_of_scope`, `structural_new`, `structural_removed`, `ocr_drift_paths`, `mode`). The script applies the classification ladder mechanically; the LLM interprets the resulting clusters and writes the report narrative.

### 4a. Per-annotation diff

For each annotation, compare historical before-snapshot vs. after-snapshot:

- **Field values** — always compare **byte-exact** per `(schema_id, path, row_index)`. Do not collapse formatting differences into matches; they get classified (see below), not hidden.
- **Messages** — match by (field, content, severity). **When Axis "hook → native rules" is in scope, treat message differences as first-class diffs, not soft-fail.**
- **Automation blockers** — match by `(schema_id, type)` first, then message content. **First-class diff result** — a missing or added automation_blocker is always a hard-fail unless explicitly marked out-of-scope.
- **Export signal** — depending on Phase 0d:
  - **Pattern 1:** diff the export payload (JSON deep-diff, XML semantic diff, or byte-for-byte depending on format).
  - **Pattern 2:** compare status transition (did both reach `exported`), time-to-export (within a tolerance window), and presence of errors.
- **Final status** — exact match (confirmed, exported, rejected, etc.)
- **Automation-downgrade** — prod annotation was `automated=true` but test annotation stayed in `to_review` / has a new blocker. **First-class diff result** — hard-fail regardless of field parity, because the business outcome (auto-processing rate) changed.

**Normalization is for severity classification, never for hiding diffs.** The rule:

1. **Start byte-exact.** If two values are byte-identical → pass, no diff reported.
2. **If they differ, the diff is always reported.** Every byte-difference appears in the per-annotation table and the grouped-failures section — the user must be able to see what changed to judge whether downstream consumers care (Coupa XML schemas, SFTP fixed-width fields, regex validators, accounting systems' strict formatters).
3. **Normalization only decides severity.** Compute a normalized form of each side using the spec below. If the normalized forms match but the raw bytes don't → class `numeric_formatting` or `locale_formatting` (soft-fail). If normalized forms also differ → apply the rest of the classification ladder (hard-fail territory).

**Normalization spec (severity-only, not match-decider):**
- Strip leading/trailing whitespace including non-breaking spaces (`\u00a0`).
- If both sides parse as numeric: compare `Decimal` values; thousands separators in `[" ", "\u00a0", ",", "."]` stripped after detecting the decimal separator.
- Drop trailing zeros from decimal fractions for the normalized comparison only (raw values preserved in the report).
- `None`, `""`, and `"null"` are equivalent for presence checks.
- Case-sensitive otherwise.

Do not apply normalization during Phase 3 replay — the PATCH sends the exact historical value, and any downstream format sensitivity is part of the behavior being tested.

**Diff classification ladder (apply top-down, first match wins):**

| Class | Severity | Rule |
|-------|----------|------|
| `annotation_identity` | out-of-scope | schema_id is on the Phase 0f always-divergent list |
| `structural_expected` | out-of-scope | schema_id is in the declared structural_new / structural_removed sets |
| `formula_crash` | **P0 critical** | blocker message contains `Traceback`, `Exception`, or `TypeError` |
| `automation_downgrade` | hard-fail | prod `automated=true` and test has any new blocker |
| `hot_surface_value` | hard-fail | schema_id in the "fields to pin" list from Phase 0 and normalized forms also differ |
| `blocker_missing` | hard-fail | prod had a blocker of type `extension` or `error_message` that the test does not reproduce |
| `blocker_new` | hard-fail if prod was automated, else soft-fail | test has a new blocker absent in prod |
| `numeric_formatting` | soft-fail | raw bytes differ but normalized values match (whitespace/separator/trailing-zero only) — diff still reported |
| `message_wording` | soft-fail | same schema_id, same blocker type, only message text differs |
| `field_value` | hard-fail | fallback for value diffs not absorbed above (raw bytes differ AND normalized forms differ) |

Classify each annotation's overall verdict as **pass** (no hard-fails), **soft-fail** (soft-fails only), or **hard-fail** (≥1 hard-fail or P0).

### 4b. Group failures (cross-corpus clustering)

Cluster hard-fails by `schema_id` (or `(schema_id, row_class)` for multivalue fields). For each cluster, emit:

- **Fan-out:** "N/K corpus annotations show this diff" — this is the signal that distinguishes a systemic regression from a one-off edge case.
- **Affected annotation IDs.**
- **Example values:** min 2 before/after pairs, including at least one that shows the largest absolute delta (for numeric) or the clearest string difference.
- **Likely root cause:** cross-reference the change manifest and the migration-trace mapping from Phase 0a — the F## or PM## source-ID is often available. Name the formula file or rule when you can.
- **Suggested fix.**

P0 formula crashes get their own cluster at the top of the report regardless of fan-out — a crash affecting 1/K is still a bug.

**Input-drift vs output-bug heuristic.** Before flagging an empty-output cluster as a missing hook/attachment problem, check whether the upstream input field also diffs at similar fan-out. If `po_line_*_match` is empty 9/10 AND `order_id` also diffs 9/10 in the same annotations, the matcher is receiving wrong input — classify as `input_drift_cascade` and demote severity. If inputs are byte-equal but outputs are empty, the hook/formula is broken — keep hard-fail.

### 4c. Coverage analysis

For each modified hook/formula/rule in the change manifest, record whether at least one corpus annotation exercised it (via hook logs or formula recompute logs during replay). Uncovered changes are risks — flag them prominently.

**Severity escalation.** If an observed regression cluster strongly suggests an *uncovered* path is also broken (e.g., price/quantity look swapped on non-blanket POs, which hints blanket-PO swap logic may misfire but the corpus has no blanket-PO case), elevate that coverage gap to **P0 — untestable with current corpus, likely broken**. Recommend expanding the corpus before promoting.

### 4d. Automation parity

Compute before/after:
- Automation rate
- Per-blocker-reason counts
- Per-message counts

A shift in any of these is a signal even if no individual field failed.

### 4e. Write the report

Write `TEST-[customer-or-folder-name]-[timestamp].md`. **Location is a choice, not a default:**
- In-project (committed alongside the implementation) if the customer expects test reports in the repo.
- `$PROJECT_ROOT/.test-runs/<timestamp>/TEST-*.md` co-located with the snapshot if reports should be ignored by git.
- An external outputs folder for ad-hoc exploration.

Ask the user if the target isn't obvious from context.

Report template:

```markdown
# Test Report: [Customer/Project Name]

**Test run:** [timestamp]
**Scope:** [one sentence describing what was tested]
**Export architecture:** [Pattern 1 / Pattern 2 / Hybrid — from Phase 0d]
**Verdict:** **Pass** / **Pass-with-warnings** / **Fail**

## Summary

- Corpus size: N annotations (A automated, M manually reviewed, spread across K queues)
- Pass / soft-fail / hard-fail: X / Y / Z
- Automation rate: before A% → after B% (Δ)
- Out-of-scope changes (documented, not diffed): [list]

## Static Validation

| Check | Result | Details |
|---|---|---|

## Equivalence Results

| Annotation ID | Queue | Verdict | Field diffs | Message diffs | Blocker diffs | Export signal | Link |
|---|---|---|---|---|---|---|---|

## Grouped Failures

### P0 — Formula crashes (if any)

| Annotation | Schema ID | Message |
|---|---|---|

### Group 1: [schema_id or pattern]
- Fan-out: N/K corpus annotations
- Affected annotations: [list]
- Example diffs: [2-3 before/after pairs]
- Likely cause: [cross-ref to change manifest; include migration-trace ID if available]
- Suggested fix: [...]

(Repeat per group)

## Coverage

| Change | Exercised by corpus? | Severity if uncovered |
|---|---|---|

Uncovered changes (risk-ranked): [list, elevate to P0 any gap where observed regressions suggest the uncovered path is likely broken]

## Automation Parity

| Metric | Before | After | Delta |
|---|---|---|---|

## Manual Sign-off Items

- [ ] ...
```

**Artifact:** `TEST-*.md` report.

---

## Important

- **Never replay against a production queue or mutate prod MDH.** Always a dedicated test queue/sandbox; MDH queries read-only.
- **Phase 0 hard gates:** engine parity (0e2), MDH dataset + Atlas Search index parity (0e3). Skipping these has produced full-of-noise reports historically — they're the single biggest determinant of a useful test.
- **Phase 1 with `--source-root`** is the only way to catch `1g_formula_dropped` regressions. The intra-tree checks (1a-c, 1e) cannot see this defect class. Always pass both `--impl-root` (target) and `--source-root` (baseline) for upgrade tests.
- **Use `apply_patch_plan.py`.** It encodes four API quirks that the bulk operations endpoint trips on: per-datapoint PATCH for replace ops (bulk silently no-ops on enums), two-phase apply with status toggle (PATCH endpoints don't fire hooks), formula-target filtering, and defensive dedup auto-restore for customer-custom delete hooks (the stock Duplicate Handling extension does not delete, so this typically does not fire). Implementing PATCH manually is rarely worth it.
- **Hook outputs are unstable in target.** Any later event that lands on `to_review` (UI session opening, status flip) re-fires hooks and may overwrite the prod-truth match values. Capture after-snapshots immediately after `apply_patch_plan.py` returns; don't open the annotation in the UI until cleanup.
- **Confirm export architecture (Pattern 1 in-Rossum hook vs Pattern 2 external service) in Phase 0d.** Mis-classification means Phase 3 silently checks for the wrong signal.
- **Don't invent diffs.** If a check can't run (side-effect policy not configured, external service not pointed at test queue, etc.), say so in the report rather than silently skipping.
- **Corpus quality over quantity** — 20 well-stratified annotations usually beat 200 random ones. For multi-queue projects, the per-queue floor (2b) takes precedence over per-stratum counts.
- **When in doubt, confirm.** Every write op against a remote environment passes through the hard-gate.
