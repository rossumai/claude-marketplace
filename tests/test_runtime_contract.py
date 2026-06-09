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
