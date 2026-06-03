#!/usr/bin/env python3
"""Phase 1: Static validation across an implementation tree.

Checks per queue:
  1a — formula `field.<name>` references resolve to schema ids
  1b — formula constraints (size, parses, no top-level return, no http imports, no self-reference)
  1c — hook chain integrity (run_after URLs resolve to local hook files)
  1e — rule URLs attached to queues exist as files

When --source-root is also passed (upgrade-test mode), runs cross-tree checks:
  1g — formula preservation: every field that was ui_configuration.type='formula'
       in source must be (a) formula-typed with a body in target, OR (b) absent
       from the target schema (explicitly removed). A field that became
       'data'/'manual'/etc. in target with no formulas/<sid>.py file is flagged
       as a migration regression — the target field will silently be empty.

Outputs phase1-static.json with violations grouped by check + queue, plus a summary.
"""

import argparse
import ast
import glob
import json
import os
import re
import sys
from collections import Counter


def collect_schema_ids(schema_node, out=None):
    """Walk a schema content tree and collect every node's `id`."""
    if out is None:
        out = set()
    nodes = schema_node if isinstance(schema_node, list) else [schema_node]
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if nid:
            out.add(nid)
        for key in ("content", "children"):
            children = n.get(key)
            if children:
                collect_schema_ids(children, out)
    return out


def find_field_refs(formula_text):
    return set(re.findall(r"\bfield\.([a-zA-Z_][a-zA-Z_0-9]*)", formula_text))


def check_formula(path, schema_ids):
    violations = []
    text = open(path).read()

    if len(text) > 2000:
        violations.append({"check": "1b_size", "severity": "Error", "file": path,
                           "msg": f"{len(text)} chars > 2000"})

    try:
        tree = ast.parse(text)
        for stmt in tree.body:
            if isinstance(stmt, ast.Return):
                violations.append({"check": "1b_top_return", "severity": "Error", "file": path,
                                   "msg": "top-level return"})
                break
    except SyntaxError as e:
        violations.append({"check": "1b_syntax", "severity": "Error", "file": path,
                           "msg": f"SyntaxError: {e}"})

    for line in text.splitlines():
        s = line.strip()
        if any(s.startswith(p) for p in (
            "import requests", "from requests", "import httpx", "from httpx",
            "import urllib", "from urllib", "import http.client",
        )):
            violations.append({"check": "1b_http_import", "severity": "Error", "file": path,
                               "msg": s})

    fname = os.path.splitext(os.path.basename(path))[0]
    refs = find_field_refs(text)
    if fname in refs:
        violations.append({"check": "1b_self_ref", "severity": "Error", "file": path,
                           "msg": f"references field.{fname}"})

    for ref in refs:
        if ref not in schema_ids:
            violations.append({"check": "1a_schema_ref", "severity": "Error", "file": path,
                               "msg": f"field.{ref} not in schema"})
    return violations


def _walk_json_under(impl_root, parent_name):
    """Yield every *.json under any directory named `parent_name` (e.g. 'hooks', 'rules').

    Uses os.walk to avoid glob's metacharacter issues with bracket/space in dir names.
    """
    for dirpath, dirnames, filenames in os.walk(impl_root):
        if os.path.basename(dirpath) == parent_name:
            for fn in filenames:
                if fn.endswith(".json"):
                    yield os.path.join(dirpath, fn)


def index_local_hooks(impl_root):
    """Return {hook_url: meta} for any *.json under hooks/ that has a top-level 'url'."""
    idx = {}
    for hf in _walk_json_under(impl_root, "hooks"):
        try:
            data = json.load(open(hf))
        except Exception:
            continue
        u = data.get("url")
        if u:
            idx[u] = {"path": hf, "active": data.get("active", True), "id": data.get("id"), "name": data.get("name")}
    return idx


def index_local_rules(impl_root):
    idx = {}
    for rf in _walk_json_under(impl_root, "rules"):
        try:
            data = json.load(open(rf))
        except Exception:
            continue
        u = data.get("url")
        if u:
            idx[u] = {"path": rf, "id": data.get("id"), "name": data.get("name")}
    return idx


def check_queue(queue_dir, queue_path, schema_path, hook_idx, rule_idx, impl_root):
    rel = os.path.relpath(queue_dir, impl_root)
    violations = []

    try:
        schema = json.load(open(schema_path))
    except Exception as e:
        return {"queue_dir": rel, "error": f"failed to parse schema.json: {e}"}

    schema_content = schema.get("content") or []
    schema_ids = collect_schema_ids(schema_content)

    formulas_dir = os.path.join(queue_dir, "formulas")
    formula_files = []
    if os.path.isdir(formulas_dir):
        formula_files = sorted(
            os.path.join(formulas_dir, fn)
            for fn in os.listdir(formulas_dir)
            if fn.endswith(".py")
        )
    for ff in formula_files:
        violations.extend(check_formula(ff, schema_ids))

    try:
        queue = json.load(open(queue_path))
    except Exception as e:
        violations.append({"check": "1c_queue_parse", "severity": "Error", "file": queue_path, "msg": str(e)})
        queue = {}

    # 1c: hook URLs on queue point to local hook files
    for hu in queue.get("hooks") or []:
        meta = hook_idx.get(hu)
        if not meta:
            violations.append({"check": "1c_hook_url", "severity": "Error", "file": queue_path,
                               "msg": f"hook URL not resolved: {hu}"})

    # 1e: rule URLs on queue point to local rule files
    for ru in queue.get("rules") or []:
        if not rule_idx.get(ru):
            violations.append({"check": "1e_rule_url", "severity": "Error", "file": queue_path,
                               "msg": f"rule URL not resolved: {ru}"})

    return {
        "queue_dir": rel,
        "schema_id_count": len(schema_ids),
        "formula_count": len(formula_files),
        "violations": violations,
        "violation_count_by_check": dict(Counter(v["check"] for v in violations)),
    }


