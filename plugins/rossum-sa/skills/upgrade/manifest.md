# Upgrade Manifest — Requirements

The `upgrade` skill's output contract toward everything downstream (review, `test-behavioral-equivalence`, audit trail, humans). One file per upgrade, produced at the end of the upgrade phase, stored in the project root. The `test-behavioral-equivalence` skill reads it in Phase 0a to scope the run; the `analyze` skill can cross-reference it when auditing; a human reviewer uses the rendered markdown form as the change log.

## Identity and location

- **Filename:** `UPGRADE-<project>-<yyyy-mm-dd>.yaml` (machine-readable, source of truth) plus an auto-rendered `UPGRADE-<project>-<yyyy-mm-dd>.md` (human-readable).
- **Location:** project root. If the project uses prd2, place it next to `prd_config.yaml`.
- **One manifest per upgrade operation.** Additive: later upgrades produce new manifests, they do not amend earlier ones.

## Required sections

### 1. Metadata — `meta`

Identity of the upgrade itself. Test skill uses this to tag the test run.

| Key                   | Required | Type     | Notes                                                                 |
|-----------------------|----------|----------|-----------------------------------------------------------------------|
| `upgrade_id`          | ✅       | string   | `<project>-<yyyy-mm-dd>-<slug>`, e.g. `acme-2026-04-14-hooks-to-formulas`     |
| `project`             | ✅       | string   | Customer/project name.                                                |
| `date`                | ✅       | date     | ISO 8601. When the upgrade artifact was authored.                     |
| `authored_by`         | ✅       | string   | Human or agent identifier.                                            |
| `source_env`          | ✅       | string   | Env that held the *before* state (e.g. `prod`).                       |
| `target_env`          | ✅       | string   | Env that now holds the *after* state (e.g. `uat`).                    |
| `prd2_commit_before`  | ⚠️ SHOULD | string  | Git commit of the `source_env` pull. Anchors behavioral baseline.     |
| `prd2_commit_after`   | ⚠️ SHOULD | string  | Git commit of the `target_env` pull after the upgrade.                |
| `summary`             | ✅       | string   | One-paragraph human summary. Goes into the rendered markdown.         |

### 2. Scope — `scope`

What did and did not change. Test skill reads this to decide what to test and what to skip.

| Key                         | Required | Type      | Notes                                                                                     |
|-----------------------------|----------|-----------|-------------------------------------------------------------------------------------------|
| `queues`                    | ✅       | list      | Object per queue: `{id, name, workspace, touched: bool, reason: "upgraded" \| "regression-sample" \| "unchanged"}`. Every queue in scope of `source_env` must appear.  |
| `hooks_disabled`            | ✅       | list[str] | IDs of hooks flipped to `active: false`.                                                  |
| `hooks_removed`             | ✅       | list[str] | IDs of hooks deleted outright (discouraged — see migration-trace convention).             |
| `hooks_added`               | ✅       | list[str] | IDs of newly created hooks.                                                               |
| `hooks_modified`            | ✅       | list[str] | Existing hooks whose code or settings changed in a behavior-affecting way.                |
| `schemas_modified`          | ✅       | list[str] | Schema IDs whose datapoints/formulas/rules changed.                                       |
| `rules_added`               | ✅       | list[str] | New native-rule IDs.                                                                      |
| `rules_removed`             | ✅       | list[str] | Deleted native rules.                                                                     |
| `out_of_scope_changes`      | ⚠️ SHOULD | list     | Known drift unrelated to the upgrade but present in the diff (see §6).                    |

### 3. Change axes — `axes`

A high-level taxonomy of *what kind* of upgrade was performed. Test skill uses this to pick the right test strategy (static-only, replay, rule-parity, etc.).

Each item: `{axis, count, behavior_preserving, description}`.

**Canonical axes** (extend as new classes appear):

- `hook_to_formula` — deprecated hook logic replaced by formula fields.
- `hook_to_rule` — hook-fired messages/blockers replaced by native business rules.
- `hook_refactor` — hook kept, internals rewritten (e.g. Calculations v1 → TxScript).
- `hook_disable_dead_code` — hook was unreachable or unused, now disabled.
- `schema_field_rename` — field renamed; all consumers updated.
- `schema_field_remove` — field removed; all consumers updated.
- `schema_field_add` — new field introduced.
- `rule_consolidation` — multiple rules collapsed into one.
- `export_pipeline_migration` — Pipeline v1 → Request Processor or similar.
- `mdh_matching_change` — matching config or query cascade changed.
- `engine_swap` — dedicated engine switched.
- `intentional_behavior_change` — a bug fix or policy change that is explicitly not behavior-preserving.

`behavior_preserving` is a **per-axis boolean claim**. If any axis is `false`, the test-behavioral-equivalence skill treats the affected fields as expected-delta rather than regression.

### 4. Migration traces — `migration_traces`

