# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code plugin marketplace for Rossum.ai workflows (https://code.claude.com/docs/en/plugin-marketplaces). Ships one plugin:

- **`rossum-sa`** — Skills, reference packs, and an MCP server for Rossum SA work

## Architecture

**Marketplace** → **Plugins** → **Skills + MCP servers**

- `.claude-plugin/marketplace.json` lists available plugins
- Each plugin lives under `plugins/<name>/` with its own `.claude-plugin/plugin.json`
- Skills are Markdown files at `plugins/<name>/skills/<skill-name>/SKILL.md`
- The `rossum-sa` plugin ships invocable skills (analyze, dead-code, document, implement, etc.) plus autoloaded `*-reference` reference packs (`user-invocable: false`). README.md is the authoritative list and count — the CI doc-sync guard keeps it accurate, so don't hardcode counts here.

**MCP server** (`plugins/rossum-sa/mcp-servers/rossum-api/server.py`):
- Single-file Python, zero external dependencies (stdlib `urllib` + optional `certifi` for SSL) — the `tests/test_runtime_contract.py` guard enforces the stdlib-only contract
- Implements MCP JSON-RPC over stdio (reads/writes newline-delimited JSON on stdin/stdout)
- Tools are registered via the `@_tool` decorator which populates `TOOLS` and `HANDLERS` dicts
- Three annotation levels control permission prompts: `_READ_ONLY`, `_WRITE`, `_DESTRUCTIVE`
- Manages its own auth state (`_cached_token`, `_cached_base_url`) — no persistent credentials
- All Rossum API calls go through `_http_request()` which handles auth, errors, and 401 invalidation
- Pagination is handled by `_paginate()` for list endpoints and `_rossum_list()` wrapper

## Tests & CI

- `.github/workflows/ci.yml` runs `ruff` (real-errors-only) + `pytest` on Python 3.12 and 3.14 for every PR and push to `main`.
- Run locally with `pip install -r requirements-dev.txt && pytest`.
- Repo-level guard tests live in `tests/`: `test_manifests.py` (manifests parse, every SKILL.md has frontmatter), `test_doc_sync.py` (README headline + skills table match the filesystem; plugin/server versions match), `test_runtime_contract.py` (server stays stdlib-only). Per-skill suites live under `plugins/.../skills/<name>/tests/`.
- The doc-sync and version guards mechanize the two sync rules below — if you add/rename a skill or tool, the guard fails until README is updated in the same change.

## Rules

- **README.md and README-internal.md must always stay in sync with the project.** When adding, removing, or renaming skills or MCP tools, update README.md to reflect the change in the same commit. README-internal.md contains internal development prompts for maintaining the plugin.
- **Version strings must stay in sync.** When bumping the version, update both `plugins/rossum-sa/.claude-plugin/plugin.json` and the `serverInfo` version in `plugins/rossum-sa/mcp-servers/rossum-api/server.py`.
- **New or modified MCP tools must be tested against the real API.** After implementing or updating a tool, call it via the MCP connection with valid arguments derived from live data (use IDs from list endpoints to feed into get endpoints, use existing collection names for Data Storage calls). For write/destructive tools, create a temporary resource, verify it exists, then clean it up. Do not consider a tool done until it passes a live call.
