#!/usr/bin/env python3
"""Compute every number for a Rossum automation report from cached API payloads.

Consumes the full payloads cached by rossum_get_automation_insights /
rossum_get_automation_projections (.rossum-cache/automation/) and emits the
computed analysis as JSON (default) or report-ready Markdown tables. The skill
must take all figures from this script — no prose arithmetic.

Usage:
    python3 analyze.py --insights <insights.json> [--projections <projections.json>]
                       [--format json|md]

Stdlib-only, Python 3.12.
"""

import argparse
import json
import sys

# Blocker taxonomy: each bucket has a different remedy. Never merge them into a
# single "blocked documents" number — large tunable counts dwarf the smaller
# structural ones that are the real automation cap.
TUNABLE = "low_score"           # threshold calibration fixes it
STRUCTURAL = "extension"        # field never extracted; schema/requirement change needed
RULES = ("error_message", "failed_checks")  # validation / matching logic
SETTINGS = ("automation_disabled", "suggested_edit_present")  # queue configuration

# An untuned queue: nearly every field sits at one default threshold and nearly
# every document has at least one low_score field.
DOMINANT_THRESHOLD_SHARE = 0.7
LOW_SCORE_DOC_SHARE = 0.9


def _bucket(blocker):
    if blocker == TUNABLE:
        return "tunable"
    if blocker == STRUCTURAL:
        return "structural"
    if blocker in RULES:
        return "rules"
    return "other"


def _window(timeseries):
    total = sum((d.get("automated_count") or 0) + (d.get("non_automated_count") or 0)
                for d in timeseries)
    active = [d for d in timeseries
              if (d.get("automated_count") or 0) + (d.get("non_automated_count") or 0) > 0]
    days = len(timeseries)
    return {
        "start": timeseries[0]["date"] if timeseries else None,
        "end": timeseries[-1]["date"] if timeseries else None,
        "days": days,
        "active_days": len(active),
        "total_documents": total,
        "automated_documents": sum(d.get("automated_count") or 0 for d in timeseries),
        "touchless_documents": sum(d.get("touchless_count") or 0 for d in timeseries),
        "monthly_run_rate": round(total / days * 30) if days else 0,
    }


def _taxonomy(insights, total_documents):
    def share(count):
        return count / total_documents if total_documents else None

    doc_blockers = insights.get("document_blockers") or []
    by_key = {(b["blocker"], b["granularity"]): b["document_count"] for b in doc_blockers}
    taxonomy = {
        "tunable": {
            "distinct_documents": by_key.get((TUNABLE, "datapoint"), 0),
            "share_of_documents": share(by_key.get((TUNABLE, "datapoint"), 0)),
            "remedy": "threshold calibration",
        },
        "structural": {
            "distinct_documents": by_key.get((STRUCTURAL, "datapoint"), 0),
            "share_of_documents": share(by_key.get((STRUCTURAL, "datapoint"), 0)),
            "remedy": "schema/requirement change (field never extracted)",
        },
        "rules": {
            # Sum over error_message + failed_checks — a document carrying both
            # blockers counts twice, so this is an upper bound, not distinct docs.
            "document_count_sum": sum(
                by_key.get((b, "datapoint"), 0) for b in RULES
            ),
            "annotation_level_documents": sum(
                by_key.get((b, "annotation"), 0) for b in RULES
            ),
            "remedy": "validation or matching logic",
        },
        "settings": {
            blocker: by_key[(blocker, "annotation")]
            for blocker in SETTINGS
            if (blocker, "annotation") in by_key
        },
    }
    # Surface blockers outside the known taxonomy (e.g. no_validation_sources,
    # observed live) instead of silently dropping them.
    known = {TUNABLE, STRUCTURAL, *RULES, *SETTINGS}
    other = [b for b in doc_blockers if b["blocker"] not in known]
    taxonomy["other"] = {
        # Sum across blockers and granularities — an upper bound, not distinct docs.
        "document_count_sum": sum(b["document_count"] for b in other),
        "blockers": sorted({b["blocker"] for b in other}),
        "remedy": "inspect per blocker (outside the standard taxonomy)",
    }
    return taxonomy