The breadcrumbs themselves live in the prd2 tree: `"//"` forward traces on deprecated hook rows and `# migration-trace:` backward headers on new formulas/rules. The manifest records only the *summary* — downstream skills parse the breadcrumbs directly, not a precomputed JSON.

| Key                | Required | Type   | Notes                                                                                   |
|--------------------|----------|--------|-----------------------------------------------------------------------------------------|
| `convention_version` | ✅     | string | Version of the trace convention (currently `1.0`).                                      |
| `coverage`         | ⚠️ SHOULD | object | `{total_migrations: N, forward_traces: N, backward_traces: N, untraced: N}`. If `untraced > 0`, list the untraced artifacts. |

Test-skill behavior: if `coverage.untraced > 0`, it emits a warning during Phase 0a and proceeds with degraded triage (fuzzy field-name matching for those items only).

### 5. Test hints — `test_hints`

Explicit guidance from the upgrade skill to the test-behavioral-equivalence skill. Optional but high-value. Every hint here spares the test-behavioral-equivalence skill from re-discovering scope through diff alone.

| Key                         | Required | Type   | Notes                                                                                   |
|-----------------------------|----------|--------|-----------------------------------------------------------------------------------------|
| `corpus_recommendations`    | ⚠️ SHOULD | object | Per-axis and per-queue suggestions for sample selection (see below).                    |
| `per_queue_coverage_floor`  | ⚠️ SHOULD | int    | Minimum annotations per touched queue the test-behavioral-equivalence skill should aim for. Default 10.         |
| `known_edge_cases`          | ⚠️ SHOULD | list   | Scenarios that must be represented in the corpus (e.g. "credit notes", "charges > 0", "multi-currency", "blanket PO"). Each item `{label, filter_hint}`. |
| `fields_to_pin`             | ⚠️ SHOULD | list   | Field IDs whose output must be identical before/after — the "hot" regression surface.   |
| `fields_expected_to_change` | ⚠️ SHOULD | list   | Field IDs where deltas are expected and should be reported as info, not regression.     |
| `automation_expectation`    | ⚠️ SHOULD | enum   | `preserve` (same auto-confirm rate ±5%), `improve` (auto-rate should go up), `unchanged_not_guaranteed`. |
| `rules_expected_to_fire`    | ⚠️ SHOULD | list   | Rule IDs whose fire-rate should match before/after — especially for hook→rule migrations. |

`corpus_recommendations` shape:

```yaml
corpus_recommendations:
  by_axis:
    hook_to_formula:
      sample_per_touched_schema: 20
      must_include: ["credit_note", "po_matched", "po_unmatched"]
    hook_to_rule:
      sample_per_rule: 15
      must_include: ["rule_historically_fired", "rule_historically_passed"]
  by_queue:
    - queue_id: 1234567
      additional_samples: 30
      reason: "High-volume NL queue, complex charges logic"
```

### 6. Out-of-scope changes — `out_of_scope_changes`

Rossum projects accumulate incidental drift: manual fixes in production, one-off schema tweaks, ad-hoc hook edits. When the upgrade is authored against an older baseline, the diff picks this up too. List the drift explicitly so test doesn't flag it.

Each entry: `{kind, locator, note}`. Example:

```yaml
out_of_scope_changes:
  - kind: schema_field_add
    locator: schema:1234567:discount_code
    note: "Added by ops on 2026-03-10 for a specific vendor. Unrelated to this upgrade."
```

### 7. Export-architecture context — `export_architecture`

Explicit capture of the Phase 0d prompt from the test-behavioral-equivalence skill so the upgrade skill commits the answer rather than asking the user again.

| Key                    | Required | Type   | Notes                                                                         |
|------------------------|----------|--------|-------------------------------------------------------------------------------|
| `pattern`              | ✅       | enum   | `in_rossum_hook` \| `external_service_driven` \| `hybrid`.                     |
| `poller_interval_min`  | ⚠️ required if `external_service_driven` or `hybrid` | int | Polling interval of the external service. |
| `status_transition_api` | ⚠️ SHOULD | string | API or mechanism used by the external service to mark exported.              |
| `mock_endpoint_available` | ⚠️ SHOULD | bool | Whether the test env has a mock SFTP/API target, so replay can complete.     |
| `notes`                | ⚠️ SHOULD | string | Anything else the test-behavioral-equivalence skill needs (credentials sourcing, offline mode, etc.). |

### 8. Risk ranking — `risk`

Optional but recommended. Lets the test-behavioral-equivalence skill weight corpus size and severity.

Each touched queue gets a risk score `low` / `medium` / `high` with a reason:

```yaml
risk:
  - queue_id: 1234567
    level: high
    reason: "Touches 12 formulas converted from C07..C20; also migrates a line-totals aggregation hook."
  - queue_id: 1234568
    level: low
    reason: "Only one formula converted; no rule changes."
```

## Skeleton

