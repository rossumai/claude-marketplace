"""Guard: marketplace/plugin manifests parse, and every skill has frontmatter.

This is the check that would have caught the nerossum skill shipping with no
YAML frontmatter (which prevents it from registering at all).
"""
from __future__ import annotations

import json

import pytest

import repo_lib as R


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


@pytest.mark.parametrize("plugin_dir", _plugin_dirs(), ids=lambda p: p.name)
def test_plugin_json_is_valid(plugin_dir):
    data = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert data.get("name"), f"{plugin_dir.name}/plugin.json missing name"
    assert data.get("version"), f"{plugin_dir.name}/plugin.json missing version"
    assert data["name"] == plugin_dir.name, (
        f"plugin.json name {data['name']!r} != directory name {plugin_dir.name!r}"
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
