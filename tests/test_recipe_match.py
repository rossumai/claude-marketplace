"""Unit tests for the recipe matcher's pure core.

The matcher is the read path: a wrong match is worse than no match, because it files a novel
structure under an existing recipe and loses the signal that a new variant exists. These pin that
behaviour on synthetic records — no filesystem, no network.
"""
from __future__ import annotations

import sys

import repo_lib as R

sys.path.insert(0, str(R.ROOT / "plugins" / "rossum-sa" / "recipes" / "tools"))

import recipe_match as M  # noqa: E402

RECIPE = {
    "shape": {
        "integration_shape": "https_out",
        "queue_axis": ["country"],
        "packaging": {"transport": "legacy_rest_chain", "payload": "custom_format_template"},
        "ingest": {"modes": ["email", "upload"]},
        "target_profile": {
            "name_class": "coupa",
            "coding_model": {"model": "segmented"},
            "md_inbound_roles": ["supplier", "purchase_order", "tax_code", "contract"],
        },
    },
    "intents": {"gating": [{"id": "A1"}]},
}


def _record(**over):
    base = {
        "integration_shape": "https_out",
        "packaging": ["legacy_rest_chain", "custom_format_template"],
        "topology": {"queue_axis": ["country"]},
        "ingest": {"modes": ["email", "upload"]},
        "rules": {"total": 12},
        "engines": {"total": 0},
        "structure": {"export_trigger": ["annotation_content.export"]},
        "source_pin": {"complete": True},
        "schema_field_families": [],
        "target_profile": {
            "name_class_candidates": ["coupa"],
            "coding_model": {"model": "segmented"},
            "md_inbound_roles": ["supplier", "purchase_order", "tax_code", "contract"],
            "md_unclassified_refs": 0,
        },
    }
    base.update(over)
    return base


def test_identical_shape_scores_one_and_follows():
    result = M.match(_record(), [("ap-invoice-to-coupa", RECIPE)])
    assert result["score"] == 1.0
    assert result["verdict"] == "follows"
    assert result["findings"] == []


def test_different_integration_shape_is_unclassified_not_forced():
    rec = _record(integration_shape="file_out",
                  target_profile={"name_class_candidates": ["sap"],
                                  "coding_model": {"model": "named"},
                                  "md_inbound_roles": [], "md_unclassified_refs": 0},
                  topology={"queue_axis": ["entity"]})
    result = M.match(rec, [("ap-invoice-to-coupa", RECIPE)])
    assert result["verdict"] == "unclassified", "a wrong match loses the new-variant signal"
    assert "candidate shape" in result["note"]


def test_core_role_gap_is_a_finding_optional_role_is_not():
    rec = _record()
    rec["target_profile"]["md_inbound_roles"] = ["supplier", "tax_code"]   # no PO, no contract
    result = M.match(rec, [("ap-invoice-to-coupa", RECIPE)])
    kinds = {(d["kind"], d["detail"]) for d in result["findings"]}
    assert ("master_data_role_absent", "purchase_order") in kinds
    info = {d["detail"] for d in result["informational"]}
    assert "contract" in info, "a role the cluster uses but this tree does not is not a defect"


def test_missing_validation_layer_is_a_finding():
    result = M.match(_record(rules={"total": 0}), [("ap-invoice-to-coupa", RECIPE)])
    assert any(d["kind"] == "no_validation_layer" for d in result["findings"])
    assert result["verdict"] == "follows_with_deviations"


def test_unclassified_role_count_qualifies_the_finding():
    """Role inference is heuristic; an absent role may just be a name the vocabulary lacks."""
    rec = _record()
    rec["target_profile"]["md_inbound_roles"] = ["supplier", "tax_code"]
    rec["target_profile"]["md_unclassified_refs"] = 9
    result = M.match(rec, [("ap-invoice-to-coupa", RECIPE)])
    why = next(d["why"] for d in result["findings"] if d["detail"] == "purchase_order")
    assert "could not be assigned a role" in why


def test_partial_pull_is_flagged_so_the_comparison_is_not_trusted_blindly():
    result = M.match(_record(source_pin={"complete": False, "missing_objects": ["hook:1"]}),
                     [("ap-invoice-to-coupa", RECIPE)])
    assert any(d["kind"] == "incomplete_pull" for d in result["findings"])
