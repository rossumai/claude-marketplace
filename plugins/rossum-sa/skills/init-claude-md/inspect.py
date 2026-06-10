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


def detect_tool(project_dir: Path) -> str:
    """Identify which deployment tool manages this project. Extensible: add a branch
    here, a sibling inspector, and a fragments/<tool>.md to support another deployment
    tool/format. Currently only prd2 is supported."""
    if (project_dir / "prd_config.yaml").is_file():
        return "prd2"
    return "unknown"


def parse_prd_config(project_dir: Path) -> dict:
    """Parse prd_config.yaml without a yaml dependency.

    Captures project_name, the top-level directory entries (Rossum orgs), and the
    subdirectories declared under each. prd2's machine-generated 2-space indentation
    is assumed; nested subdirectory keys (e.g. regex) are intentionally ignored.
    """
    cfg_path = project_dir / "prd_config.yaml"
    if not cfg_path.is_file():
        return {"project_name": project_dir.name, "environments": [], "directories": []}

    project_name = project_dir.name
    directories: list[dict] = []
    cur = None
    in_directories = False
    in_subdirs = False
    for raw in cfg_path.read_text().splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        key = stripped.split(":", 1)[0].strip()
        if indent == 0:
            if re.match(r"^project_name\s*:", line):
                project_name = line.split(":", 1)[1].strip().strip('"').strip("'")
            in_directories = key == "directories"
            cur = None
            in_subdirs = False
        elif not in_directories:
            continue
        elif indent == 2 and stripped.endswith(":"):
            cur = {"name": key, "subdirs": []}
            directories.append(cur)
            in_subdirs = False
        elif indent == 4 and cur is not None:
            in_subdirs = key == "subdirectories"
        elif indent == 6 and cur is not None and in_subdirs:
            cur["subdirs"].append(key)

    return {
        "project_name": project_name,
        "environments": [d["name"] for d in directories],
        "directories": directories,
    }


def _object_roots(project_dir: Path, directory: dict) -> list[Path]:
    """Roots to search for objects: <org>/<subdir>/ per declared subdirectory, or
    <org>/ as a fallback when none are declared (keeps subdir-less fixtures working)."""
    base = project_dir / directory["name"]
    if directory["subdirs"]:
        return [base / sd for sd in directory["subdirs"]]
    return [base]


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


def discover_queues(project_dir: Path, directories: list[dict]) -> list[dict]:
    """Walk each org's object roots → workspaces → queues; collect facts per queue."""
    out: list[dict] = []
    for d in directories:
        for root in _object_roots(project_dir, d):
            for q_json in sorted(root.glob("workspaces/*/queues/*/queue.json")):
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
                    "environment": d["name"],
                    "schema_field_count": field_count,
                })
    return out


COUPA_PATTERNS = (
    re.compile(r"\bCIB\b"),
    re.compile(r"\bcoupa\b", re.IGNORECASE),
    re.compile(r"Coupa Integration Baseline", re.IGNORECASE),
)
SAP_PATTERNS = (
    re.compile(r"\bINVOIC0[12]\b"),
    re.compile(r"\bIDOC\b"),
    re.compile(r"\bSAP\b"),
)
SFI_PATTERNS = (
    re.compile(r"structured[._-]formats[._-]import", re.IGNORECASE),
    re.compile(r"\bZUGFeRD\b", re.IGNORECASE),
    re.compile(r"\bX-Rechnung\b", re.IGNORECASE),
)
SFTP_PATTERNS = (
    re.compile(r"\bfile-storage-export\b"),
    re.compile(r"\bsftp://", re.IGNORECASE),
)


def discover_hooks(project_dir: Path, directories: list[dict]) -> list[dict]:
    """Discover hooks under each org's object roots: <root>/hooks/*.json."""
    out: list[dict] = []
    for d in directories:
        for root in _object_roots(project_dir, d):
            hooks_dir = root / "hooks"
            if not hooks_dir.is_dir():
                continue
            for hook_json in sorted(hooks_dir.glob("*.json")):
                hook = json.loads(hook_json.read_text())
                runtime = ((hook.get("config") or {}).get("runtime")) or ""
                out.append({
                    "name": hook.get("name", hook_json.stem),
                    "type": hook.get("type", ""),
                    "runtime": runtime,
                    "environment": d["name"],
                    "queue_count": len(hook.get("queues") or []),
                })
    return out


def detect_integration_target(project_dir: Path, environments: list[str]) -> str:
    """First match wins: Coupa → SAP → SFI → SFTP → REST → unknown.

    Scans hook JSON, hook .py files, and any top-level export-pipeline configs.
    """
    blobs: list[str] = []
    for env in environments:
        env_dir = project_dir / env
        if not env_dir.is_dir():
            continue
        for path in env_dir.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".py"}:
                try:
                    blobs.append(path.read_text(errors="ignore"))
                except OSError:
                    continue
    haystack = "\n".join(blobs)

    if any(p.search(haystack) for p in COUPA_PATTERNS):
        return "Coupa"
    if any(p.search(haystack) for p in SAP_PATTERNS):
        return "SAP"
    if any(p.search(haystack) for p in SFI_PATTERNS):
        return "SFI"
    if any(p.search(haystack) for p in SFTP_PATTERNS):
        return "SFTP"
    # Generic REST detection: presence of a call_api block in any hook config.
    if re.search(r'"call_api"\s*:', haystack):
        return "Generic REST"
    return "unknown"


def main(project_dir_str: str) -> None:
    project_dir = Path(project_dir_str).resolve()
    if not project_dir.is_dir():
        print(f"error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(2)

    tool = detect_tool(project_dir)
    if tool != "prd2":
        json.dump({"tool": tool, "supported": False}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    facts = parse_prd_config(project_dir)
    facts["tool"] = "prd2"
    directories = facts["directories"]
    queues = discover_queues(project_dir, directories)
    hooks = discover_hooks(project_dir, directories)
    workspaces = {(q["environment"], q["workspace"]) for q in queues}
    facts["queues"] = queues
    facts["queue_count"] = len(queues)
    facts["workspace_count"] = len(workspaces)
    facts["hooks"] = hooks
    facts["hook_count"] = len(hooks)
    facts["integration_target"] = detect_integration_target(project_dir, facts["environments"])

    json.dump(facts, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: inspect.py <project_dir>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
