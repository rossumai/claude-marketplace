---
name: automation-report
description: Turn Rossum queue-level automation analytics into a detailed, actionable report with concrete recommendations. Uses automation_insights (available on every queue) and opportunistically upgrades the analysis when automation_projections responds. Use when diagnosing why automation is low or planning threshold calibration. Triggers on "automation report", "why is automation low", "analyze automation insights", "what blocks automation", "tune automation thresholds".
argument-hint: [queue-id-or-url]
allowed-tools: Read, Grep, Glob, Bash, Agent
context: fork
---

# Automation Report

Produce `AUTOMATION-REPORT-[queue-name-or-id].md` for one queue: where automation
stands today, what exactly blocks it (split by remedy), and a numbered list of
recommendations ordered by unlocked-document impact.

Follow `skills/__shared/verification-rules.md`: every claim grounded in fetched
data. **The Iron Rule: every number in the report comes from `analyze.py` output —
no prose arithmetic.** The script lives at
`${CLAUDE_PLUGIN_ROOT}/skills/automation-report/analyze.py`.

## Phase 1 — Gather (silently; no report output yet)

Requires the `rossum-api` MCP connection (`rossum_set_token` first if needed).
Resolve the queue ID from the argument (accepts a bare ID or any Rossum app URL
containing `/queues/<id>`).

1. **Insights (required).** Call `rossum_get_automation_insights` with the queue
   ID. The digest lands in context; the full payload is cached to
   `.rossum-cache/automation/queue_<id>_insights.json` (path in
   `full_payload_cache`). If this fails, stop — there is no report without it.
2. **Projections (optional, graceful).** Call
   `rossum_get_automation_projections`. Branch on the response:
   - `available: true` → full payload cached; the report gets threshold analysis.
   - `available: false` → record `status_code` + `reason` verbatim and continue.
     Common modes (live-observed): HTTP 200 with no scenarios (queue lacks
     reviewed documents), 404 (queue not visible to this token). Do **not**
     retry or treat as an error.
3. **Queue settings.** `rossum_get_queue` → the automation configuration lives
   in **top-level queue fields** (live-verified; there is no
   `settings.automation` object): `automation_enabled` (bool),
   `automation_level` (`never` / `confident` / `always`),
   `default_score_threshold`, plus `settings.suggested_edit`
   (`disable` / ...). `automation_level: never` directly explains an
   `automation_disabled` document blocker.
4. **Schema.** `rossum_get_schema` (schema ID is in the queue payload) → which
   fields exist, which are formula/manual fields. Workflow fields (e.g.
   `approval_name`, `sender_id_match`) can carry validation errors that
   projections never model.
5. **Compute.** Run the helper on the cached payloads:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/automation-report/analyze.py \
       --insights .rossum-cache/automation/queue_<id>_insights.json \
       [--projections .rossum-cache/automation/queue_<id>_projections.json] \
       --format md
   ```

   Run it twice: `--format md` for report-ready tables (paste them verbatim) and
   the default JSON for branching decisions. When projections were unavailable,
   pass the unavailable JSON response saved to a file, or omit `--projections` —
   both produce the insights-only analysis.
6. **Sample annotations.** The JSON output lists `annotation_sampling_targets`
   (top `error_message` field and top `extension` field with example annotation
   IDs). Open 5–10 of each via `rossum_get_annotation` — the resolved blocker
   items are under the `blocker.items` key of the compact response (each item:
   `{type, level, schema_id, content, details}`). Group them by field and root
   cause (e.g. PO formats failing a
   strict regex; supplier groups omitting delivery dates). These are the only
   per-annotation fetches — do not bulk-fetch all example IDs.

## Phase 2 — Interpret

The script encodes the methodology; your job is to interpret, not recompute:

- **Blocker taxonomy.** Present tunable (`low_score` — threshold calibration),
  structural (`extension` — field never extracted; only schema/requirement
  changes help), and rules (`error_message`, `failed_checks` — validation or
  matching logic) **separately**. Never present a single mixed "docs with
  blockers" number — the Rossum dashboard does, and it inverts priorities.
- **Untuned thresholds.** If `threshold_calibration.never_calibrated` is true,
  state explicitly that the configuration was never calibrated and frame any
  threshold change as a *first calibration*, not a loosening.
- **Touchless ceiling.** `touchless_ceiling.gap` is the opportunity: the
  empirical upper bound minus what automation delivers today.
- **Structural bounds.** Use `structural_bounds` (inclusion–exclusion) when
  recommending make-optional / formula-default for the top extension field —
  attach the `solely_blocked_min`–`solely_blocked_max` range.
- **Diluted rates (projections).** Quote `corrected_automation_rate` from the
  active window, not the headline — the headline averages over pre-simulation
  history and can understate the steady state several-fold. Mention the
  `timeseries_document_total` vs `sampling.insights_window_documents` dilution.
- **Error economics (projections).** Per scenario: `errors_per_1000_automated`
  and `expected_erroneous_exports_per_month`. Present the `hybrid_proposal`
  (aggressive thresholds for zero-error fields, conservative for the risk
  carriers — typically identity/VAT fields, where a wrong value propagating to
  an ERP costs most). Always quote each scenario's `zero_error_upper_bound`:
  a measured 0% over N automated documents means "below ~3/N", not zero (the
  trials are the documents the scenario automates, not the whole sample).
- **Workflow blind spot.** `unsimulated_fields` were outside the simulation —
  projected rates are upper bounds until these are confirmed non-blocking.
- **Insights-only degradation.** When projections are unavailable the report
  still delivers the taxonomy, ceiling, structural recommendations with bounds,
  validation-rule findings with example annotations, and queue-setting findings
  (automation level, blocker toggles, the small `automation_disabled` /
  `suggested_edit_present` counts). Include the script's `unknowns` as a
  "What we cannot know without projections" section. Do **not** fabricate
  threshold or error-rate numbers from a single-threshold aggregate.

## Phase 3 — Write the report

`AUTOMATION-REPORT-[queue-name-or-id].md`, sections in this order:

1. **Executive summary** — rates, ceiling, one-line verdict.
2. **Current state diagnosis** — window, volume, automation level settings.
3. **Blocker taxonomy table** — field × bucket × count × % of docs (paste the
   script's per-field table).
4. **Structural blockers & bounds.**
5. **Threshold analysis** — scenarios, corrected rates, error economics, hybrid
   proposal — or the "cannot know without projections" section.
6. **Workflow-field caveats.**
7. **Numbered recommendations** ordered by unlocked-document impact, each with
   an effort class (config / schema change / extension work) and evidence links
   back to the tables.
8. **Appendix** — example annotation IDs per blocker and the root-cause notes
   from the sampled annotations.

Keep raw payloads out of chat context: summaries in context, full payloads stay
in `.rossum-cache/`.
