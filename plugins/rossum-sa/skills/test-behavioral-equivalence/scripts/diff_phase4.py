#!/usr/bin/env python3
"""Phase 4: Diff before/after normalized snapshots, classify per the ladder, cluster across corpus.

Inputs : per-annotation normalized.json files in --before-dir and --after-dir,
         a --mapping file pairing prod_aid to target annotation id,
         optional --config with hot_fields, out_of_scope, structural_new, etc.

Output : phase4-diff.json with per-annotation verdicts, classifications, and cross-corpus clusters.
"""

import argparse
import json
import os
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation


# --- value normalization (severity, never visibility) ---

def normalize_value(v):
    """Return (normalized_string, is_numeric) for severity classification only."""
    if v is None or v == "" or v == "null":
        return ("", False)
    s = str(v).strip().replace("\u00a0", "")
    candidate = s
    last_period = candidate.rfind(".")
    last_comma = candidate.rfind(",")
    if last_period > last_comma:
        candidate = candidate.replace(",", "").replace(" ", "")
    elif last_comma > last_period:
        candidate = candidate.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        candidate = candidate.replace(" ", "")
    try:
        d = Decimal(candidate)
        return (format(d.normalize(), "f"), True)
    except (InvalidOperation, ValueError):
        return (s, False)


def values_byte_equal(a, b):
    sa = "" if a is None else str(a)
    sb = "" if b is None else str(b)
    return sa == sb


# --- per-diff classification ---

def classify_diff(prod_field, uat_field, ctx):
    """Apply the classification ladder. Return dict or None when byte-equal (no diff)."""
    if prod_field is None and uat_field is None:
        return None
    sid = (prod_field or uat_field)["schema_id"]
    raw_prod = (prod_field or {}).get("value")
    raw_uat = (uat_field or {}).get("value")

    # 1. annotation_identity
    if sid in ctx["out_of_scope"]:
        if values_byte_equal(raw_prod, raw_uat):
            return None
        return {"class": "annotation_identity", "severity": "out-of-scope",
                "raw_prod": raw_prod, "raw_uat": raw_uat}

    # 2. structural_expected
    if sid in ctx["structural_new"] or sid in ctx["structural_removed"]:
        if values_byte_equal(raw_prod, raw_uat):
            return None
        return {"class": "structural_expected", "severity": "out-of-scope",
                "raw_prod": raw_prod, "raw_uat": raw_uat}

    # 3. ocr_drift_line_items (only when prod row count was 0 and human-edited mode)
    path = (prod_field or uat_field)["path"]
    if ctx["mode"] == "human-only":
        for drift_path in ctx["ocr_drift_paths"]:
            if path.startswith(drift_path + "."):
                if values_byte_equal(raw_prod, raw_uat):
                    return None
                return {"class": "ocr_drift_line_items", "severity": "soft-fail",
                        "raw_prod": raw_prod, "raw_uat": raw_uat}

    if values_byte_equal(raw_prod, raw_uat):
        return None

    norm_prod, prod_is_num = normalize_value(raw_prod)
    norm_uat, uat_is_num = normalize_value(raw_uat)
    norm_match = (norm_prod == norm_uat)

    base = {"raw_prod": raw_prod, "raw_uat": raw_uat,
            "normalized_prod": norm_prod, "normalized_uat": norm_uat}

    # 6. hot_surface_value (hard-fail when normalized forms also differ)
    if sid in ctx["hot_fields"] and not norm_match:
        return {"class": "hot_surface_value", "severity": "hard-fail", **base}

    # 9. numeric_formatting (raw differ, normalized match, both numeric)
    if prod_is_num and uat_is_num and norm_match:
        return {"class": "numeric_formatting", "severity": "soft-fail", **base}

    # 10. locale_formatting (raw differ, normalized match, non-numeric)
    if norm_match:
        return {"class": "locale_formatting", "severity": "soft-fail", **base}

    # 12. field_value (fallback)
    return {"class": "field_value", "severity": "hard-fail", **base}


CRASH_TOKENS = ("Traceback", "TypeError", "Exception", "AttributeError",
                "ValueError", "KeyError", "ZeroDivisionError", "NameError")


def detect_formula_crash(blocker_items):
    crashes = []
    for it in blocker_items or []:
        msg = it.get("message") or ""
        if any(tok in msg for tok in CRASH_TOKENS):
            crashes.append(it)
    return crashes


def diff_blockers(prod_items, uat_items):
    def sig(it): return (it["schema_id"], it["type"])
    prod_keys = Counter(sig(x) for x in prod_items)
    uat_keys = Counter(sig(x) for x in uat_items)
    return {
        "only_in_prod": [list(k) for k in sorted(set(prod_keys) - set(uat_keys))],
        "only_in_uat": [list(k) for k in sorted(set(uat_keys) - set(prod_keys))],
        "common": [list(k) for k in sorted(set(prod_keys) & set(uat_keys))],
    }


