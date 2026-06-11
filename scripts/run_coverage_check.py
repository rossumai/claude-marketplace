"""Scheduled API-coverage run (issue #46): refresh snapshot/doc + render the watch issue.

Unlike bootstrap_coverage.py (which auto-seeds the curated coverage map), this run
NEVER touches coverage-map.json — that's human-curated. It:
  1. fetches the live spec and builds a fresh inventory,
  2. diffs it against the committed snapshot (logs added/changed/removed),
  3. updates data/api-inventory.json and regenerates the autoloaded doc,
  4. writes issue-body.md (the pending backlog) for the workflow to upsert.

The workflow opens a PR for the snapshot/doc changes and upserts the issue.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from api_inventory import build, coverage, diff, fetch, render_doc, render_issue  # noqa: E402

DATA = ROOT / "data"
SNAPSHOT = DATA / "api-inventory.json"
CMAP = DATA / "coverage-map.json"
DOC = ROOT / "plugins/rossum-sa/skills/rossum-reference/api-coverage.md"
ISSUE_BODY = ROOT / "issue-body.md"


def main() -> int:
    new = build.extract_inventory(fetch.fetch_spec())
    old = json.loads(SNAPSHOT.read_text("utf-8")) if SNAPSHOT.exists() else []
    cmap = json.loads(CMAP.read_text("utf-8")) if CMAP.exists() else {}

    d = diff.diff_inventories(old, new)
    pending = coverage.pending_operations(new, cmap)

    SNAPSHOT.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    DOC.write_text(render_doc.render_coverage_doc(new, cmap), encoding="utf-8")
    ISSUE_BODY.write_text(render_issue.render_issue_body(pending), encoding="utf-8")

    print(f"operations: {len(new)}  pending: {len(pending)}")
    print(f"diff vs snapshot — added: {len(d['added'])}  "
          f"changed: {len(d['changed'])}  removed: {len(d['removed'])}")
    for kind in ("added", "removed", "changed"):
        for op in d[kind]:
            print(f"  {kind:7} {op['method']} {op['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
