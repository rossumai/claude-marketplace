"""Guard: the MCP server stays dependency-free.

CLAUDE.md promises the server is "zero external dependencies (stdlib urllib +
optional certifi for SSL)". A stray third-party import would break installs in
environments that only have the standard library, so assert it via the AST.
"""
from __future__ import annotations

import ast
import sys

import repo_lib as R

ALLOWED = set(sys.stdlib_module_names) | {"certifi"}


def _imported_top_level_modules(source: str) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            mods.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_server_imports_only_stdlib_and_certifi():
    mods = _imported_top_level_modules(R.SERVER_PY.read_text(encoding="utf-8"))
    extra = sorted(mods - ALLOWED)
    assert not extra, f"server.py imports non-stdlib modules: {extra}"


import ast
from pathlib import Path

_STDLIB_OK = {
    "__future__", "re", "os", "sys", "json", "pathlib",
}

def test_detect_friction_is_stdlib_only():
    src = (Path(__file__).resolve().parents[1]
           / "plugins/rossum-sa/hooks/detect_friction.py").read_text()
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= _STDLIB_OK, f"non-stdlib imports: {mods - _STDLIB_OK}"
