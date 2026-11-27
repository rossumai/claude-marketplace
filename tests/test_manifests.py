"""Guard: marketplace/plugin manifests parse, and every skill has frontmatter.

The frontmatter check catches a skill shipping with no YAML frontmatter
(which prevents it from registering at all).
"""
from __future__ import annotations

import json
import re

import pytest
import yaml

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


def _raw_frontmatter_block(skill_md) -> str | None:
    """Return the raw text between a SKILL.md's leading ``---`` markers.

    Unlike R.parse_frontmatter, this does no interpretation — it hands the
    exact text to a real YAML parser.
    """
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:i])
    return None


@pytest.mark.parametrize(
    "name,skill_md", _all_skill_mds(), ids=[n for n, _ in _all_skill_mds()]
)
def test_skill_frontmatter_is_valid_yaml(name, skill_md):
    """R.parse_frontmatter is a deliberately lenient first-colon splitter (see
    its docstring), so it happily accepts frontmatter that a real YAML
    consumer — e.g. GitHub's SKILL.md renderer — rejects outright. That gap let
    five SKILL.md files ship with frontmatter GitHub couldn't render. Parse the
    raw block with an actual YAML parser here so that class of bug fails CI.
    """
    raw = _raw_frontmatter_block(skill_md)
    assert raw is not None, f"{skill_md} has no YAML frontmatter block"
    try:
        yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        pytest.fail(
            f"{skill_md} frontmatter is not valid YAML: {exc}\n"
            "Note: the line/column YAML reports is usually misleading here — "
            "the real cause is almost always an unquoted value elsewhere in "
            "the block that starts with '[' (parsed as a flow sequence) or "
            "contains a colon followed by a space (ends the plain scalar "
            "early, e.g. 'block automation: native Rossum...'). Quote the "
            "whole value instead of reformatting it."
        )


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


# --- Anthropic skill-authoring rules -------------------------------------------------
# https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
# name: <=64 chars, lowercase/digits/hyphens, no reserved words; description: <=1024
# chars, no XML tags; SKILL.md body <500 lines; reference files >100 lines open with a
# table of contents; relative links between skill files must resolve.

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_XML_TAG_RE = re.compile(r"<[A-Za-z/][^>]*>")
_LINK_RE = re.compile(r"\]\(([^)#:\s]*\.md)?(?:#([^)\s]+))?\)")


def _heading_slugs(path):
    """GitHub-style anchors for a Markdown file's headings (fenced code ignored, -N for repeats)."""
    slugs, seen, fence = set(), {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(```|~~~)", line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        h = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if fence is None and h:
            base = re.sub(r"[^\w\- ]", "", h.group(1).lower()).replace(" ", "-")
            n = seen.get(base, 0)
            slugs.add(base if n == 0 else f"{base}-{n}")
            seen[base] = n + 1
    return slugs


@pytest.mark.parametrize(
    "name,skill_md", _all_skill_mds(), ids=[n for n, _ in _all_skill_mds()]
)
def test_skill_meets_anthropic_frontmatter_rules(name, skill_md):
    fm = yaml.safe_load(_raw_frontmatter_block(skill_md))
    assert len(name) <= 64 and _NAME_RE.match(name), f"{name}: invalid skill name"
    assert not re.search(r"anthropic|claude", name), f"{name}: reserved word in name"
    desc = str(fm["description"])
    assert len(desc) <= 1024, f"{name}: description is {len(desc)} chars (max 1024)"
    assert not _XML_TAG_RE.search(desc), f"{name}: description contains an XML-like tag"


@pytest.mark.parametrize(
    "name,skill_md", _all_skill_mds(), ids=[n for n, _ in _all_skill_mds()]
)
def test_skill_body_under_500_lines(name, skill_md):
    # The frontmatter ends at the first line that is exactly '---' — a '---' inside a value doesn't count.
    lines_all = skill_md.read_text(encoding="utf-8").splitlines()
    end = next(i for i, ln in enumerate(lines_all[1:], start=1) if ln.strip() == "---")
    lines = len(lines_all) - end - 1
    assert lines < 500, f"{name}/SKILL.md body is {lines} lines — move detail to a reference file"


def _skill_reference_files():
    out = []
    for _, skill_md in _all_skill_mds():
        for md in sorted(skill_md.parent.rglob("*.md")):
            if md.name != "SKILL.md" and "tests" not in md.parts:
                out.append(md)
    return out


@pytest.mark.parametrize("ref", _skill_reference_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_long_reference_file_opens_with_contents(ref):
    lines = ref.read_text(encoding="utf-8").splitlines()
    if len(lines) <= 100:
        return
    head = [ln.strip().lower() for ln in lines[:30]]  # "at the top": after the title and a short intro
    assert any(h in ("## contents", "## table of contents") for h in head), (
        f"{ref} is {len(lines)} lines — add a '## Contents' list in its first 30 lines"
    )


@pytest.mark.parametrize(
    "md", [s for _, s in _all_skill_mds()] + _skill_reference_files(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_relative_markdown_links_resolve(md):
    """Every relative .md link points at an existing file, and its #section at a real heading."""
    problems = []
    for target, anchor in _LINK_RE.findall(md.read_text(encoding="utf-8")):
        if not target and not anchor:
            continue
        path = (md.parent / target).resolve() if target else md
        if not path.exists():
            problems.append(f"missing file {target}")
        elif anchor and anchor.lower() not in _heading_slugs(path):
            problems.append(f"no heading for #{anchor} in {path.name}")
    assert not problems, f"{md}: " + "; ".join(problems)
