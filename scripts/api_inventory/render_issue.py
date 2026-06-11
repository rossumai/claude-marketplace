"""Render the rolling 'API coverage — watch' tracking-issue body, deterministically.

The body is the pending backlog: API operations not yet wrapped (or classified)
by the MCP server, grouped by tag. A leading digest marker (over the pending set,
sorted, no timestamps) lets the workflow no-op when nothing changed — so quiet
days produce no notification. API additions/removals are captured separately by
the snapshot PR.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

DIGEST_PREFIX = "<!-- coverage-digest:"


def _digest(pending_ops: list[dict]) -> str:
    keys = sorted(f"{o['method']} {o['path']}" for o in pending_ops)
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:12]


def render_issue_body(pending_ops: list[dict]) -> str:
    digest = _digest(pending_ops)
    by_tag: dict[str, list] = defaultdict(list)
    for op in pending_ops:
        by_tag[op.get("tag") or "(untagged)"].append(op)

    lines = [
        f"{DIGEST_PREFIX} {digest} -->", "",
        "# Rossum API coverage — watch", "",
        f"_Auto-maintained by the API coverage workflow. **{len(pending_ops)}** API operations "
        "are not yet wrapped by the `rossum-api` MCP server (nor classified not-planned / "
        "deprecated). Full status table: "
        "`plugins/rossum-sa/skills/rossum-reference/api-coverage.md`._", "",
        f"## Pending operations ({len(pending_ops)})", "",
    ]
    for tag in sorted(by_tag):
        ops = sorted(by_tag[tag], key=lambda o: (o["path"], o["method"]))
        lines.append(f"### {tag} ({len(ops)})")
        for o in ops:
            summary = f" — {o['summary']}" if o.get("summary") else ""
            lines.append(f"- `{o['method']} {o['path']}`{summary}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def extract_digest(body: str) -> str | None:
    """Pull the digest out of an existing issue body (for no-op comparison)."""
    for line in (body or "").splitlines():
        if line.startswith(DIGEST_PREFIX):
            return line[len(DIGEST_PREFIX):].split("-->")[0].strip()
    return None