def check_hook_run_after(hook_idx):
    """Cross-hook: every run_after URL resolves to a local hook file."""
    violations = []
    for url, meta in hook_idx.items():
        try:
            data = json.load(open(meta["path"]))
        except Exception:
            continue
        for ra in data.get("run_after") or []:
            if not hook_idx.get(ra):
                violations.append({"check": "1c_run_after", "severity": "Warning",
                                   "file": meta["path"],
                                   "msg": f"run_after URL not resolved: {ra}"})
    return violations


def find_queue_dirs(impl_root):
    for dirpath, dirnames, filenames in os.walk(impl_root):
        if "queue.json" in filenames and "schema.json" in filenames:
            yield dirpath, os.path.join(dirpath, "queue.json"), os.path.join(dirpath, "schema.json")


# ---------------------------------------------------------------------------
# 1g — formula preservation across source -> target (upgrade-test mode)
# ---------------------------------------------------------------------------

QUEUE_NAME_RE = re.compile(r"^(.+?)_\[(\d+)\]$")


def index_queues_by_name(impl_root):
    """Walk impl_root for queue dirs and return {queue_name: {schema_path, formulas_dir, queue_id}}.

    queue_name is the dir basename without the trailing `_[<id>]` suffix.
    """
    out = {}
    for qdir, _qf, sf in find_queue_dirs(impl_root):
        base = os.path.basename(qdir)
        m = QUEUE_NAME_RE.match(base)
        if not m:
            continue
        qname = m.group(1)
        out[qname] = {
            "queue_dir": qdir,
            "schema_path": sf,
            "formulas_dir": os.path.join(qdir, "formulas"),
            "queue_id": int(m.group(2)),
        }
    return out


def walk_schema_nodes(node, out=None):
    """Yield every dict node that has an `id` field in the schema tree."""
    if out is None:
        out = []
    if isinstance(node, dict):
        if node.get("id"):
            out.append(node)
        for k in ("content", "children"):
            if k in node:
                walk_schema_nodes(node[k], out)
    elif isinstance(node, list):
        for n in node:
            walk_schema_nodes(n, out)
    return out


def schema_id_to_node(schema):
    out = {}
    for n in walk_schema_nodes(schema):
        sid = n.get("id")
        if sid:
            out[sid] = n
    return out