def diff_one_annotation(prod_norm, uat_norm, ctx):
    prod_idx = {(f["path"], f["row"]): f for f in prod_norm["fields"]}
    uat_idx = {(f["path"], f["row"]): f for f in uat_norm["fields"]}

    diffs = []
    for key in set(prod_idx) | set(uat_idx):
        result = classify_diff(prod_idx.get(key), uat_idx.get(key), ctx)
        if result:
            result["path"] = key[0]
            result["row"] = key[1]
            result["schema_id"] = (prod_idx.get(key) or uat_idx.get(key))["schema_id"]
            diffs.append(result)

    bl_diff = diff_blockers(prod_norm.get("blocker_items") or [], uat_norm.get("blocker_items") or [])
    crashes = detect_formula_crash(uat_norm.get("blocker_items") or [])

    # blocker classification: missing/new
    for sid, typ in bl_diff["only_in_prod"]:
        if typ in ("extension", "error_message"):
            diffs.append({
                "class": "blocker_missing", "severity": "hard-fail",
                "schema_id": sid, "blocker_type": typ,
                "path": None, "row": None,
            })
    for sid, typ in bl_diff["only_in_uat"]:
        sev = "hard-fail" if prod_norm.get("automated") else "soft-fail"
        diffs.append({
            "class": "blocker_new", "severity": sev,
            "schema_id": sid, "blocker_type": typ,
            "path": None, "row": None,
        })

    status_delta = {
        "prod_status": prod_norm.get("status"),
        "uat_status": uat_norm.get("status"),
        "prod_automated": prod_norm.get("automated"),
        "uat_automated": uat_norm.get("automated"),
        "automation_downgrade": bool(prod_norm.get("automated")) and not bool(uat_norm.get("automated")),
    }

    if status_delta["automation_downgrade"]:
        diffs.append({
            "class": "automation_downgrade", "severity": "hard-fail",
            "schema_id": None, "path": None, "row": None,
            "raw_prod": "automated=True", "raw_uat": f"status={uat_norm.get('status')}",
        })

    severities = [d["severity"] for d in diffs]
    if crashes:
        verdict = "P0"
    elif "hard-fail" in severities:
        verdict = "hard-fail"
    elif "soft-fail" in severities:
        verdict = "soft-fail"
    else:
        verdict = "pass"

    return {
        "prod_annotation_id": prod_norm.get("annotation_id"),
        "uat_annotation_id": uat_norm.get("annotation_id"),
        "verdict": verdict,
        "status_delta": status_delta,
        "diff_count_total": len(diffs),
        "diff_count_by_class": dict(Counter(d["class"] for d in diffs)),
        "diff_count_by_severity": dict(Counter(severities)),
        "blocker_parity": bl_diff,
        "crashes": crashes,
        "diffs": diffs,
    }


def cluster_failures(per_annotation):
    clusters = {}
    K = len(per_annotation)
    for ann in per_annotation:
        for d in ann["diffs"]:
            if d["severity"] not in ("hard-fail", "P0"):
                continue
            key = (d.get("schema_id") or "(none)", d["class"])
            cl = clusters.setdefault(key, {
                "schema_id": key[0], "class": key[1],
                "annotations": [], "examples": [],
            })
            cl["annotations"].append(ann["prod_annotation_id"])
            if len(cl["examples"]) < 3 and "raw_prod" in d:
                cl["examples"].append({"prod": d.get("raw_prod"), "uat": d.get("raw_uat")})
    for cl in clusters.values():
        cl["fan_out"] = f"{len(cl['annotations'])}/{K}"
    return sorted(clusters.values(), key=lambda c: -len(c["annotations"]))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--before-dir", required=True, help="dir with prod *.normalized.json")
    p.add_argument("--after-dir", required=True, help="dir with uat *.normalized.json")
    p.add_argument("--mapping", required=True, help="JSON: {prod_aid: {uatv2_annotation_id|uat_annotation_id}}")
    p.add_argument("--config", help="optional config JSON: hot_fields, out_of_scope, structural_new/_removed, ocr_drift_paths, mode")
    p.add_argument("--out", required=True, help="output phase4-diff.json")
    args = p.parse_args()

    config = {}
    if args.config and os.path.exists(args.config):
        config = json.load(open(args.config))

    ctx = {
        "out_of_scope": set(config.get("out_of_scope") or []),
        "structural_new": set(config.get("structural_new") or []),
        "structural_removed": set(config.get("structural_removed") or []),
        "hot_fields": set(config.get("hot_fields") or []),
        "ocr_drift_paths": set(config.get("ocr_drift_paths") or []),
        "mode": config.get("mode", "human-only"),
    }

    mapping = json.load(open(args.mapping))
    per_annotation = []
    for prod_aid_str, m in mapping.items():
        prod_aid = int(prod_aid_str)
        uat_aid = m.get("uatv2_annotation_id") or m.get("uat_annotation_id") or m.get("target_annotation_id")
        if not uat_aid:
            print(f"  skip {prod_aid}: no target annotation id", file=sys.stderr)
            continue
        prod_path = os.path.join(args.before_dir, f"{prod_aid}.normalized.json")
        uat_path = os.path.join(args.after_dir, f"{uat_aid}.normalized.json")
        if not os.path.exists(prod_path) or not os.path.exists(uat_path):
            print(f"  skip prod={prod_aid} uat={uat_aid}: missing normalized.json", file=sys.stderr)
            continue
        prod_norm = json.load(open(prod_path))
        uat_norm = json.load(open(uat_path))
        per_annotation.append(diff_one_annotation(prod_norm, uat_norm, ctx))

    clusters = cluster_failures(per_annotation)

    output = {
        "config": {k: sorted(v) if isinstance(v, set) else v for k, v in ctx.items()},
        "corpus_size": len(per_annotation),
        "verdict_counts": dict(Counter(a["verdict"] for a in per_annotation)),
        "per_annotation": per_annotation,
        "clusters": clusters,
    }
    json.dump(output, open(args.out, "w"), indent=2, default=str)

    print(f"\nverdict counts: {output['verdict_counts']}")
    print(f"clusters (top 10 by fan-out):")
    for cl in clusters[:10]:
        print(f"  {cl['fan_out']}  {cl['schema_id']}  ({cl['class']})")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
