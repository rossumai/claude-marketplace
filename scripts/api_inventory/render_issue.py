"""Render the rolling 'API coverage — watch' tracking-issue body, deterministically.

The body has three parts:
  1. Changes since the committed snapshot — added / changed / removed operations.
  2. Stale coverage-map entries — decisions whose endpoint no longer exists.
  3. Pending backlog — operations with no MCP tool yet, grouped by tag.

A leading digest marker (over all three, sorted, no timestamps) lets the workflow
no-op when nothing changed, so quiet weeks produce no notification. The workflow
commits nothing — a maintainer incorporates the changes into the snapshot, the
`api-coverage.md` doc, and any new tools via a normal human-authored PR.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

DIGEST_PREFIX = "<!-- coverage-digest:"


def _digest(diff: dict, pending_ops: list[dict], stale: list[str]) -> str:
    keys = []
    for kind in ("added", "changed", "removed"):
        keys += [f"{kind} {o['method']} {o['path']}" for o in diff.get(kind, [])]
    keys += [f"pending {o['method']} {o['path']}" for o in pending_ops]
    keys += [f"stale {k}" for k in stale]
    return hashlib.sha1("\n".join(sorted(keys)).encode("utf-8")).hexdigest()[:12]


def has_content(diff: dict, pending_ops: list[dict], stale: list[str]) -> bool:
    """True if there's anything worth opening/keeping an issue for (pending_ops = uncovered writes only; reads are implicitly covered by rossum_get)."""
    return bool(diff.get("added") or diff.get("changed") or diff.get("removed")
                or pending_ops or stale)


def render_issue_body(diff: dict, pending_ops: list[dict], stale: list[str]) -> str:
    digest = _digest(diff, pending_ops, stale)
    added, changed, removed = diff.get("added", []), diff.get("changed", []), diff.get("removed", [])

    lines = [
        f"{DIGEST_PREFIX} {digest} -->", "",
        "# Rossum API coverage — watch", "",
        "_Auto-maintained by the API coverage workflow (read-only — it commits "
        "nothing). A maintainer incorporates the changes below into the snapshot, "
        "the `api-coverage.md` doc, and any new tools via a normal PR. Full status "
        "table: `plugins/rossum-sa/skills/rossum-reference/api-coverage.md`._", "",
        f"## Changes since the committed snapshot "
        f"({len(added)} added · {len(changed)} changed · {len(removed)} removed)", "",
    ]
    if added or changed or removed:
        for o in added:
            s = f" — {o['summary']}" if o.get("summary") else ""
            lines.append(f"- 🟢 added `{o['method']} {o['path']}`{s}")
        for c in changed:
            b, a = c["before"], c["after"]
            lines.append(f"- 🟡 changed `{c['method']} {c['path']}` "
                         f"({b['operationId']}/{b['tag']} → {a['operationId']}/{a['tag']})")
        for o in removed:
            lines.append(f"- 🔴 removed `{o['method']} {o['path']}`")
    else:
        lines.append("_No API changes since the last incorporated snapshot._")
    lines.append("")

    if stale:
        lines += [
            f"## Stale coverage-map entries ({len(stale)})", "",
            "_These have a decision in `coverage-map.json` but no longer exist in the "
            "API — clean them up:_",
        ]
        lines += [f"- `{k}`" for k in stale]
        lines.append("")

    by_tag: dict[str, list] = defaultdict(list)
    for op in pending_ops:
        by_tag[op.get("tag") or "(untagged)"].append(op)
    lines += [f"## Pending operations — uncovered writes, no MCP tool yet ({len(pending_ops)})", "",
              "_Reads are covered generically by `rossum_get`; only writes are tracked here._", ""]
    for tag in sorted(by_tag):
        ops = sorted(by_tag[tag], key=lambda o: (o["path"], o["method"]))
        lines.append(f"### {tag} ({len(ops)})")
        for o in ops:
            s = f" — {o['summary']}" if o.get("summary") else ""
            lines.append(f"- `{o['method']} {o['path']}`{s}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def extract_digest(body: str) -> str | None:
    """Pull the digest out of an existing issue body (for no-op comparison)."""
    for line in (body or "").splitlines():
        if line.startswith(DIGEST_PREFIX):
            return line[len(DIGEST_PREFIX):].split("-->")[0].strip()
    return None
