#!/usr/bin/env python3
"""Phase 3a step 3: build PATCH ops mapping prod snapshot to target datapoint IDs.

Full-overwrite: every captured non-formula datapoint from prod is transferred. Each replace op
carries value+page+position from the prod snapshot for spatial fidelity. Each op is tagged with
`_meta.phase` ∈ {`pre`, `post`} based on prod's validation_sources — pre-hook ops run before the
status-toggle, post-hook ops run after (so prod-truth hook-output values aren't overwritten by
the target's hooks re-firing). Multivalue row mismatches are surfaced; explicit add/remove ops
are emitted when --emit-row-ops.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from content_walker import walk, walk_tree, load_content_nodes


def parent_multivalue_path(path, known_mv_paths=None):
    """Find the multivalue ancestor path of a datapoint.

    The walker emits paths like ``<section>.<multivalue>.<tuple_sid>.<datapoint_sid>``
    when the tuple node has its own schema_id (e.g. ``line_items_section.line_items.line_item.item_amount``).
    But some multivalues have unnamed tuples (no schema_id on the tuple node), which
    makes the path one segment shorter:
    ``amounts_section.amount_total_tax_ns.amount_total_tax``. A fixed strip-N heuristic
    is wrong for one of the two shapes.

    When ``known_mv_paths`` is provided, return the longest prefix of ``path`` that is
    actually a multivalue (correct for both shapes). Falls back to a 2-segment strip
    when no known set is supplied (legacy callers).
    """
    parts = path.split(".")
    if known_mv_paths:
        for n in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:n])
            if candidate in known_mv_paths:
                return candidate
        return None
    if len(parts) >= 3:
        return ".".join(parts[:-2])
    return None


def collect_formula_schema_ids(schema_node):
    """Walk a parsed schema.json and return the set of schema_ids whose ui_configuration.type == 'formula'.

    Formula-typed datapoints are read-only via the operations / per-dp PATCH endpoints —
    the API rejects with HTTP 400 "The computed datapoint X can only be updated from UI."
    The plan must skip these to avoid mid-batch failures.
    """
    formula_ids = set()
    def walk(n):
        if isinstance(n, dict):
            ui = n.get("ui_configuration") or {}
            if ui.get("type") == "formula":
                sid = n.get("id")
                if sid:
                    formula_ids.add(sid)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(schema_node)
    return formula_ids


def build_plan(prod_normalized, uat_content, emit_row_ops, target_formula_ids=None):
    uat_nodes = load_content_nodes_inplace(uat_content)
    uat_flat = walk(uat_nodes)
    uat_tree = walk_tree(uat_nodes)

    uat_idx = {}
    for f in uat_flat:
        uat_idx.setdefault((f["path"], f["row"]), []).append(f)

    ops = []
    unmatched = []
    skipped_formula_targets = []

    for prod_field in prod_normalized["fields"]:
        if prod_field["value"] in (None, ""):
            continue
        if target_formula_ids and prod_field["schema_id"] in target_formula_ids:
            skipped_formula_targets.append({
                "schema_id": prod_field["schema_id"],
                "path": prod_field["path"],
                "row": prod_field["row"],
                "reason": "formula_typed_in_target_schema",
            })
            continue

        key = (prod_field["path"], prod_field["row"])
        candidates = uat_idx.get(key, [])
        if not candidates:
            unmatched.append({"reason": "no_uat_match", "prod": prod_field})
            continue
        if len(candidates) > 1:
            unmatched.append({"reason": "ambiguous_uat", "prod": prod_field})
            continue
        target = candidates[0]

        # Classify into pre-hook vs post-hook PATCH phase. Fields that prod's snapshot recorded
        # as written by hooks (validation_sources contains "data_matching" or "rules") are MDH /
        # rule OUTPUTS — they must be PATCHed *after* the hooks have fired in the target, otherwise
        # the hook firing in target will overwrite our prod-truth value with whatever its own logic
        # produces (which may differ when prod's hooks ran on different — pre-human-edit — input).
        # Fields not touched by hooks in prod (extraction or human edits only) are INPUTS to those
        # hooks and must be PATCHed *before* the hooks fire.
        prod_vs = prod_field["validation_sources"] or []
        if any(s in ("data_matching", "rules", "connector") for s in prod_vs):
            phase = "post"
        else:
            phase = "pre"

        ops.append({
            "op": "replace",
            "id": target["datapoint_id"],
            "value": {
                "value": prod_field["value"],
                "page": prod_field.get("page"),
                "position": prod_field.get("position"),
            },
            "_meta": {
                "schema_id": prod_field["schema_id"],
                "path": prod_field["path"],
                "row": prod_field["row"],
                "from": target["value"],
                "to": prod_field["value"],
                "human_edited_in_prod": "human" in prod_vs,
                "prod_validation_sources": prod_vs,
                "phase": phase,
            },
        })

    # Multivalue row reconciliation
    # First pass: collect known multivalue paths from the uat tree so we can resolve
    # paths whose tuple has no schema_id (e.g. amounts_section.amount_total_tax_ns).
    uat_mv_ids = {}
    uat_tuple_ids = {}
    for n in uat_tree:
        if n.get("category") == "multivalue":
            uat_mv_ids[n["path"]] = n["id"]
        elif n.get("category") == "tuple":
            uat_tuple_ids.setdefault(n["path"], {})[n["row"]] = n["id"]

    known_mv_paths = set(uat_mv_ids)

    prod_rows_by_mv = {}
    for f in prod_normalized["fields"]:
        if f["row"] is not None:
            mv = parent_multivalue_path(f["path"], known_mv_paths)
            if mv:
                prod_rows_by_mv.setdefault(mv, set()).add(f["row"])

    uat_rows_by_mv = {}
    for f in uat_flat:
        if f["row"] is not None:
            mv = parent_multivalue_path(f["path"], known_mv_paths)
            if mv:
                uat_rows_by_mv.setdefault(mv, set()).add(f["row"])

    row_reconciliation = []
    row_ops = []
    for mv in set(prod_rows_by_mv) | set(uat_rows_by_mv):
        prod_rows = prod_rows_by_mv.get(mv, set())
        uat_rows = uat_rows_by_mv.get(mv, set())
        if prod_rows == uat_rows:
            continue
        only_prod = sorted(prod_rows - uat_rows)
        only_uat = sorted(uat_rows - prod_rows)
        row_reconciliation.append({
            "multivalue_path": mv,
            "multivalue_id": uat_mv_ids.get(mv),
            "prod_row_count": len(prod_rows),
            "uat_row_count": len(uat_rows),
            "rows_only_in_prod": only_prod,
            "rows_only_in_uat": only_uat,
        })

        if not emit_row_ops:
            continue

        # tuple_path inside multivalue: try to find the tuple schema name from any datapoint child of the multivalue
        # For uat_tuple_ids, keys are tuple paths like "section.multivalue.tuple_schema"
        tuple_paths = [p for p in uat_tuple_ids.keys() if p.startswith(mv + ".") and p.count(".") == mv.count(".") + 1]
        tuple_path = tuple_paths[0] if tuple_paths else None

        for row in only_uat:
            tid = uat_tuple_ids.get(tuple_path, {}).get(row) if tuple_path else None
            if tid:
                row_ops.append({"op": "remove", "id": tid, "_meta": {"reason": "row_only_in_uat", "multivalue_path": mv, "row": row}})

        for _ in only_prod:
            mv_id = uat_mv_ids.get(mv)
            if mv_id:
                # Rossum content/operations endpoint accepts only add/replace/remove. For "add"
                # on a multivalue, value must be an empty object — the API allocates a fresh
                # tuple (and its child datapoints) and returns them in the response.
                row_ops.append({"op": "add", "id": mv_id, "value": {}, "_meta": {"reason": "row_only_in_prod", "multivalue_path": mv}})

    return {
        "prod_annotation_id": prod_normalized.get("annotation_id"),
        "ops": ops,
        "row_ops": row_ops,
        "row_reconciliation": row_reconciliation,
        "unmatched": unmatched,
        "skipped_formula_targets": skipped_formula_targets,
    }


def load_content_nodes_inplace(uat_content):
    """uat_content can be either a parsed dict or a path. Return node list."""
    if isinstance(uat_content, str):
        return load_content_nodes(uat_content)
    if isinstance(uat_content, list):
        return uat_content
    return uat_content.get("content") or uat_content.get("results") or [uat_content]


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--prod-normalized", required=True, help="prod normalized.json from normalize_snapshot")
    p.add_argument("--uat-content", required=True, help="uatv2 content.json (post-extraction, pre-patch)")
    p.add_argument("--target-schema", help="path to target queue's schema.json — when supplied, schema_ids whose ui_configuration.type == 'formula' are filtered out (those datapoints are read-only via the API)")
    p.add_argument("--emit-row-ops", action="store_true", help="emit add_empty_tuple/remove ops for row reconciliation")
    p.add_argument("--out", required=True, help="output patch plan JSON")
    args = p.parse_args()

    prod = json.load(open(args.prod_normalized))
    uat = json.load(open(args.uat_content))

    target_formula_ids = None
    if args.target_schema:
        target_schema = json.load(open(args.target_schema))
        target_formula_ids = collect_formula_schema_ids(target_schema)

    plan = build_plan(prod, uat, args.emit_row_ops, target_formula_ids)
    json.dump(plan, open(args.out, "w"), indent=2, default=str)

    n_pre = sum(1 for o in plan["ops"] if o["_meta"]["phase"] == "pre")
    n_post = sum(1 for o in plan["ops"] if o["_meta"]["phase"] == "post")
    summary = (
        f"prod ann {plan['prod_annotation_id']}: "
        f"{len(plan['ops'])} replace ops ({n_pre} pre-hook, {n_post} post-hook), "
        f"{len(plan['row_ops'])} row ops, "
        f"{len(plan['row_reconciliation'])} mvs need reconciliation, "
        f"{len(plan['unmatched'])} unmatched"
    )
    if target_formula_ids:
        summary += f", {len(plan['skipped_formula_targets'])} skipped (formula-typed in target)"
    print(summary)
    for r in plan["row_reconciliation"]:
        print(f"  {r['multivalue_path']}: prod={r['prod_row_count']} uat={r['uat_row_count']}")


if __name__ == "__main__":
    main()