def _field_table(insights, total_documents):
    rows = []
    for stat in insights.get("datapoint_statistics") or ():
        counts = stat.get("blocked_document_counts") or {}
        buckets = {"tunable": 0, "structural": 0, "rules": 0, "other": 0}
        for blocker, count in counts.items():
            buckets[_bucket(blocker)] += count
        total = sum(counts.values())
        rows.append({
            "schema_id": stat.get("schema_id"),
            "confidence_threshold": stat.get("confidence_threshold"),
            **buckets,
            "total_blocked": total,
            "share_of_documents": total / total_documents if total_documents else None,
        })
    rows.sort(key=lambda r: -r["total_blocked"])
    return rows


def _threshold_calibration(insights, total_documents):
    stats = insights.get("datapoint_statistics") or []
    thresholds = [s.get("confidence_threshold") for s in stats
                  if s.get("confidence_threshold") is not None]
    if not thresholds:
        return None
    dominant = max(set(thresholds), key=thresholds.count)
    at_dominant = thresholds.count(dominant)
    doc_blockers = insights.get("document_blockers") or []
    low_score_docs = next(
        (b["document_count"] for b in doc_blockers
         if b["blocker"] == TUNABLE and b["granularity"] == "datapoint"), 0)
    low_score_share = low_score_docs / total_documents if total_documents else None
    return {
        "dominant_threshold": dominant,
        "fields_at_dominant": at_dominant,
        "field_count": len(thresholds),
        "share_fields_at_dominant": at_dominant / len(thresholds),
        "low_score_document_share": low_score_share,
        "never_calibrated": (
            at_dominant / len(thresholds) >= DOMINANT_THRESHOLD_SHARE
            and (low_score_share or 0) >= LOW_SCORE_DOC_SHARE
        ),
    }


def _touchless_ceiling(insights, monthly_run_rate):
    touchless = insights.get("document_touchless_rate") or 0
    automation = insights.get("document_automation_rate") or 0
    gap = touchless - automation
    return {
        "touchless_rate": touchless,
        "automation_rate": automation,
        "gap": gap,
        "unlocked_documents_per_month": gap * monthly_run_rate,
    }


def _structural_bounds(insights):
    """Inclusion-exclusion bounds: how many documents does the top extension field
    block *solely*? min = distinct_total - sum(other fields), max = own count."""
    doc_blockers = insights.get("document_blockers") or []
    distinct = next(
        (b["document_count"] for b in doc_blockers
         if b["blocker"] == STRUCTURAL and b["granularity"] == "datapoint"), 0)
    per_field = []
    for stat in insights.get("datapoint_statistics") or ():
        count = (stat.get("blocked_document_counts") or {}).get(STRUCTURAL, 0)
        if count:
            per_field.append((stat["schema_id"], count))
    if not per_field or not distinct:
        return None
    per_field.sort(key=lambda pair: -pair[1])
    top_field, top_count = per_field[0]
    others = sum(count for _, count in per_field[1:])
    return {
        "schema_id": top_field,
        "field_document_count": top_count,
        "distinct_documents": distinct,
        "other_fields_sum": others,
        "solely_blocked_min": max(0, distinct - others),
        "solely_blocked_max": min(top_count, distinct),
    }


def _annotation_sampling_targets(insights):
    """Top error_message and top extension field with their example annotation IDs,
    for root-cause sampling via rossum_get_annotation."""
    best = {}
    for stat in insights.get("datapoint_statistics") or ():
        for blocker_item in stat.get("blockers") or ():
            blocker = blocker_item.get("blocker")
            if blocker not in ("error_message", STRUCTURAL):
                continue
            count = blocker_item.get("document_count") or 0
            key = "extension" if blocker == STRUCTURAL else blocker
            if key not in best or count > best[key]["document_count"]:
                best[key] = {
                    "blocker": key,
                    "schema_id": stat.get("schema_id"),
                    "document_count": count,
                    "example_annotation_ids": blocker_item.get("example_annotation_ids") or [],
                }
    return sorted(best.values(), key=lambda t: -t["document_count"])


