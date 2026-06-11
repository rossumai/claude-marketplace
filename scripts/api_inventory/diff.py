"""Diff two API inventories into added / removed / changed operations.

Keyed on (method, path). `changed` compares operationId + tag and IGNORES
summary-only edits (description churn). Deeper request/response schema diffs are
out of scope by design — the compact inventory doesn't carry shapes.
"""
from __future__ import annotations


def _key(op: dict) -> tuple:
    return (op["method"], op["path"])


def _sig(op: dict) -> tuple:
    return (op.get("operationId"), op.get("tag"))


def diff_inventories(old: list[dict], new: list[dict]) -> dict:
    """Return {added, removed, changed} between two inventories."""
    old_by = {_key(o): o for o in old}
    new_by = {_key(o): o for o in new}
    added = [new_by[k] for k in new_by if k not in old_by]
    removed = [old_by[k] for k in old_by if k not in new_by]
    changed = []
    for k in new_by:
        if k in old_by and _sig(old_by[k]) != _sig(new_by[k]):
            a, b = old_by[k], new_by[k]
            changed.append({
                "method": k[0], "path": k[1],
                "before": {"operationId": a.get("operationId"), "tag": a.get("tag")},
                "after": {"operationId": b.get("operationId"), "tag": b.get("tag")},
            })
    return {
        "added": sorted(added, key=lambda o: (o["path"], o["method"])),
        "removed": sorted(removed, key=lambda o: (o["path"], o["method"])),
        "changed": sorted(changed, key=lambda c: (c["path"], c["method"])),
    }
