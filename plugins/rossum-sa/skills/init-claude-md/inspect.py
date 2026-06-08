#!/usr/bin/env python3
"""Inspect a Rossum prd2 project tree and emit JSON facts.

Usage: python3 inspect.py <project_dir>

Output: a single JSON object on stdout with project facts. Stderr is reserved
for errors. No remote API calls; pure local file reading. Designed to be
called by the init-claude-md skill, which renders the facts into CLAUDE.md.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def parse_prd_config(project_dir: Path) -> dict:
    """Parse prd_config.yaml without a yaml dependency.

    The file is tightly structured (project_name + directories map). We only
    need project_name and the top-level keys under directories. A minimal
    line-based parser is enough and avoids adding PyYAML as a dep.
    """
    cfg_path = project_dir / "prd_config.yaml"
    if not cfg_path.is_file():
        return {"project_name": project_dir.name, "environments": []}

    project_name = project_dir.name
    environments: list[str] = []
    in_directories = False
    for raw in cfg_path.read_text().splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if re.match(r"^project_name\s*:", line):
            project_name = line.split(":", 1)[1].strip().strip('"').strip("'")
            continue
        if re.match(r"^directories\s*:\s*$", line):
            in_directories = True
            continue
        if in_directories:
            # Top-level env entry: exactly 2 spaces of indent then `name:`.
            m = re.match(r"^  ([^\s:]+)\s*:\s*$", line)
            if m:
                environments.append(m.group(1))
            elif not line.startswith(" "):
                in_directories = False

    return {"project_name": project_name, "environments": environments}


FIELD_CATS = {"datapoint", "multivalue", "tuple", "button"}


def count_schema_fields(schema: dict) -> int:
    """Count leaf field nodes in a schema's content tree."""
    n = 0

    def walk(node: object) -> None:
        nonlocal n
        if isinstance(node, dict):
            if node.get("category") in FIELD_CATS:
                n += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema.get("content", []))
    return n


def discover_queues(project_dir: Path, environments: list[str]) -> list[dict]:
    """Walk environments → workspaces → queues; collect facts per queue."""
    out: list[dict] = []
    for env in environments:
        env_dir = project_dir / env
        for q_json in sorted(env_dir.glob("workspaces/Workspace_*/queues/Queue_*/queue.json")):
            queue = json.loads(q_json.read_text())
            workspace_json = q_json.parent.parent.parent / "workspace.json"
            workspace_name = (
                json.loads(workspace_json.read_text()).get("name", "")
                if workspace_json.is_file() else ""
            )
            schema_path = q_json.parent / "schema.json"
            field_count = (
                count_schema_fields(json.loads(schema_path.read_text()))
                if schema_path.is_file() else 0
            )
            out.append({
                "name": queue.get("name", q_json.parent.name),
                "workspace": workspace_name,
                "environment": env,
                "schema_field_count": field_count,
            })
    return out


def main(project_dir_str: str) -> None:
    project_dir = Path(project_dir_str).resolve()
    if not project_dir.is_dir():
        print(f"error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(2)

    facts = parse_prd_config(project_dir)
    queues = discover_queues(project_dir, facts["environments"])
    workspaces = {(q["environment"], q["workspace"]) for q in queues}
    facts["queues"] = queues
    facts["queue_count"] = len(queues)
    facts["workspace_count"] = len(workspaces)

    json.dump(facts, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: inspect.py <project_dir>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
