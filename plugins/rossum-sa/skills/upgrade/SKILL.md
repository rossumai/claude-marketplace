---
name: upgrade
description: Upgrade deprecated Rossum extensions to modern equivalents and bump old Python runtimes on function hooks to python3.12. Finds old Copy & Paste, Find & Replace, Value Mapping, and Date Calculation extensions and produces replacement formula fields with migration steps. Use when modernizing a customer implementation. Triggers on requests like "upgrade extensions", "migrate to formulas", "replace deprecated hooks", "upgrade python runtime", "modernize this setup".
argument-hint: [path-to-implementation]
allowed-tools: Read, Grep, Glob, Bash, Agent
context: fork
---

# Upgrade Rossum Implementation

You are a Rossum.ai Solution Architect upgrading a customer's implementation from deprecated extensions to modern formula fields and bringing serverless function hooks onto the current Python runtime.

> Path or context: $ARGUMENTS

**The output of this skill is the upgrade manifest** (`UPGRADE-<project>-<date>.yaml` + rendered `.md`) **plus the in-place migration-trace breadcrumbs.** Downstream skills (`test-behavioral-equivalence`, `analyze`, `review`) read these to know what changed and how to verify it. See:

- [manifest.md](manifest.md) — manifest schema and validation rules.
- [migration-trace.md](migration-trace.md) — the `//`-comment and `migration-trace:` header convention, plus the grammar the `test-behavioral-equivalence` skill's parser expects.

## Scope

This upgrade covers **value transformations, date calculations, and Python runtime versions** — extensions that copy, transform, map field values, compute dates, plus any function hook pinned to an outdated Python runtime:

| Deprecated Extension / Runtime | Replacement | Why |
|-------------------------------|-------------|-----|
| Copy & Paste Values | Formula field | Extension is deprecated and no longer maintained |
| Find & Replace Values | Formula field | Extension is deprecated and no longer maintained |
| Value Mapping | Formula field | Formula fields are simpler, faster, and version-controlled |
| Date Calculation | Formula field | Extension is deprecated and no longer maintained |
| Function hook `config.runtime` = `python3.8` / `python3.9` / `python3.10` / `python3.11` | `python3.12` | Older runtimes are deprecated on the Rossum platform |

## Phase 1: Find Deprecated Extensions

Use the provided path (or current directory if none given). Refer to `skills/__shared/discovery-checklist.md` for glob patterns.

1. Find all hook JSON files: `**/hooks/*.json`
2. Identify deprecated extensions by **grepping hook files** for these patterns:
   - Names containing: `Copy`, `Paste`, `Find`, `Replace`, `Value Mapping`, `Mapping`, `Date Calculation`, `Date Calc`
   - Hook URLs containing: `copy-paste`, `find-replace`, `value-mapping`, `date-calculation`
3. Identify outdated Python runtimes on function hooks by **grepping hook files** for `"runtime":` and collecting every value that is not `"python3.12"` (e.g., `python3.8`, `python3.9`, `python3.10`, `python3.11`). Only hooks of `"type": "function"` have a runtime field — webhook/connector hooks do not.
4. For each match, read the **full hook JSON** — especially `settings` (the transformation rules) and `queues` (which queues use it). For function hooks, also read the corresponding `.py` file(s) so you can flag any code that won't run on 3.12.
5. Find the **schema** for each affected queue (`**/schema.json` in the matching queue directory) so you know what fields exist

Do NOT produce output during this phase. Read everything first.

## Phase 2: Understand Each Extension

For each deprecated extension found, extract the transformation logic from its `settings`:

### Copy & Paste Values

Settings typically contain source-to-target field mappings with optional conditions. Look for keys like `mappings`, `operations`, `source`, `target`, `conditions`. Each mapping copies a value from one schema field to another, optionally gated by a condition (e.g., "only if target is empty").

### Find & Replace Values

Settings contain regex-based find-and-replace operations. Look for keys like `operations`, `field_id`/`field`, `pattern`/`find`, `replacement`/`replace`, `flags`. Each operation applies `re.sub()` to a field value.

### Value Mapping

