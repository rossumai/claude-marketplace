"""Guard: files referenced by manifests and skills actually exist.

Catches a moved/renamed/deleted helper that would break a skill or the MCP
server at runtime while every other check stays green.
"""
from __future__ import annotations

import json
import re

import pytest

import repo_lib as R

_MD_LINK = re.compile(r"\]\(([^)]+)\)")


def _all_skill_mds():
    skills = []
    for plugin_dir in R.PLUGINS.iterdir():
        if plugin_dir.is_dir():
            skills += R.iter_skills(plugin_dir.name)
    return skills


def _plugin_jsons():
    return sorted(R.PLUGINS.glob("*/.claude-plugin/plugin.json"))


@pytest.mark.parametrize("plugin_json", _plugin_jsons(), ids=lambda p: p.parent.parent.name)
def test_mcp_server_paths_exist(plugin_json):
    """Every ${CLAUDE_PLUGIN_ROOT}-relative path in mcpServers resolves to a file."""
    plugin_dir = plugin_json.parent.parent
    data = json.loads(plugin_json.read_text("utf-8"))
    for server_name, cfg in (data.get("mcpServers") or {}).items():
        for arg in cfg.get("args", []):
            if "${CLAUDE_PLUGIN_ROOT}" not in arg:
                continue
            rel = arg.replace("${CLAUDE_PLUGIN_ROOT}", "").lstrip("/")
            target = plugin_dir / rel
            assert target.is_file(), (
                f"{plugin_dir.name} mcpServers.{server_name} points at "
                f"missing file: {arg} -> {target}"
            )


@pytest.mark.parametrize(
    "name,skill_md", _all_skill_mds(), ids=[n for n, _ in _all_skill_mds()]
)
def test_skill_local_links_resolve(name, skill_md):
    """Relative markdown links in a SKILL.md must point at files that exist."""
    missing = []
    for raw in _MD_LINK.findall(skill_md.read_text("utf-8")):
        link = raw.split()[0]  # drop any "(path \"title\")" title
        link = link.split("#")[0]  # drop #fragment
        if not link or link.startswith(("http://", "https://", "mailto:", "#")) or "${" in link:
            continue
        if not (skill_md.parent / link).exists():
            missing.append(link)
    assert not missing, f"{name}/SKILL.md links to missing file(s): {missing}"