```yaml
# UPGRADE-acme-2026-04-14-hooks-to-formulas.yaml
meta:
  upgrade_id: acme-2026-04-14-hooks-to-formulas
  project: acme
  date: 2026-04-14
  authored_by: "rossum-sa:upgrade (session X)"
  source_env: prod
  target_env: uat
  prd2_commit_before: a1b2c3d
  prd2_commit_after: d4e5f6a
  summary: |
    Migrated Calculations, Date Calculations, and Post-Match Calculations
    hooks to formula fields across all schemas. Replaced Business Rules
    Validation hook's value checks with native business rules. Removed
    dead Copy & Paste extensions from archive queues.

scope:
  queues:
    - {id: 1234567, name: "Invoices A", workspace: "Main", touched: true, reason: upgraded}
    - {id: 1234568, name: "Invoices B", workspace: "Main", touched: true, reason: upgraded}
    # ...
  hooks_disabled: [1000001, 1000002, 1000003, 1000004]
  hooks_removed: []
  hooks_added: []
  hooks_modified: [1000005]
  schemas_modified: [1234567, 1234568, 1234569]
  rules_added: [R02, R03, R04]
  rules_removed: []
  out_of_scope_changes:
    - {kind: schema_field_add, locator: "schema:1234567:discount_code",
       note: "Added by ops on 2026-03-10; unrelated."}

axes:
  - {axis: hook_to_formula,        count: 45, behavior_preserving: true,
     description: "C/D/PM rows migrated to F<n> formulas"}
  - {axis: hook_to_rule,           count: 3,  behavior_preserving: true,
     description: "Business Rules Validation value checks → native rules"}
  - {axis: hook_disable_dead_code, count: 17, behavior_preserving: true,
     description: "Copy & Paste extensions disabled on archive queues"}

migration_traces:
  convention_version: "1.0"
  coverage:
    total_migrations: 65
    forward_traces: 65
    backward_traces: 47
    untraced: 18
    untraced_artifacts:
      - {kind: formula, schema: 1234567, field: amount_total_base,
         reason: "backward header comment not yet emitted"}

test_hints:
  per_queue_coverage_floor: 10
  known_edge_cases:
    - {label: credit_note,       filter_hint: "document_type in ['credit_note','other_credit_note']"}
    - {label: po_matched,        filter_hint: "has matched purchase order"}
    - {label: po_unmatched,      filter_hint: "no PO match"}
    - {label: charges_present,   filter_hint: "len(charges) > 0"}
  fields_to_pin:
    [amount_total_base, amount_total_tax, item_amount_base]
  fields_expected_to_change: []
  automation_expectation: preserve
  rules_expected_to_fire: [R02, R03, R04]
  corpus_recommendations:
    by_axis:
      hook_to_formula:
        sample_per_touched_schema: 20
        must_include: [credit_note, charges_present]
      hook_to_rule:
        sample_per_rule: 15
        must_include: [rule_historically_fired, rule_historically_passed]

export_architecture:
  pattern: external_service_driven
  poller_interval_min: 15
  status_transition_api: "Rossum annotation status → exported via external service"
  mock_endpoint_available: false
  notes: |
    Target env shares the production export endpoint. Testing should
    rely on the "document reached confirmed and did not error" signal
    rather than expecting post-export verification.

risk:
  - {queue_id: 1234567, level: high,
     reason: "Primary schema drives C00..C30 migrations and has highest volume"}
  - {queue_id: 1234568, level: medium,
     reason: "Fewer formulas migrated; no rule changes"}
```

## Validation rules

The `upgrade` skill must self-check before emitting the manifest:

1. Every ID in `scope.hooks_disabled` exists in `target_env` with `active: false`.
2. Every ID in `scope.hooks_removed` does **not** exist in `target_env`. Prefer `hooks_disabled` over `hooks_removed` (preserves trace).
3. Every queue with `touched: true` has at least one entry in `axes` or an explicit `out_of_scope_changes` entry that justifies the diff.
4. `migration_traces.coverage.total_migrations == forward_traces + untraced` (forward must be complete modulo documented exceptions).
5. For every axis with `behavior_preserving: false`, the narrative `summary` explicitly calls out the intentional change.
6. `export_architecture.pattern` is set (no defaulting).

## What the `test-behavioral-equivalence` skill does with this

Phase 0a becomes deterministic:

```
read UPGRADE-*.yaml                → scope, axes, hints, export pattern, risk
walk prd2 tree for `"//"` + headers → forward + backward trace map
```

No more diffing source vs target to guess what changed. The manifest *is* the scope, and discovery-only discovery is reserved for manifests that are missing or incomplete (in which case Phase 0a falls back to the legacy diff-based scoping and logs a `MANIFEST_MISSING` warning in the report).

## What the `analyze` skill does with this

Running `analyze` against a project that has an `UPGRADE-*.yaml` lets it silence expected changes (the ones listed in the manifest) and focus findings on **unexpected** drift — which is exactly what a post-upgrade review should surface.

## What the `review` skill does with this

The rendered `UPGRADE-*.md` is the change-log entry for the upgrade PR. A human reviewer reads the summary, the axes table, and the risk ranking without having to diff prd2 pulls by hand.
