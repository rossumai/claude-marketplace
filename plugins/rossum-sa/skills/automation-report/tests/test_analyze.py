"""Tests for the automation-report helper script (analyze.py).

All expected values are hand-computed from the fixtures — see fixture design notes
in each test. The script is the single source of numbers for the report (no prose
arithmetic), so these tests pin its arithmetic exactly.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("automation_analyze", SKILL_DIR / "analyze.py")
analyze_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze_mod)


@pytest.fixture(scope="module")
def insights():
    return json.loads((FIXTURES / "insights_only.json").read_text())


@pytest.fixture(scope="module")
def projections():
    return json.loads((FIXTURES / "projections.json").read_text())


@pytest.fixture(scope="module")
def insights_only_result(insights):
    return analyze_mod.analyze(insights, None)


@pytest.fixture(scope="module")
def full_result(insights, projections):
    return analyze_mod.analyze(insights, projections)


# --- window / volume ---


def test_window_and_volume(insights_only_result):
    window = insights_only_result["window"]
    assert window["days"] == 10
    assert window["active_days"] == 8  # 2 zero-volume days excluded
    assert window["total_documents"] == 80
    assert window["automated_documents"] == 8
    assert window["monthly_run_rate"] == 240  # 80 docs / 10 days * 30


# --- 1. blocker taxonomy ---


def test_taxonomy_buckets_keep_remedies_separate(insights_only_result):
    taxonomy = insights_only_result["taxonomy"]
    assert taxonomy["tunable"]["distinct_documents"] == 78
    assert taxonomy["tunable"]["share_of_documents"] == pytest.approx(0.975)
    assert taxonomy["structural"]["distinct_documents"] == 30
    assert taxonomy["structural"]["share_of_documents"] == pytest.approx(0.375)
    # rules sums per-blocker counts (a document with both error_message and
    # failed_checks counts twice) — the key must say so, not claim distinctness
    assert taxonomy["rules"]["document_count_sum"] == 20  # error_message datapoint
    assert taxonomy["rules"]["annotation_level_documents"] == 4
    assert "distinct_documents" not in taxonomy["rules"]
    assert taxonomy["settings"] == {"automation_disabled": 3, "suggested_edit_present": 2}


def test_per_field_taxonomy_table_sorted_by_total_blocked(insights_only_result):
    rows = insights_only_result["field_table"]
    assert [r["schema_id"] for r in rows] == [
        "sender_id",
        "delivery_date",
        "po_number",
        "amount_total",
    ]
    sender = rows[0]
    assert sender["tunable"] == 50
    assert sender["structural"] == 10
    assert sender["rules"] == 0
    assert sender["total_blocked"] == 60
    assert sender["share_of_documents"] == pytest.approx(0.75)  # 60/80
    po = rows[2]
    assert po["rules"] == 20
    assert po["tunable"] == 10


def test_unknown_datapoint_blockers_surface_in_other_bucket():
    # no_validation_sources was observed live on 70 queues across 3 orgs —
    # the analyzer must not silently drop blockers outside the known taxonomy.
    insights = {
        "document_automation_rate": 0.0,
        "document_touchless_rate": 0.0,
        "document_automation_timeseries": [
            {"date": "2026-05-01", "automated_count": 0, "non_automated_count": 10,
             "touchless_count": 0, "touched_count": 10}
        ],
        "document_blockers": [
            {"blocker": "no_validation_sources", "granularity": "datapoint",
             "document_count": 7, "example_annotation_ids": []}
        ],
        "datapoint_statistics": [
            {"schema_id": "iban", "blocked_document_counts": {"no_validation_sources": 7},
             "estimated_error_rate": None, "confidence_threshold": 0.95,
             "is_quality_estimate": False, "blockers": []}
        ],
        "estimated_error_rate_timeseries": [],
    }
    result = analyze_mod.analyze(insights, None)
    other = result["taxonomy"]["other"]
    assert other["document_count_sum"] == 7
    assert other["blockers"] == ["no_validation_sources"]
    assert result["field_table"][0]["other"] == 7
    # ...and the Markdown path must not silently drop the bucket
    md = analyze_mod._render_md(result)
    assert "no_validation_sources" in md
    assert "| Other |" in md  # per-field table column
    assert "| iban | 0.95 | 0 | 0 | 0 | 7 | 7 | 70.0% |" in md


# --- 2. untuned-threshold detection ---


def test_untuned_threshold_detection(insights_only_result):
    untuned = insights_only_result["threshold_calibration"]
    assert untuned["dominant_threshold"] == 0.95
    assert untuned["fields_at_dominant"] == 3
    assert untuned["field_count"] == 4
    assert untuned["share_fields_at_dominant"] == pytest.approx(0.75)
    assert untuned["low_score_document_share"] == pytest.approx(0.975)
    assert untuned["never_calibrated"] is True


# --- 3. touchless ceiling ---


def test_touchless_ceiling(insights_only_result):
    ceiling = insights_only_result["touchless_ceiling"]
    assert ceiling["touchless_rate"] == pytest.approx(0.3)
    assert ceiling["automation_rate"] == pytest.approx(0.1)
    assert ceiling["gap"] == pytest.approx(0.2)
    assert ceiling["unlocked_documents_per_month"] == pytest.approx(48)  # 0.2 * 240


# --- 4. overlap bounds for the top structural field ---


def test_overlap_bounds_inclusion_exclusion(insights_only_result):
    bounds = insights_only_result["structural_bounds"]
    # delivery_date carries 25 of the 30 distinct extension-blocked documents;
    # sender_id carries the other 10 — so 30-10=20..25 docs are solely delivery_date.
    assert bounds["schema_id"] == "delivery_date"
    assert bounds["field_document_count"] == 25
    assert bounds["distinct_documents"] == 30
    assert bounds["other_fields_sum"] == 10
    assert bounds["solely_blocked_min"] == 20
    assert bounds["solely_blocked_max"] == 25


# --- 5. diluted-rate correction (projections) ---


def test_active_window_rate_correction(full_result):
    scenarios = full_result["threshold_analysis"]["scenarios"]
    s0, s1 = scenarios
    assert s0["headline_automation_rate"] == pytest.approx(0.125)
    assert s0["active_window"]["days"] == 2
    assert s0["active_window"]["automated"] == 10
    assert s0["active_window"]["total"] == 20
    assert s0["active_window"]["corrected_automation_rate"] == pytest.approx(0.5)
    assert s1["active_window"]["corrected_automation_rate"] == pytest.approx(0.8)


def test_active_window_includes_zero_automation_days_after_cutover():
    # Filtering to automated-only days would bias the corrected rate upward:
    # a post-cutover day with volume but zero automation belongs in the
    # denominator. Day 1 (pre-cutover) excluded; days 2-3 both count.
    timeseries = [
        {"date": "2026-05-01", "automated_count": 0, "non_automated_count": 10,
         "touchless_count": 0, "touched_count": 10},
        {"date": "2026-05-02", "automated_count": 5, "non_automated_count": 5,
         "touchless_count": 5, "touched_count": 5},
        {"date": "2026-05-03", "automated_count": 0, "non_automated_count": 10,
         "touchless_count": 0, "touched_count": 10},
    ]
    active = analyze_mod._active_window(timeseries)
    assert active["days"] == 2
    assert active["automated"] == 5
    assert active["total"] == 20
    assert active["corrected_automation_rate"] == pytest.approx(0.25)


# --- 6. error economics + hybrid threshold proposal ---


def test_error_economics_per_scenario(full_result):
    s0, s1 = full_result["threshold_analysis"]["scenarios"]
    assert s0["errors_per_1000_automated"] == pytest.approx(1.0)
    # 240 docs/month * 0.5 corrected rate * 0.001 error rate
    assert s0["expected_erroneous_exports_per_month"] == pytest.approx(0.12)
    assert s1["errors_per_1000_automated"] == pytest.approx(4.0)
    assert s1["expected_erroneous_exports_per_month"] == pytest.approx(0.768)
    assert s0["risk_fields"] == ["sender_id"]
    assert sorted(s1["risk_fields"]) == ["amount_total", "sender_id"]


def test_scenario_timeseries_coverage_exposes_simulation_dilution(full_result):
    # On real data the projection timeseries covers only a fraction of the
    # insights window (~25.7% on the reference queue) even when
    # used_document_count == total_document_count — expose both totals.
    # The fixture encodes that asymmetry (20 of 80) so the two fields cannot
    # be wired to each other's source.
    analysis = full_result["threshold_analysis"]
    assert analysis["sampling"]["insights_window_documents"] == 80
    s0 = analysis["scenarios"][0]
    assert s0["timeseries_document_total"] == 20


def test_zero_error_rate_caveat_uses_rule_of_three_over_automated_trials(full_result):
    analysis = full_result["threshold_analysis"]
    assert analysis["sampling"]["used_document_count"] == 20
    assert analysis["sampling"]["total_document_count"] == 80
    assert analysis["sampling"]["sampling_fraction"] == pytest.approx(0.25)
    # The error rate is per AUTOMATED document, so only automated documents are
    # Bernoulli trials: trials = used * corrected rate. Scenario 0: 20 * 0.5 =
    # 10 trials → bound 3/10; scenario 1: 20 * 0.8 = 16 trials → 3/16.
    s0, s1 = analysis["scenarios"]
    assert s0["zero_error_upper_bound"] == pytest.approx(0.3)
    assert s1["zero_error_upper_bound"] == pytest.approx(0.1875)


def test_zero_error_bound_is_none_without_automated_trials(insights, projections):
    crippled = json.loads(json.dumps(projections))
    crippled["used_document_count"] = 0
    result = analyze_mod.analyze(insights, crippled)
    for scenario in result["threshold_analysis"]["scenarios"]:
        assert scenario["zero_error_upper_bound"] is None


def test_hybrid_threshold_proposal(full_result):
    hybrid = {row["schema_id"]: row for row in full_result["threshold_analysis"]["hybrid_proposal"]}
    # delivery_date never showed an error in any scenario → most aggressive threshold
    assert hybrid["delivery_date"]["proposed_threshold"] == pytest.approx(0.7)
    assert hybrid["delivery_date"]["stance"] == "aggressive"
    # sender_id carries error risk in both scenarios → keep the current threshold
    assert hybrid["sender_id"]["proposed_threshold"] == pytest.approx(0.95)
    assert hybrid["sender_id"]["stance"] == "conservative"
    # amount_total shows risk in scenario 1 → conservative
    assert hybrid["amount_total"]["proposed_threshold"] == pytest.approx(0.8)
    assert hybrid["amount_total"]["stance"] == "conservative"
    # po_number was never simulated → must not appear in the proposal
    assert "po_number" not in hybrid


# --- 7. workflow blind spot ---


def test_workflow_blind_spot_flags_unsimulated_fields(full_result):
    blind = full_result["threshold_analysis"]["unsimulated_fields"]
    assert [f["schema_id"] for f in blind] == ["po_number"]
    assert blind[0]["blocked_document_counts"] == {"error_message": 20, "low_score": 10}


# --- 8. insights-only degradation ---


def test_insights_only_mode_has_no_threshold_analysis(insights_only_result):
    assert insights_only_result["mode"] == "insights_only"
    assert insights_only_result["threshold_analysis"] is None
    unknowns = " ".join(insights_only_result["unknowns"])
    assert "threshold" in unknowns
    assert "error rate" in unknowns


def test_unavailable_projections_payload_treated_as_missing(insights):
    unavailable = json.loads((FIXTURES / "projections_unavailable.json").read_text())
    result = analyze_mod.analyze(insights, unavailable)
    assert result["mode"] == "insights_only"
    assert result["threshold_analysis"] is None
    assert "no projection scenarios" in result["projections_unavailable_reason"]


def test_full_mode_marker(full_result):
    assert full_result["mode"] == "insights_with_projections"


# --- 9. annotation sampling targets ---


def test_sampling_targets_top_error_message_and_extension_fields(insights_only_result):
    targets = insights_only_result["annotation_sampling_targets"]
    by_blocker = {t["blocker"]: t for t in targets}
    assert by_blocker["error_message"]["schema_id"] == "po_number"
    assert by_blocker["error_message"]["example_annotation_ids"] == [301, 302, 303, 304, 305]
    assert by_blocker["extension"]["schema_id"] == "delivery_date"
    assert len(by_blocker["extension"]["example_annotation_ids"]) == 10


# --- CLI ---


def test_cli_json_output_round_trips(tmp_path):
    out = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "analyze.py"),
            "--insights",
            str(FIXTURES / "insights_only.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["mode"] == "insights_only"


def test_cli_markdown_output_contains_key_numbers(tmp_path):
    out = subprocess.run(
        [
            sys.executable,
            str(SKILL_DIR / "analyze.py"),
            "--insights",
            str(FIXTURES / "insights_only.json"),
            "--projections",
            str(FIXTURES / "projections.json"),
            "--format",
            "md",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    md = out.stdout
    assert "| sender_id |" in md
    assert "97.5%" in md  # low_score document share
    assert "50.0%" in md  # corrected automation rate scenario 0
    assert "30.0%" in md  # scenario-0 zero-error upper bound (3/10 trials)
    # A document can be blocked in several buckets, so per-field shares can
    # exceed 100% — the table must carry that caveat.
    assert "can exceed 100%" in md
    # rules/other bucket rows are sums, not distinct documents
    assert "upper bounds" in md