def _active_window(timeseries):
    """Rate over the post-cutover window: everything from the first day with
    automated documents onward. Post-cutover days with volume but zero
    automation stay in the denominator — filtering to automated-only days
    would bias the corrected rate upward."""
    cutover = next(
        (i for i, d in enumerate(timeseries) if (d.get("automated_count") or 0) > 0),
        None,
    )
    window = timeseries[cutover:] if cutover is not None else []
    volume_days = [
        d for d in window
        if (d.get("automated_count") or 0) + (d.get("non_automated_count") or 0) > 0
    ]
    automated = sum(d.get("automated_count") or 0 for d in volume_days)
    total = sum((d.get("automated_count") or 0) + (d.get("non_automated_count") or 0)
                for d in volume_days)
    return {
        "days": len(volume_days),
        "automated": automated,
        "total": total,
        "corrected_automation_rate": automated / total if total else None,
    }


def _scenario_analysis(scenario, index, monthly_run_rate, used_document_count):
    error_rate = scenario.get("estimated_error_rate") or 0
    timeseries = scenario.get("document_automation_timeseries") or []
    active = _active_window(timeseries)
    corrected = active["corrected_automation_rate"] or 0
    risk_fields = sorted(
        s["schema_id"] for s in scenario.get("datapoint_statistics") or ()
        if (s.get("estimated_error_rate") or 0) > 0
    )
    # Rule of three over the documents this scenario actually automates: the
    # error rate is per AUTOMATED document, so trials = used docs x automation
    # rate, and 0 observed errors bound the true rate below ~3/trials (95%).
    trials = used_document_count * corrected
    return {
        "scenario": index,
        "headline_automation_rate": scenario.get("document_automation_rate"),
        "estimated_error_rate": scenario.get("estimated_error_rate"),
        "active_window": active,
        "zero_error_upper_bound": 3 / trials if trials else None,
        # On real data the simulation timeseries can cover far fewer documents
        # than the insights window — surface the totals so dilution is visible.
        "timeseries_document_total": round(sum(
            (d.get("automated_count") or 0) + (d.get("non_automated_count") or 0)
            for d in timeseries
        )),
        "errors_per_1000_automated": error_rate * 1000,
        "expected_erroneous_exports_per_month": monthly_run_rate * corrected * error_rate,
        "risk_fields": risk_fields,
    }


def _hybrid_proposal(scenarios, baseline_thresholds):
    """Aggressive thresholds for fields that never showed an error in any scenario,
    the current (baseline) threshold for fields carrying error risk."""
    fields = {}
    for scenario in scenarios:
        for stat in scenario.get("datapoint_statistics") or ():
            schema_id = stat["schema_id"]
            entry = fields.setdefault(
                schema_id, {"thresholds": [], "max_error_rate": 0.0})
            entry["thresholds"].append(stat.get("confidence_threshold"))
            entry["max_error_rate"] = max(
                entry["max_error_rate"], stat.get("estimated_error_rate") or 0)
    proposal = []
    for schema_id, entry in fields.items():
        risky = entry["max_error_rate"] > 0
        thresholds = [t for t in entry["thresholds"] if t is not None]
        proposal.append({
            "schema_id": schema_id,
            "stance": "conservative" if risky else "aggressive",
            "proposed_threshold": (
                baseline_thresholds.get(schema_id) if risky
                else (min(thresholds) if thresholds else None)
            ),
            "max_scenario_error_rate": entry["max_error_rate"],
        })
    proposal.sort(key=lambda row: row["schema_id"])
    return proposal


def _threshold_analysis(insights, projections, monthly_run_rate):
    baseline_thresholds = {
        s["schema_id"]: s.get("confidence_threshold")
        for s in insights.get("datapoint_statistics") or ()
    }
    used = projections.get("used_document_count") or 0
    scenarios_raw = projections.get("projections") or []
    scenarios = [
        _scenario_analysis(s, i, monthly_run_rate, used)
        for i, s in enumerate(scenarios_raw)
    ]
    simulated_fields = {
        stat["schema_id"]
        for scenario in scenarios_raw
        for stat in scenario.get("datapoint_statistics") or ()
    }
    unsimulated = [
        {
            "schema_id": stat["schema_id"],
            "blocked_document_counts": stat.get("blocked_document_counts") or {},
        }
        for stat in insights.get("datapoint_statistics") or ()
        if stat["schema_id"] not in simulated_fields
    ]
    total = projections.get("total_document_count") or 0
    insights_window_documents = sum(
        (d.get("automated_count") or 0) + (d.get("non_automated_count") or 0)
        for d in insights.get("document_automation_timeseries") or ()
    )
    return {
        "sampling": {
            "used_document_count": used,
            "total_document_count": total,
            "sampling_fraction": used / total if total else None,
            "insights_window_documents": insights_window_documents,
        },
        "scenarios": scenarios,
        "hybrid_proposal": _hybrid_proposal(scenarios_raw, baseline_thresholds),
        "unsimulated_fields": unsimulated,
    }


