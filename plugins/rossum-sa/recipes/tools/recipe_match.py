#!/usr/bin/env python3
"""Match one pulled prd2 tree against the published recipes.

Read path for the recipe layer: shape record in, nearest recipe + deviations out, or
`unclassified` when nothing fits. Read-only, stdlib-only, no network.

    recipe_match.py --env <pulled-env> [--recipes <dir>] [--json out.json]

Every IDP delivery shares one skeleton — a document arrives, master data is pulled in, the
document is matched against it, values are derived, the result is validated, a payload is built
and sent. So the SPINE is expected to match; what varies is the target PROFILE (payload, auth,
master-data roles, coding model).

Verdicts:
  follows                   spine and profile both agree
  follows_with_deviations   they agree but specific things differ — the useful case
  profile_missing           it is an IDP implementation, but no profile describes this target.
                            That is a gap of one file, not a new recipe: write a profile.
  not_idp                   the tree does not look like an IDP delivery at all
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shape_extract  # noqa: E402

# Dimensions that decide WHICH PROFILE fits. The spine itself is not scored on these — the
# skeleton is the same whatever the target.
WEIGHTS = {"integration_shape": 3, "name_class": 3, "coding_model": 2}
FLOOR = 0.5          # below this the target is not described by any profile we have

# A recipe's md_inbound_roles is the UNION across the cluster, so any single tree legitimately
# lacks several. Only these carry a finding on their own; the rest are informational, or every
# report drowns in noise.
CORE_ROLES = {"supplier", "purchase_order", "purchase_order_line"}


def _profile_shape(profile):
    return {
        "integration_shape": profile.get("integration_shape"),
        "name_class": profile.get("name_class"),
        "coding_model": (profile.get("coding_model") or {}).get("model"),
    }


def is_idp(record):
    """Does this tree do the IDP job at all: documents in, something out?

    Deliberately generous. The point of the spine is that almost every delivery matches it, so
    this only rejects trees that clearly are not one - no queues, or nothing that ingests.
    """
    reasons = []
    if not (record.get("topology") or {}).get("queues"):
        reasons.append("no queues")
    if not ((record.get("ingest") or {}).get("modes")):
        reasons.append("no ingest channel")
    nodes = (record.get("hook_graph") or {}).get("nodes") or []
    if not nodes:
        reasons.append("no hooks")
    return (not reasons), reasons


def _record_shape(record):
    target = record.get("target_profile", {})
    names = target.get("name_class_candidates") or []
    return {
        "integration_shape": record.get("integration_shape"),
        "name_class": names[0] if names else None,
        "coding_model": (target.get("coding_model") or {}).get("model"),
        "queue_axis": (record.get("topology", {}).get("queue_axis") or [None])[0],
    }


def score(record, profile):
    """Weighted agreement on the dimensions that separate TARGETS. 0.0 - 1.0."""
    want, got = _profile_shape(profile), _record_shape(record)
    earned = possible = 0
    detail = {}
    for key, weight in WEIGHTS.items():
        expected, actual = want.get(key), got.get(key)
        if expected is None:
            continue                       # the recipe does not constrain this dimension
        possible += weight
        agree = expected == actual
        if agree:
            earned += weight
        detail[key] = {"expected": expected, "actual": actual, "agree": agree}
    return (earned / possible if possible else 0.0), detail


def deviations(record, spine, profile):
    """Concrete, checkable differences — each one a review finding."""
    out = []
    shape = spine.get("shape", {})
    target = profile or {}

    want_pack = set((profile.get("packaging") or {}).values()) if isinstance(
        profile.get("packaging"), dict) else set(profile.get("packaging") or [])
    got_pack = set(record.get("packaging") or [])
    for missing in sorted(want_pack - got_pack):
        out.append({"kind": "packaging_absent", "severity": "finding", "detail": missing,
                    "why": f"the recipe builds on {missing}; this tree has none"})

    want_roles = set(target.get("md_inbound_roles") or [])
    got_roles = set(record.get("target_profile", {}).get("md_inbound_roles") or [])
    unclassified = record.get("target_profile", {}).get("md_unclassified_refs") or 0
    for missing in sorted(want_roles - got_roles):
        core = missing in CORE_ROLES
        why = ("matching that depends on this role cannot resolve" if core else
               "the recipe's role list is the union across its cluster; this tree simply does "
               "not use this one")
        if unclassified:
            why += (f" — note {unclassified} collection reference(s) could not be assigned a role, "
                    "so this may be present under a name the vocabulary does not know")
        out.append({"kind": "master_data_role_absent", "detail": missing,
                    "severity": "finding" if core else "informational", "why": why})

    want_ingest = set((shape.get("ingest") or {}).get("modes") or [])
    got_ingest = set((record.get("ingest") or {}).get("modes") or [])
    for missing in sorted(want_ingest - got_ingest):
        out.append({"kind": "ingest_mode_absent", "severity": "informational", "detail": missing,
                    "why": "documents from this channel are not handled"})

    rules = record.get("rules") or {}
    if rules.get("total", 0) == 0 and spine.get("intents", {}).get("gating"):
        out.append({"kind": "no_validation_layer", "severity": "finding", "detail": "0 native rules",
                    "why": "the recipe gates automation on validation; nothing gates here"})

    # the line-item trap: a table in the schema but an engine that cannot extract one
    engines = record.get("engines") or {}
    has_table = any(f.get("scope") in ("line_item", "both")
                    for f in record.get("schema_field_families") or [])
    if has_table and engines.get("total", 0) and not engines.get("line_level_use_case", True):
        out.append({"kind": "engine_not_line_level", "severity": "finding", "detail": "settings.use_case",
                    "why": "line-item columns exist but the engine profile is header-only, so "
                           "they extract nothing — silently"})

    structure = record.get("structure") or {}
    if structure.get("export_trigger") and "annotation_content.export" not in (
            structure.get("export_trigger") or []):
        out.append({"kind": "unusual_export_trigger",
                    "detail": ", ".join(structure["export_trigger"]),
                    "why": "export does not fire on annotation_content.export; ordering "
                           "assumptions in the recipe may not hold"})

    src = record.get("source_pin") or {}
    if src.get("complete") is False:
        out.append({"kind": "incomplete_pull", "severity": "finding", "detail": "; ".join(src.get("missing_objects") or []),
                    "why": "referenced objects are missing from the pull, so this comparison is "
                           "against a partial tree"})
    return out


def match(record, spine, profiles):
    """Spine + best profile for this record. `profiles` is [(name, profile_dict)]."""
    idp, why_not = is_idp(record)
    if not idp:
        return {"verdict": "not_idp", "reasons": why_not,
                "note": "this does not look like an IDP delivery, so the spine does not apply"}

    scored = []
    for name, profile in profiles:
        value, detail = score(record, profile)
        scored.append({"profile": name, "score": round(value, 3), "dimensions": detail})
    scored.sort(key=lambda r: -r["score"])

    if not scored or scored[0]["score"] < FLOOR:
        return {"verdict": "profile_missing", "recipe": spine.get("recipe"),
                "candidates": scored,
                "note": "the spine applies — this is an IDP delivery — but no profile describes "
                        "this target. Write one: payload shape, auth, master-data roles, coding "
                        "model. That is one file, not a new recipe.",
                "observed": _record_shape(record)}

    best = scored[0]
    profile = dict(profiles)[best["profile"]]
    devs = deviations(record, spine, profile)
    findings = [d for d in devs if d.get("severity") == "finding"]
    return {"verdict": "follows" if best["score"] == 1.0 and not findings
                       else "follows_with_deviations",
            "recipe": spine.get("recipe"), "profile": best["profile"], "score": best["score"],
            "dimensions": best["dimensions"],
            "findings": findings,
            "informational": [d for d in devs if d.get("severity") != "finding"],
            "runners_up": scored[1:3]}


def load_spine(root):
    """The spine recipe and its target profiles."""
    spine_path = os.path.join(root, "idp-spine", "recipe.json")
    spine = json.load(open(spine_path))
    profiles = []
    for path in sorted(glob.glob(os.path.join(root, "idp-spine", "profiles", "*.json"))):
        try:
            profiles.append((os.path.basename(path)[:-5], json.load(open(path))))
        except Exception as exc:               # a broken profile must not break the read path
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
    return spine, profiles


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", required=True, help="a pulled prd2 environment directory")
    ap.add_argument("--recipes", default=os.path.dirname(here), help="the recipes directory")
    ap.add_argument("--json", help="write the full result here")
    args = ap.parse_args()

    record = shape_extract.extract(os.path.expanduser(args.env))
    spine, profiles = load_spine(os.path.expanduser(args.recipes))
    result = match(record, spine, profiles)
    if args.json:
        json.dump({"match": result, "record": record}, open(args.json, "w"), indent=2)
        print("written:", args.json)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
