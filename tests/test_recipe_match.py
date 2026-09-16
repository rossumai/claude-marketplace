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

SPINE = {"recipe": "idp-spine", "shape": {"ingest": {"modes": ["email", "upload"]}},
         "intents": {"gating": [{"id": "A1"}]}}

PROFILE = {
    "profile": "coupa",
    "name_class": "coupa",
    "integration_shape": "https_out",
    "coding_model": {"model": "segmented"},
    "packaging": {"transport": "legacy_rest_chain", "payload": "custom_format_template"},
    "md_inbound_roles": ["supplier", "purchase_order", "tax_code", "contract"],
}
PROFILES = [("coupa", PROFILE)]


def _record(**over):
    base = {
        "integration_shape": "https_out",
        "packaging": ["legacy_rest_chain", "custom_format_template"],
        "topology": {"queue_axis": ["country"], "queues": 12},
        "ingest": {"modes": ["email", "upload"]},
        "rules": {"total": 12},
        "hook_graph": {"nodes": [{"role": "x"}]},
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
    result = M.match(_record(), SPINE, PROFILES)
    assert result["score"] == 1.0
    assert result["verdict"] == "follows"
    assert result["findings"] == []


def test_unknown_target_needs_a_profile_not_a_new_recipe():
    """The skeleton is the same for every IDP delivery; only the target varies."""
    rec = _record(integration_shape="file_out",
                  target_profile={"name_class_candidates": ["sap"],
                                  "coding_model": {"model": "named"},
                                  "md_inbound_roles": [], "md_unclassified_refs": 0})
    result = M.match(rec, SPINE, PROFILES)
    assert result["verdict"] == "profile_missing"
    assert result["recipe"] == "idp-spine", "the spine still applies — this is an IDP delivery"
    assert "one file" in result["note"]


def test_a_tree_that_does_not_ingest_anything_is_not_idp():
    rec = _record(ingest={"modes": []}, topology={"queues": 0})
    result = M.match(rec, SPINE, PROFILES)
    assert result["verdict"] == "not_idp"
    assert "no ingest channel" in result["reasons"]


def test_core_role_gap_is_a_finding_optional_role_is_not():
    rec = _record()
    rec["target_profile"]["md_inbound_roles"] = ["supplier", "tax_code"]   # no PO, no contract
    result = M.match(rec, SPINE, PROFILES)
    kinds = {(d["kind"], d["detail"]) for d in result["findings"]}
    assert ("master_data_role_absent", "purchase_order") in kinds
    info = {d["detail"] for d in result["informational"]}
    assert "contract" in info, "a role the cluster uses but this tree does not is not a defect"


def test_missing_validation_layer_is_a_finding():
    result = M.match(_record(rules={"total": 0}), SPINE, PROFILES)
    assert any(d["kind"] == "no_validation_layer" for d in result["findings"])
    assert result["verdict"] == "follows_with_deviations"


def test_unclassified_role_count_qualifies_the_finding():
    """Role inference is heuristic; an absent role may just be a name the vocabulary lacks."""
    rec = _record()
    rec["target_profile"]["md_inbound_roles"] = ["supplier", "tax_code"]
    rec["target_profile"]["md_unclassified_refs"] = 9
    result = M.match(rec, SPINE, PROFILES)
    why = next(d["why"] for d in result["findings"] if d["detail"] == "purchase_order")
    assert "could not be assigned a role" in why


def test_partial_pull_is_flagged_so_the_comparison_is_not_trusted_blindly():
    result = M.match(_record(source_pin={"complete": False, "missing_objects": ["hook:1"]}),
                     SPINE, PROFILES)
    assert any(d["kind"] == "incomplete_pull" for d in result["findings"])
