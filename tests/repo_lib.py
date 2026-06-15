"""Shared helpers for the repo-level guard tests. Standard library only.

The marketplace's own CLAUDE.md states two invariants that drift in practice:
  * README must stay in sync with the skills / MCP tools that exist.
  * The plugin.json version must match the MCP server's serverInfo version.
These helpers let the tests compute the ground truth from the filesystem so the
guards enforce those invariants mechanically.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
RSA_SKILLS = PLUGINS / "rossum-sa" / "skills"
SERVER_PY = PLUGINS / "rossum-sa" / "mcp-servers" / "rossum-api" / "server.py"
RSA_PLUGIN_JSON = PLUGINS / "rossum-sa" / ".claude-plugin" / "plugin.json"
README = ROOT / "README.md"


def parse_frontmatter(skill_md: Path) -> dict[str, str]:
    """Parse a SKILL.md's leading YAML frontmatter block.

    The frontmatter in this repo is flat ``key: value`` pairs (values may
    themselves contain colons), so we split on the first colon only and avoid a
    YAML dependency. Returns {} when there is no frontmatter block.
    """
    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.startswith((" ", "\t")):
            # Skip blank lines and continuation/list lines — none of the keys
            # the guards care about (name, description, user-invocable) use them.
            continue
        key, _, val = line.partition(":")
        fields[key.strip()] = val.strip().strip('"').strip("'")
    return fields


def iter_skills(plugin: str) -> list[tuple[str, Path]]:
    """All (skill_name, SKILL.md path) under a plugin's skills/ dir.

    Only directories that actually contain a SKILL.md count as skills — this
    skips __shared/ and any stray dirs (e.g. leftover __pycache__).
    """
    skills_dir = PLUGINS / plugin / "skills"
    out: list[tuple[str, Path]] = []
    if not skills_dir.is_dir():
        return out
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            out.append((child.name, skill_md))
    return out


def is_reference(skill_md: Path) -> bool:
    """A reference pack is an autoloaded, non-invocable skill."""
    return parse_frontmatter(skill_md).get("user-invocable", "").lower() == "false"


def rsa_skill_counts() -> tuple[int, int, list[str]]:
    """(invocable_count, reference_count, invocable_names) for rossum-sa."""
    invocable, reference = [], []
    for name, skill_md in iter_skills("rossum-sa"):
        (reference if is_reference(skill_md) else invocable).append(name)
    return len(invocable), len(reference), sorted(invocable)


def mcp_tool_count() -> int:
    """Number of @_tool-decorated handlers in the MCP server."""
    return len(re.findall(r"@_tool\b", SERVER_PY.read_text(encoding="utf-8")))


def server_version() -> str | None:
    text = SERVER_PY.read_text(encoding="utf-8")
    # Prefer the module-level _SERVER_VERSION constant (canonical source of truth).
    m = re.search(r'^_SERVER_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if m:
        return m.group(1)
    # Fall back to a literal version string in the serverInfo dict.
    m = re.search(
        r'"serverInfo"\s*:\s*\{[^}]*"version"\s*:\s*"([^"]+)"',
        text,
    )
    return m.group(1) if m else None
