"""Flatten an OpenAPI spec into a compact operation inventory.

One entry per (method, path) operation: just what we need to detect additions,
removals, and coverage — never the full spec.
"""
from __future__ import annotations

import re

_METHODS = ("get", "post", "put", "patch", "delete")


def _group(path: str) -> str:
    segs = [s for s in path.strip("/").split("/")
            if s and not re.fullmatch(r"(api|v\d+)", s)]
    return segs[0] if segs else "(root)"


def extract_inventory(spec: dict) -> list[dict]:
    """Return sorted [{method, path, operationId, summary, tag}] for every operation."""
    out = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method not in _METHODS or not isinstance(op, dict):
                continue
            tags = op.get("tags") or []
            out.append({
                "method": method.upper(),
                "path": path,
                "operationId": op.get("operationId", ""),
                "summary": op.get("summary", ""),
                "tag": tags[0] if tags else _group(path),
            })
    return sorted(out, key=lambda o: (o["path"], o["method"]))
