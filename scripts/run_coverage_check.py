"""Scheduled API-coverage run (issue #46): build the watch-issue body.

Read-only by default — it computes the diff vs the committed snapshot, the pending
backlog, and any stale coverage-map entries, and writes issue-body.md. It does NOT
commit anything: a maintainer incorporates the changes into the snapshot, the
autoloaded doc, and any new tools via a normal PR.

Pass --apply (the human "incorporate" step) to also refresh the committed
snapshot (data/api-inventory.json) and the doc (api-coverage.md) from the live
spec + the existing curated coverage map — never reseeding the map.

Emits `surface=true|false` to $GITHUB_OUTPUT (if set) so the workflow only
creates/edits the issue when there is something to surface.
"""
from __future__ import annotations

import argparse
import json
import os
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the API-coverage watch issue body.")
    ap.add_argument("--apply", action="store_true",
                    help="also refresh the committed snapshot + doc (human incorporate step)")
    args = ap.parse_args(argv)

    new = build.extract_inventory(fetch.fetch_spec())
    old = json.loads(SNAPSHOT.read_text("utf-8")) if SNAPSHOT.exists() else []
    cmap = json.loads(CMAP.read_text("utf-8")) if CMAP.exists() else {}

    d = diff.diff_inventories(old, new)
    pending = coverage.pending_operations(new, cmap)
    stale = coverage.stale_coverage_entries(new, cmap)

    ISSUE_BODY.write_text(render_issue.render_issue_body(d, pending, stale), encoding="utf-8")
    surface = render_issue.has_content(d, pending, stale)

    if args.apply:
        SNAPSHOT.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        DOC.write_text(render_doc.render_coverage_doc(new, cmap), encoding="utf-8")

    print(f"operations: {len(new)}  pending: {len(pending)}  stale: {len(stale)}")
    print(f"diff vs snapshot — added: {len(d['added'])}  "
          f"changed: {len(d['changed'])}  removed: {len(d['removed'])}")
    for kind in ("added", "removed", "changed"):
        for op in d[kind]:
            print(f"  {kind:7} {op['method']} {op['path']}")
    print(f"surface: {surface}")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"surface={'true' if surface else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
