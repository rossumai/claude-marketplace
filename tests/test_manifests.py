"""Guard: marketplace/plugin manifests parse, and every skill has frontmatter.

The frontmatter check catches a skill shipping with no YAML frontmatter
(which prevents it from registering at all).
"""
from __future__ import annotations

import json

import pytest

import repo_lib as R

# Known keys for the Claude Code plugin manifests. An unknown top-level key is
# almost always a typo (e.g. "mcpServer" for "mcpServers") that silently breaks
# loading — the structural checks below use .get() and would never notice. So we
# reject unknown keys. Adding a genuinely new manifest key is a deliberate edit
# to these sets.
MARKETPLACE_REQUIRED = {"name", "plugins"}
MARKETPLACE_OPTIONAL = {"owner", "metadata"}
PLUGIN_ENTRY_REQUIRED = {"name", "source"}
PLUGIN_ENTRY_OPTIONAL = {
    "description", "version", "author", "category", "strict",
    "keywords", "license", "homepage",
}
PLUGIN_JSON_REQUIRED = {"name", "version"}
PLUGIN_JSON_OPTIONAL = {
    "description", "author", "homepage", "repository", "license", "keywords",
    "commands", "agents", "hooks", "mcpServers", "skills",
}
# SKILL.md frontmatter. A typo here (e.g. "allowed-tool", "user-invokable")
# silently drops a tool restriction or invocability flag, so reject unknowns.
SKILL_FM_REQUIRED = {"name", "description"}
SKILL_FM_OPTIONAL = {
    "argument-hint", "allowed-tools", "user-invocable", "context",
    "model", "disable-model-invocation",
}


def _check_keys(obj, required, optional, where):
    keys = set(obj)
    missing = required - keys
    assert not missing, f"{where}: missing required key(s) {sorted(missing)}"
    unknown = keys - required - optional
    assert not unknown, (
        f"{where}: unknown key(s) {sorted(unknown)} — typo? "
        f"allowed: {sorted(required | optional)}"
    )


def _plugin_dirs():
    return sorted(p.parent.parent for p in R.PLUGINS.glob("*/.claude-plugin/plugin.json"))


def test_marketplace_json_is_valid():
    data = json.loads((R.ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    assert data.get("name"), "marketplace.json must have a name"
    assert isinstance(data.get("plugins"), list) and data["plugins"], "must list plugins"
    for plugin in data["plugins"]:
        assert plugin.get("name"), f"plugin entry missing name: {plugin}"
        assert plugin.get("source"), f"plugin {plugin.get('name')} missing source"
        src = (R.ROOT / plugin["source"]).resolve()
        assert src.is_dir(), f"plugin source path does not exist: {plugin['source']}"


def test_marketplace_lists_every_plugin_on_disk():
    """Every plugin/ dir is registered in marketplace.json, and vice versa —
    catches a plugin added on disk but never registered, or a dangling entry."""
    data = json.loads((R.ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    registered = {p["name"] for p in data["plugins"]}
    on_disk = {d.name for d in _plugin_dirs()}
    assert registered == on_disk, (
        f"marketplace.json registers {sorted(registered)} "
        f"but plugins/ on disk are {sorted(on_disk)}"
    )


def test_marketplace_json_known_keys():
    """Reject unknown/typo'd keys in marketplace.json and its plugin entries."""
    data = json.loads((R.ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    _check_keys(data, MARKETPLACE_REQUIRED, MARKETPLACE_OPTIONAL, "marketplace.json")
    for i, plugin in enumerate(data["plugins"]):
        _check_keys(
            plugin, PLUGIN_ENTRY_REQUIRED, PLUGIN_ENTRY_OPTIONAL,
            f"marketplace.json plugins[{i}] ({plugin.get('name', '?')})",
        )


@pytest.mark.parametrize("plugin_dir", _plugin_dirs(), ids=lambda p: p.name)
def test_plugin_json_is_valid(plugin_dir):
    data = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert data.get("name"), f"{plugin_dir.name}/plugin.json missing name"
    assert data.get("version"), f"{plugin_dir.name}/plugin.json missing version"
    assert data["name"] == plugin_dir.name, (
        f"plugin.json name {data['name']!r} != directory name {plugin_dir.name!r}"
    )


@pytest.mark.parametrize("plugin_dir", _plugin_dirs(), ids=lambda p: p.name)
def test_plugin_json_known_keys(plugin_dir):
    """Reject unknown/typo'd top-level keys in plugin.json (e.g. mcpServer)."""
    data = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    _check_keys(
        data, PLUGIN_JSON_REQUIRED, PLUGIN_JSON_OPTIONAL,
        f"{plugin_dir.name}/plugin.json",
    )


def _all_skill_mds():
    skills = []
    for plugin_dir in R.PLUGINS.iterdir():
        if plugin_dir.is_dir():
            skills += R.iter_skills(plugin_dir.name)
    return skills


@pytest.mark.parametrize(
    "name,skill_md", _all_skill_mds(), ids=[n for n, _ in _all_skill_mds()]
)
def test_skill_has_frontmatter(name, skill_md):
    fm = R.parse_frontmatter(skill_md)
    assert fm, f"{skill_md} has no YAML frontmatter block (it won't register)"
    assert fm.get("name"), f"{skill_md} frontmatter missing 'name'"
    assert fm.get("description"), f"{skill_md} frontmatter missing 'description'"


@pytest.mark.parametrize(
    "name,skill_md", _all_skill_mds(), ids=[n for n, _ in _all_skill_mds()]
)
def test_skill_name_matches_directory(name, skill_md):
    """Skills resolve by directory name, so frontmatter name must match it —
    catches a renamed dir whose frontmatter drifted (or vice versa)."""
    fm = R.parse_frontmatter(skill_md)
    assert fm.get("name") == name, (
        f"{skill_md.parent.name}/SKILL.md frontmatter name {fm.get('name')!r} "
        f"!= directory name {name!r}"
    )


@pytest.mark.parametrize(
    "name,skill_md", _all_skill_mds(), ids=[n for n, _ in _all_skill_mds()]
)
def test_skill_frontmatter_known_keys(name, skill_md):
    """Reject unknown/typo'd SKILL.md frontmatter keys (e.g. allowed-tool)."""
    _check_keys(
        R.parse_frontmatter(skill_md), SKILL_FM_REQUIRED, SKILL_FM_OPTIONAL,
        f"{name}/SKILL.md frontmatter",
    )