Settings contain value-to-value mappings. Look for keys like `mappings`, `source`, `target`, `mapping`/`values`, `default`. Each mapping translates discrete values in one field to corresponding values in another field.

### Date Calculation

Settings contain a `calculations` array. Each calculation has:
- `expression`: a string like `{date_issue} + timedelta(days=30)` where `{field_id}` references schema fields and `timedelta(days=N)` adds/subtracts time. The `timedelta` parameter can be a literal integer or a schema field reference (e.g., `timedelta(days={terms})`).
- `target_field`: the schema field ID to write the computed date to.
- `condition` (optional): a string expression that gates the calculation (e.g., `{sender_name} == 'Milk Company'`).

All fields referenced in expressions (except `timedelta` parameters) must be of type `date` in the schema. Calculations are evaluated in order — later entries can override earlier ones for the same `target_field` (useful for conditional overrides).

### Numbered Export Webhook Chain

**Symptom.** Several webhook hooks on the export event whose names carry an ordinal prefix — `1. Export - ...`, `3. API1 - Create draft`, `5. API 2 - File append`, `9. API 4 - Submit` — chained with `run_after`, usually with a separate "extract response" hook after each call. The ordinal in the name is the only statement of intended order.

**Modern equivalent.** One Request Processor hook whose `settings.stages` array holds the same calls in order; `response_handlers` absorb the response-extraction hooks entirely. Measured on one real implementation: an eleven-hook chain became one hook with four stages. See `export-pipeline-reference` → One Hook, Not a Chain.

**This is opportunistic, not urgent.** A working chain is not deprecated and does not need emergency migration — rewriting a stable production export carries real risk for no functional gain. Propose it when the export is being substantially reworked anyway, or when the chain is already causing ordering bugs. If the project is *adding* an export, the new one should be a Request Processor regardless.

**Before proposing, check the one blocker:** a chain may rely on a formula field being computed between hooks, which a single hook cannot reproduce (formulas evaluate only after the hook completes). Each such value must become a `property` carried between stages. If you cannot account for every mid-chain formula dependency, say so and stop rather than proposing a migration that will silently drop a value.

