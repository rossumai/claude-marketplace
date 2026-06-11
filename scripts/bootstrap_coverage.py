"""Bootstrap the API inventory + first-pass coverage map, and print where we stand.

Run from the repo root: python scripts/bootstrap_coverage.py
Writes data/api-inventory.json and data/coverage-map.json (auto-seeded), then
prints a summary so we can see current coverage before any manual classification.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from api_inventory import build, coverage, fetch  # noqa: E402

DATA = ROOT / "data"
SERVER = ROOT / "plugins/rossum-sa/mcp-servers/rossum-api/server.py"


def main() -> int:
    DATA.mkdir(exist_ok=True)
    inv = build.extract_inventory(fetch.fetch_spec())
    (DATA / "api-inventory.json").write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")

    endpoints = coverage.extract_tool_endpoints(SERVER.read_text(encoding="utf-8"))
    cmap = coverage.seed_coverage_map(inv, endpoints)
    (DATA / "coverage-map.json").write_text(
        json.dumps(cmap, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summ = coverage.summarize(inv, cmap)
    print(f"inventory:            {len(inv)} operations")
    print(f"tool endpoints found: {len(endpoints)} (across the MCP server)")
    print(f"coverage:             {summ}")

    pending_ops = [op for op in inv
                   if (op["method"], coverage.normalize_path(op["path"])) not in endpoints]
    pending_by_tag = Counter(op["tag"] for op in pending_ops)
    print("\npending operations by tag (top 20):")
    for tag, n in pending_by_tag.most_common(20):
        print(f"  {n:3}  {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