UNKNOWNS_WITHOUT_PROJECTIONS = [
    "no threshold-vs-precision curve: cannot say which confidence threshold "
    "achieves which automation rate",
    "no estimated error rate per field or per scenario: cannot quantify the "
    "risk of wrong values reaching the downstream system",
    "no hybrid threshold proposal: recommend enabling the Automation Assistant "
    "(automation_projections) and re-running this report when it responds",
]


def analyze(insights, projections=None):
    if projections is not None and (
        projections.get("available") is False or not projections.get("projections")
    ):
        reason = projections.get("reason") if isinstance(projections, dict) else None
        projections = None
    else:
        reason = None

    window = _window(insights.get("document_automation_timeseries") or [])
    total_documents = window["total_documents"]
    result = {
        "mode": "insights_with_projections" if projections else "insights_only",
        "window": window,
        "is_aurora_queue": insights.get("is_aurora_queue"),
        "taxonomy": _taxonomy(insights, total_documents),
        "field_table": _field_table(insights, total_documents),
        "threshold_calibration": _threshold_calibration(insights, total_documents),
        "touchless_ceiling": _touchless_ceiling(insights, window["monthly_run_rate"]),
        "structural_bounds": _structural_bounds(insights),
        "annotation_sampling_targets": _annotation_sampling_targets(insights),
        "threshold_analysis": (
            _threshold_analysis(insights, projections, window["monthly_run_rate"])
            if projections else None
        ),
    }
    if not projections:
        result["unknowns"] = UNKNOWNS_WITHOUT_PROJECTIONS
    if reason:
        result["projections_unavailable_reason"] = reason
    return result


# --- Markdown rendering ---


