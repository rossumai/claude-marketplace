"""Guard: README headline + skills table stay in sync with the filesystem,
and the plugin version matches the MCP server's serverInfo version.

Mechanizes the two "must stay in sync" rules in CLAUDE.md. The headline counts
refer to the rossum-sa toolkit: invocable skills, autoloaded reference packs,
and @_tool-decorated MCP tools.
"""
from __future__ import annotations

import json
import re

import repo_lib as R

# The "·" separators are U+00B7 middle dots, as written in the README.
_HEADLINE = re.compile(
    r"(\d+)\s+skills\s+·\s+(\d+)\s+reference packs\s+·\s+(\d+)\s+MCP tools"
)


def test_readme_headline_matches_filesystem():
    invocable, reference, _ = R.rsa_skill_counts()
    tools = R.mcp_tool_count()
    m = _HEADLINE.search(R.README.read_text(encoding="utf-8"))
    assert m, "README headline 'N skills · M reference packs · K MCP tools' not found"
    got = tuple(int(x) for x in m.groups())
    assert got == (invocable, reference, tools), (
        f"README headline {got} != filesystem "
        f"({invocable} skills, {reference} reference packs, {tools} MCP tools)"
    )


def test_every_invocable_skill_is_documented():
    _, _, invocable_names = R.rsa_skill_counts()
    readme = R.README.read_text(encoding="utf-8")
    missing = [n for n in invocable_names if f"rossum-sa:{n}" not in readme]
    assert not missing, f"invocable skills absent from README: {missing}"


def test_plugin_version_matches_server_version():
    plugin_version = json.loads(R.RSA_PLUGIN_JSON.read_text("utf-8"))["version"]
    assert R.server_version() == plugin_version, (
        f"plugin.json version {plugin_version!r} != "
        f"server.py serverInfo version {R.server_version()!r}"
    )


def test_readme_feedback_row_mentions_anonymous_channel():
    readme = R.README.read_text(encoding="utf-8")
    row = next(
        (ln for ln in readme.splitlines() if "rossum-sa:plugin-feedback" in ln), "")
    assert row, "plugin-feedback row missing from README"
    assert "anonymous" in row.lower(), (
        "README's plugin-feedback row must mention the anonymous channel"
    )


def test_readme_internal_has_form_repin_runbook():
    internal = (R.ROOT / "README-internal.md").read_text(encoding="utf-8")
    assert "## Re-pin the feedback form" in internal, (
        "README-internal.md must document the form re-pin procedure"
    )