Record as `export_pipeline_migration` in the manifest `axes`, and set `export_architecture.pattern` accordingly. Not behavior-preserving by default — the response handling is re-expressed, so it needs verification against a real annotation (see "Verify the upgrade against an annotation" below, and the `iterate` skill's export re-fire for the loop).

### Python Runtime (function hooks)

For each function hook with `config.runtime` older than `python3.12`, note:

- The current runtime value and the hook's name/ID.
- The hook's code source: typically a sibling `.py` file managed by `prd2` (preferred) or, as a fallback, the inline `config.code` string.
- Whether the code uses anything removed or meaningfully changed in Python 3.12:
  - `distutils` (removed in 3.12) — replace with `packaging`, `shutil`, or `sysconfig`.
  - `imp` (removed in 3.12) — replace with `importlib`.
  - `asynchat`, `asyncore`, `smtpd` (removed in 3.12).
  - `collections.Callable`/`collections.Mapping`/etc. aliases (removed in 3.10) — import from `collections.abc`.
  - Implicit `datetime.utcnow()` usage — still works but is deprecated; prefer `datetime.now(timezone.utc)`.
  - `ssl.wrap_socket` (removed in 3.12) — use `SSLContext.wrap_socket`.
  - Any third-party dependency pinned in the hook that requires an older Python.

If the code uses none of the above, the runtime bump is a pure `config.runtime` change with no code edits required.

## Phase 3: Leave Migration-Trace Breadcrumbs

For **every** migration you execute, leave two breadcrumbs — one on the old artifact, one on the new — so the `test-behavioral-equivalence` skill can join regressions back to the original logic without fuzzy field-name matching. Full grammar in [migration-trace.md](migration-trace.md); the essentials:

### Forward traces (on the deprecated artifact)

**Do not delete** a deprecated hook whose logic has been migrated. Keep the JSON in the repo with `"active": false` and leave every row in its settings list intact. Add a `"//"` sibling key to each migrated row:

```json
{
  "//": "C07 -> F05 on schema 1234567 (DE). Cond: has_value({amount_paid})",
  "output": "amount_due",
  "value": "{amount_total} - {amount_paid}"
}
```

The legacy ID prefix encodes the source type: `C` (Calculations), `D` (Date Calculations), `PM` (Post-Match Calculations), `CP` (Copy & Paste), `FR` (Find & Replace), `VM` (Value Mapping), `VO` (Value Operations). New-ID prefixes: `F` (formula field), `R` (native rule), `H` (new hook). Numbering is local to the source artifact.

Suffix tokens, all optional: `(<target_name>)`, `on schema <id> (<label>)`, `Migrated on all N schemas.`, `Cond: <expr>`, `Note: <free-form>`.

A `//` value that has no `->` (e.g. `"C00: OrderAmountLine default"`) is a descriptive-only comment, **not** a migration — the parser will ignore it. Rows in an active hook use this form.

### Backward traces (on the new artifact)

Every formula, rule, or hook you create gets a header pointing back at the legacy ID(s).

Formula `.py` file:

```python
# migration-trace: F05 <- C05,C06,C07,C08,C09 (Calculations hook 1000001)
# Outstanding amount: total minus paid, fallback to total when unpaid.
field.amount_total - field.amount_paid if field.amount_paid else field.amount_total
```

Native rule: prepend the trace to the `description` field:

```json
{
  "description": "migration-trace: R02 <- C11 (Business Rules Validation hook 1000003). Original blocker kept."
}
```

### Intentional behavior changes

If the new artifact deliberately does something different (e.g. a rounding bug fix), add the token `INTENTIONAL-CHANGE` to the trace line. The `test-behavioral-equivalence` skill down-ranks matches with this token from regression to expected delta:

```
C14 -> F08 on all schemas. INTENTIONAL-CHANGE: old rule rounded half-up, new rounds banker's. Cond: unconditional
```

### Breadcrumb self-check

After writing the breadcrumbs, mentally run the grammar from [migration-trace.md](migration-trace.md) over every `"//"` you added and every `# migration-trace:` header you wrote. Every forward trace must match the forward regex; every backward trace must match the backward regex. If any do not, fix the breadcrumb — downstream skills parse these directly from the prd2 tree and a malformed line is silently dropped.

## Phase 4: Produce the Upgrade Manifest

The manifest is the skill's **output contract**. Downstream skills (`test-behavioral-equivalence`, `analyze`, `review`) read it instead of re-diffing prd2 pulls. Produce two files, stored in the project root (next to `prd_config.yaml` if prd2 is used):

1. **`UPGRADE-<project>-<yyyy-mm-dd>.yaml`** — machine-readable source of truth.
2. **`UPGRADE-<project>-<yyyy-mm-dd>.md`** — human-readable rendering of the YAML. The narrative `summary`, the `axes` table, and the `risk` ranking go here. No information lives only in the markdown.

One manifest per upgrade operation. Later upgrades emit new manifests — they do not amend earlier ones.

### Required content

Follow [manifest.md](manifest.md) for the full spec. The YAML must contain:

- `meta` — `upgrade_id`, `project`, `date`, `authored_by`, `source_env`, `target_env`, `summary`. `prd2_commit_before`/`prd2_commit_after` SHOULD be set.
- `scope` — `queues` (every source-env queue, with `touched` flag and `reason`), `hooks_disabled`, `hooks_removed`, `hooks_added`, `hooks_modified`, `schemas_modified`, `rules_added`, `rules_removed`, plus `out_of_scope_changes` for any drift surfaced by the diff that is unrelated to this upgrade.
- `axes` — taxonomy of *what kind* of upgrade this is (`hook_to_formula`, `hook_to_rule`, `hook_refactor`, `hook_disable_dead_code`, `schema_field_*`, `rule_consolidation`, `export_pipeline_migration`, `mdh_matching_change`, `engine_swap`, `intentional_behavior_change`). Each axis carries a `behavior_preserving` boolean and a count. For this skill's two use cases:
  - Deprecated-extension replacements → `hook_to_formula` (with `behavior_preserving: true` if the formula reproduces the extension exactly).
  - Python runtime bumps with no code changes → `hook_refactor` (with `behavior_preserving: true`). If code had to change for 3.12 compatibility, still `hook_refactor` but call out the edits in `summary`.
- `migration_traces` — `convention_version` (`1.0`) and `coverage` (totals for forward/backward traces and any untraced artifacts). Downstream skills parse breadcrumbs directly from the prd2 tree; the manifest only records what the upgrade intended to write.
- `test_hints` — `fields_to_pin` (the field IDs replaced by new formulas — these must produce identical output before/after), `per_queue_coverage_floor`, `known_edge_cases`, `automation_expectation` (default `preserve` for pure upgrades), plus `rules_expected_to_fire` if any hook→rule migration happened.
- `export_architecture` — `pattern` is required. If the project has no export at all yet, still state the pattern explicitly; do not default silently.
- `risk` — one entry per touched queue, `low` / `medium` / `high` with a reason.

### Validation before emitting

The skill must self-check:

1. Every ID in `scope.hooks_disabled` exists in `target_env` with `active: false`.
2. Every ID in `scope.hooks_removed` does **not** exist in `target_env`. Prefer `hooks_disabled` over `hooks_removed` — disable preserves the trace.
3. Every queue with `touched: true` is explained by either an axis entry or an `out_of_scope_changes` note.
4. `migration_traces.coverage.total_migrations == forward_traces + untraced`.
5. If any axis has `behavior_preserving: false`, `meta.summary` names the intentional change.
6. `export_architecture.pattern` is set; no silent default.

If any check fails, fix the data — do not emit the manifest.

### Rendered markdown structure

The `.md` file is generated from the `.yaml` and must contain, at minimum:

1. **Title** — `# Upgrade: <project> (<date>)`
2. **Summary** — the verbatim `meta.summary` paragraph.
3. **Axes table** — `axis | count | behavior_preserving | description`.
4. **Scope section** — the queue list (touched vs. not) and the hook/schema/rule ID lists.
5. **Extensions to Upgrade** — for each deprecated extension being replaced:
   - Extension name, affected queues, one-sentence description of current behavior.
   - The target formula field ID and the `formulas/<id>.py` body (literal code, including the `# migration-trace: F<n> <- ...` header).
   - Migration steps: add/change schema field → write `.py` with backward trace → write forward `//` traces on the old hook rows → test parity → remove extension from queue hook chains → set the old hook `"active": false` (**do not delete** — the forward traces must stay greppable).
6. **Python Runtime Upgrades** — for each function hook with `config.runtime` < `python3.12`:
   - Hook name + ID, current → target runtime, affected queues.
   - Code-compatibility line: either "no changes required" or a bullet list of 3.12 incompatibilities to rewrite.
   - Migration steps: edit `.py` if needed → change `config.runtime` → `prd2 push` → trigger on a representative document.
7. **Out-of-scope changes** — the `out_of_scope_changes` list (so reviewers don't chase them as regressions).
8. **Risk ranking** — the `risk` list, rendered as a table or bulleted list.
9. **Migration checklist** — standard boxes:
   - [ ] All formula fields added to schemas.
   - [ ] All formula `.py` files created with `# migration-trace: F<n> <- ...` headers.
   - [ ] All forward `"//"` traces written on the old hook rows.
   - [ ] All deprecated extensions removed from queue hook chains and their hooks set to `"active": false` (not deleted).
   - [ ] All function hooks on outdated runtimes bumped to `python3.12`.
   - [ ] Any code incompatible with 3.12 rewritten in the hook's `.py` file.
   - [ ] Tested in dev/sandbox before promoting to production.

## Verify the upgrade against an annotation

A migration is not done when the manifest is written — it is done when a replacement formula/hook produces the **same value** the deprecated extension did, on a real document. Whenever you finish migrating an extension or bumping a runtime, **proactively offer to verify it** rather than waiting to be asked:

> Want to verify these replacements against a real annotation? Paste a **sandbox/UAT** annotation ID (or say "skip").

- Single document, tight inner loop ("did formula `F05` resolve to the same value the old hook produced?") → hand off to the `iterate` skill. Always use a **sandbox/UAT** annotation — never production; the loop re-fires (and may confirm) the document.
- Whole population, before promoting ("did anything regress across the corpus?") → hand off to `test-behavioral-equivalence`, which uses the `# migration-trace:` breadcrumbs to attribute any diffs back to the migration that caused them.

## Formula Field Rules

When writing replacement formulas, follow these constraints:

- **Max 2000 characters** per formula file. If the logic is too complex, split across multiple formula fields or use a serverless function instead.
- **No HTTP requests** — formulas cannot call external services.
- **No self-reference** — a formula field must never read its own value (circular reference error).
- **No return statements** — the last expression evaluated is the output.
- **Extensions cannot overwrite formula values** — if another extension needs to modify the same field, use a separate "data" type field as an intermediary.
- **Line items**: formulas on line item fields execute per-row. Use `field.<name>.all_values` to aggregate across rows.

### Replacement Patterns

Every generated formula file must start with a `# migration-trace: F<n> <- <LEGACY_ID>[,...] (<source artifact name and id>)` header. The snippets below omit it for brevity; add it verbatim when you emit real files.

**Copy & Paste → Formula:**
```python
# Simple copy
field.source_field

# Conditional copy (only if target would otherwise be empty)
field.source_field if not field.target_field else field.target_field

# Copy with transformation
field.source_field.upper()
```

**Find & Replace → Formula:**
```python
import re
# Remove non-alphanumeric characters
re.sub(r'[^A-Za-z0-9]', '', str(field.original_field))

# Normalize whitespace
re.sub(r'\s+', ' ', str(field.original_field)).strip()
```

**Value Mapping → Formula:**
```python
# Direct mapping with fallback to original value
{
    "DE": "Germany",
    "AT": "Austria",
    "CZ": "Czech Republic",
}.get(field.country_code, field.country_code)

# Mapping with default
{
    "INV": "Invoice",
    "CN": "Credit Note",
}.get(field.document_type, "Other")
```

**Date Calculation → Formula:**
```python
from datetime import datetime, timedelta

# Fixed offset: date_issue + 30 days
datetime.strptime(field.date_issue, "%Y-%m-%d") + timedelta(days=30) if field.date_issue else None

# Dynamic offset from another field (e.g., payment terms)
datetime.strptime(field.date_issue, "%Y-%m-%d") + timedelta(days=int(field.terms)) if field.date_issue and field.terms else None

# Conditional: different offset based on a field value
(
    datetime.strptime(field.date_issue, "%Y-%m-%d") + timedelta(days=14)
    if field.sender_name == "Milk Company"
    else datetime.strptime(field.date_issue, "%Y-%m-%d") + timedelta(days=30)
) if field.date_issue else None
```

## Important

- Only upgrade extensions and runtimes you actually find. Do not invent issues.
- If an extension's settings are too complex for a formula (>2000 chars or requires HTTP), note it as "requires serverless function" and skip the formula generation.
- When multiple queues share the same extension, produce one formula per queue (they may need slight variations if schemas differ).
- Preserve the exact transformation logic — the formula must produce identical results to the extension it replaces.
- For runtime bumps, do not edit the inline `code` field or rewrite logic you don't need to rewrite. Bump the `runtime` string and only touch the `.py` source when a specific 3.12 incompatibility requires it.
- **Disable, don't delete.** A deprecated hook whose logic has been migrated stays in the repo with `"active": false`; its forward `"//"` traces are what `test-behavioral-equivalence` uses to attribute regressions. Removing the hook deletes the trace. If a hook was genuinely never used (dead code, no migration target), that's a different axis — `hook_disable_dead_code` — and is also disable-not-delete.
- **Every migration needs two breadcrumbs.** Forward (`"//"` on the old row) and backward (`# migration-trace:` header on the new formula, or `description:` prefix on the new rule). Missing either direction is treated by `test-behavioral-equivalence` as an untraced migration and degrades its triage to fuzzy name matching.
- **Do not skip the manifest.** The `.yaml` is the output contract; the `.md` is an auto-rendering of it. If the upgrade is too small to justify a manifest, question whether the upgrade is worth doing at all — the downstream skills assume the manifest is present.