def _pct(value):
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _render_md(result):
    lines = []
    window = result["window"]
    lines.append("## Computed automation analysis\n")
    lines.append(
        f"Window: {window['start']} → {window['end']} ({window['days']} days, "
        f"{window['active_days']} active), {window['total_documents']} documents, "
        f"monthly run rate ≈ {window['monthly_run_rate']}.\n"
    )
    ceiling = result["touchless_ceiling"]
    lines.append(
        f"Automation rate {_pct(ceiling['automation_rate'])}, touchless ceiling "
        f"{_pct(ceiling['touchless_rate'])}, gap {_pct(ceiling['gap'])} "
        f"(≈ {ceiling['unlocked_documents_per_month']:.0f} documents/month).\n"
    )

    lines.append("### Blocker taxonomy\n")
    lines.append("| Bucket | Documents | Share | Remedy |")
    lines.append("|--------|-----------|-------|--------|")
    taxonomy = result["taxonomy"]
    for bucket in ("tunable", "structural", "rules", "other"):
        entry = taxonomy[bucket]
        count = entry.get("distinct_documents", entry.get("document_count_sum"))
        if bucket == "other" and not count:
            continue
        label = bucket
        if entry.get("blockers"):
            label = f"{bucket} ({', '.join(entry['blockers'])})"
        lines.append(
            f"| {label} | {count} | "
            f"{_pct(entry.get('share_of_documents'))} | {entry['remedy']} |"
        )
    lines.append(
        "\ntunable/structural are distinct documents; rules/other are sums "
        "across blockers and granularities — upper bounds, not distinct documents.\n"
    )
    if taxonomy["settings"]:
        settings = ", ".join(f"{k}: {v}" for k, v in taxonomy["settings"].items())
        lines.append(f"\nQueue-setting blockers (annotation level): {settings}.\n")

    calibration = result["threshold_calibration"]
    if calibration:
        lines.append("### Threshold calibration\n")
        lines.append(
            f"{calibration['fields_at_dominant']}/{calibration['field_count']} fields sit at "
            f"threshold {calibration['dominant_threshold']}; "
            f"{_pct(calibration['low_score_document_share'])} of documents have ≥1 "
            f"low-score field. Never calibrated: {calibration['never_calibrated']}.\n"
        )

    lines.append("### Per-field blocked documents\n")
    lines.append(
        "| Field | Threshold | Tunable | Structural | Rules | Other | Total | % of docs |"
    )
    lines.append(
        "|-------|-----------|---------|------------|-------|-------|-------|-----------|"
    )
    for row in result["field_table"]:
        lines.append(
            f"| {row['schema_id']} | {row['confidence_threshold']} | {row['tunable']} | "
            f"{row['structural']} | {row['rules']} | {row['other']} | "
            f"{row['total_blocked']} | {_pct(row['share_of_documents'])} |"
        )
    lines.append(
        "\nA document can be blocked in several buckets at once, so per-field "
        "shares can exceed 100%.\n"
    )

    bounds = result["structural_bounds"]
    if bounds:
        lines.append(
            f"\nTop structural field `{bounds['schema_id']}`: blocks "
            f"{bounds['field_document_count']} documents; "
            f"{bounds['solely_blocked_min']}–{bounds['solely_blocked_max']} of the "
            f"{bounds['distinct_documents']} extension-blocked documents are blocked "
            f"solely by this field.\n"
        )

    analysis = result["threshold_analysis"]
    if analysis:
        lines.append("### Threshold scenarios\n")
        sampling = analysis["sampling"]
        lines.append(
            f"Simulation sample: {sampling['used_document_count']} of "
            f"{sampling['total_document_count']} documents "
            f"({_pct(sampling['sampling_fraction'])}). The 0-error bound column is "
            f"the rule-of-three 95% upper bound over each scenario's automated "
            f"documents — a measured 0% error rate is not zero.\n"
        )
        lines.append(
            "| Scenario | Headline rate | Corrected rate (active window) | "
            "Errors /1000 automated | Expected errors /month | 0-error bound | "
            "Risk fields |"
        )
        lines.append("|----------|---------------|--------------------------------|"
                     "------------------------|------------------------|"
                     "---------------|-------------|")
        for s in analysis["scenarios"]:
            lines.append(
                f"| {s['scenario']} | {_pct(s['headline_automation_rate'])} | "
                f"{_pct(s['active_window']['corrected_automation_rate'])} | "
                f"{s['errors_per_1000_automated']:.1f} | "
                f"{s['expected_erroneous_exports_per_month']:.2f} | "
                f"~{_pct(s['zero_error_upper_bound'])} | "
                f"{', '.join(s['risk_fields']) or '—'} |"
            )
        lines.append("\n### Hybrid threshold proposal\n")
        lines.append("| Field | Stance | Proposed threshold | Max scenario error rate |")
        lines.append("|-------|--------|--------------------|-------------------------|")
        for row in analysis["hybrid_proposal"]:
            lines.append(
                f"| {row['schema_id']} | {row['stance']} | {row['proposed_threshold']} | "
                f"{row['max_scenario_error_rate']} |"
            )
        if analysis["unsimulated_fields"]:
            names = ", ".join(f["schema_id"] for f in analysis["unsimulated_fields"])
            lines.append(
                f"\nFields outside the simulation (projected rates are upper bounds "
                f"until these are confirmed non-blocking): {names}.\n"
            )
    else:
        lines.append("### What we cannot know without projections\n")
        for unknown in result["unknowns"]:
            lines.append(f"- {unknown}")
        if result.get("projections_unavailable_reason"):
            lines.append(
                f"\nProjections unavailable: {result['projections_unavailable_reason']}"
            )

    targets = result["annotation_sampling_targets"]
    if targets:
        lines.append("\n### Annotation sampling targets\n")
        for target in targets:
            ids = ", ".join(str(i) for i in target["example_annotation_ids"][:10])
            lines.append(
                f"- top `{target['blocker']}` field `{target['schema_id']}` "
                f"({target['document_count']} documents): example annotations {ids}"
            )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--insights", required=True, help="path to full insights payload")
    parser.add_argument("--projections", help="path to full projections payload (optional)")
    parser.add_argument("--format", choices=("json", "md"), default="json")
    args = parser.parse_args()

    with open(args.insights, encoding="utf-8") as f:
        insights = json.load(f)
    projections = None
    if args.projections:
        with open(args.projections, encoding="utf-8") as f:
            projections = json.load(f)

    result = analyze(insights, projections)
    if args.format == "md":
        sys.stdout.write(_render_md(result))
    else:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