def check_formula_preservation(source_root, target_root):
    """For each queue present in BOTH trees (matched by queue_name), find fields
    that are formula-typed with a body in source but lost their formula in target.

    Returns (violations, summary) where violations is a flat list and summary is
    {"queues_in_both": N, "fields_checked": N, "queues_only_in_source": [...],
     "queues_only_in_target": [...]}.
    """
    src = index_queues_by_name(source_root)
    tgt = index_queues_by_name(target_root)

    only_src = sorted(set(src) - set(tgt))
    only_tgt = sorted(set(tgt) - set(src))
    in_both = sorted(set(src) & set(tgt))

    violations = []
    fields_checked = 0
    for qname in in_both:
        s_q = src[qname]
        t_q = tgt[qname]
        try:
            s_schema = json.load(open(s_q["schema_path"]))
            t_schema = json.load(open(t_q["schema_path"]))
        except Exception as e:
            violations.append({
                "check": "1g_schema_parse", "severity": "Warning",
                "queue_name": qname,
                "msg": f"failed to parse schemas: {e}",
            })
            continue

        s_idx = schema_id_to_node(s_schema)
        t_idx = schema_id_to_node(t_schema)

        for sid, s_node in s_idx.items():
            ui = s_node.get("ui_configuration") or {}
            if ui.get("type") != "formula":
                continue
            if not s_node.get("formula"):  # source flagged formula but no body — ambiguous, skip
                continue
            fields_checked += 1

            t_node = t_idx.get(sid)
            if t_node is None:
                # Field was removed in target. That's a deliberate change; not a regression here.
                # (`field_removed` audit is separate from formula preservation.)
                continue

            t_ui = t_node.get("ui_configuration") or {}
            t_is_formula = t_ui.get("type") == "formula"
            formula_file = os.path.join(t_q["formulas_dir"], f"{sid}.py")
            has_formula_file = os.path.exists(formula_file)
            has_inline = bool(t_node.get("formula"))

            if not t_is_formula and not has_inline and not has_formula_file:
                violations.append({
                    "check": "1g_formula_dropped", "severity": "Error",
                    "queue_name": qname, "queue_id_target": t_q["queue_id"],
                    "schema_id": sid,
                    "source_ui": {"type": ui.get("type"), "edit": ui.get("edit")},
                    "target_ui": {"type": t_ui.get("type"), "edit": t_ui.get("edit")},
                    "target_formula_file_expected": os.path.relpath(formula_file, target_root),
                    "source_formula_body": s_node.get("formula"),
                    "target_schema_path": os.path.relpath(t_q["schema_path"], target_root),
                    "msg": (f"prod field '{sid}' was ui_configuration.type='formula' with body; "
                            f"target is type='{t_ui.get('type')}' with no formulas/{sid}.py — "
                            f"formula lost in migration"),
                })
            elif t_is_formula and not has_inline and not has_formula_file:
                # Target says formula but no body anywhere — also a regression
                violations.append({
                    "check": "1g_formula_body_missing", "severity": "Error",
                    "queue_name": qname, "queue_id_target": t_q["queue_id"],
                    "schema_id": sid,
                    "target_formula_file_expected": os.path.relpath(formula_file, target_root),
                    "source_formula_body": s_node.get("formula"),
                    "target_schema_path": os.path.relpath(t_q["schema_path"], target_root),
                    "msg": f"target field '{sid}' is type='formula' but has no inline body and no formulas/{sid}.py",
                })

    summary = {
        "queues_in_both": len(in_both),
        "queues_only_in_source": only_src,
        "queues_only_in_target": only_tgt,
        "source_formula_fields_checked": fields_checked,
        "violations": len(violations),
    }
    return violations, summary


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--impl-root", required=True, help="target implementation root (e.g. uatv2/uatv2/)")
    p.add_argument("--source-root", default=None,
                   help="optional source/baseline implementation root (e.g. prodbackup/prod/). "
                        "Enables cross-tree check 1g (formula preservation across upgrade).")
    p.add_argument("--out", required=True, help="output phase1-static.json")
    args = p.parse_args()

    impl_root = args.impl_root.rstrip("/")
    if not os.path.isdir(impl_root):
        print(f"impl-root not a dir: {impl_root}", file=sys.stderr)
        sys.exit(2)
    source_root = args.source_root.rstrip("/") if args.source_root else None
    if source_root and not os.path.isdir(source_root):
        print(f"source-root not a dir: {source_root}", file=sys.stderr)
        sys.exit(2)

    hook_idx = index_local_hooks(impl_root)
    rule_idx = index_local_rules(impl_root)

    queue_results = []
    for qdir, qf, sf in find_queue_dirs(impl_root):
        queue_results.append(check_queue(qdir, qf, sf, hook_idx, rule_idx, impl_root))

    cross_hook_violations = check_hook_run_after(hook_idx)

    all_violations = []
    for qr in queue_results:
        for v in qr.get("violations") or []:
            all_violations.append((v, qr["queue_dir"]))
    for v in cross_hook_violations:
        all_violations.append((v, "<cross-hook>"))

    formula_preservation = None
    if source_root:
        fp_violations, fp_summary = check_formula_preservation(source_root, impl_root)
        formula_preservation = {"summary": fp_summary, "violations": fp_violations}
        for v in fp_violations:
            all_violations.append((v, f"<1g:{v.get('queue_name')}>"))

    output = {
        "queue_count": len(queue_results),
        "hook_count": len(hook_idx),
        "rule_count": len(rule_idx),
        "total_violations": len(all_violations),
        "violations_by_check": dict(Counter(v["check"] for v, _ in all_violations)),
        "queues": queue_results,
        "cross_hook_violations": cross_hook_violations,
        "formula_preservation": formula_preservation,
    }
    json.dump(output, open(args.out, "w"), indent=2)

    print(f"\nQueues: {output['queue_count']}, Hooks indexed: {output['hook_count']}, Rules indexed: {output['rule_count']}")
    print(f"Total violations: {output['total_violations']}")
    if output["violations_by_check"]:
        for check, count in sorted(output["violations_by_check"].items(), key=lambda x: -x[1]):
            print(f"  {count:>4}  {check}")
    if formula_preservation:
        s = formula_preservation["summary"]
        print(f"\n1g formula preservation (cross-tree):")
        print(f"  queues matched: {s['queues_in_both']}")
        print(f"  source formula-fields checked: {s['source_formula_fields_checked']}")
        print(f"  violations: {s['violations']}")
        if s.get("queues_only_in_source"):
            print(f"  queues only in source: {s['queues_only_in_source'][:5]}{'...' if len(s['queues_only_in_source'])>5 else ''}")
        if s.get("queues_only_in_target"):
            print(f"  queues only in target: {s['queues_only_in_target'][:5]}{'...' if len(s['queues_only_in_target'])>5 else ''}")


if __name__ == "__main__":
    main()
