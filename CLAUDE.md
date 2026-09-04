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
- Repo-level guard tests live in `tests/`: `test_manifests.py` (manifests parse, every SKILL.md has frontmatter), `test_doc_sync.py` (README headline + skills table match the filesystem; plugin/server versions match), `test_runtime_contract.py` (server stays stdlib-only), `test_parts.py` (the parts library: metadata schema, `«param»`↔placeholder parity, and each `fragment.*` parses once its seams are filled). Per-skill suites live under `plugins/.../skills/<name>/tests/`.
- **Part fragments are `«param»` templates, not runnable source** — the guillemet seams are deliberately invalid in every target language, so `parts/**` is excluded from `ruff` (see `pyproject.toml`). Fragment syntax is checked by `test_parts.py` against the *filled-in* form, not the linter. Any new lint/validate step must also exclude `parts/**`.
- The doc-sync and version guards mechanize the two sync rules below — if you add/rename a skill or tool, the guard fails until README is updated in the same change.
- Per-skill script suites follow the testing bar in `docs/testing-skill-scripts.md` — scale to blast radius, pin behavior not implementation, prune in the same PR that grows a suite past the bar.

## Rules

- **README.md and README-internal.md must always stay in sync with the project.** When adding, removing, or renaming skills or MCP tools, update README.md to reflect the change in the same commit. README-internal.md contains internal development prompts for maintaining the plugin.
- **Version strings must stay in sync.** When bumping the version, update both `plugins/rossum-sa/.claude-plugin/plugin.json` and the `serverInfo` version in `plugins/rossum-sa/mcp-servers/rossum-api/server.py`.
- **New or modified MCP tools must be tested against the real API.** After implementing or updating a tool, call it via the MCP connection with valid arguments derived from live data (use IDs from list endpoints to feed into get endpoints, use existing collection names for Data Storage calls). For write/destructive tools, create a temporary resource, verify it exists, then clean it up. Do not consider a tool done until it passes a live call.
- **Skill references must track the tool surface.** When adding, removing, or renaming an MCP tool — or changing a coverage classification — scan `plugins/rossum-sa/skills/**/*.md` in the same PR for references that need updating: (a) names of removed/renamed tools, and (b) "no tool exists / use a raw request" guidance that a new tool now covers (e.g. the generic `rossum_get`).
- **A dedicated MCP tool must clear the promotion bar; the generic `rossum_get` is the default.** A dedicated tool has to do something the generic GET can't: (1) write safely, (2) compose multiple calls, (3) trim a large payload, (4) carry non-trivial inputs used often, or (5) signpost a workflow the model should reach for unprompted. Everything else is `rossum_get`'s job — the GETs with no dedicated tool outnumber the wrapped ones several times over (current split: the `implicit` count in `api-coverage.md`), and wrapping them individually would bloat the tool list and *degrade* tool-selection accuracy. The bar cuts both ways: a plain `{id}` GET with no trimming, no composition, and no core-spine role fails every criterion and should be deleted (five were, in the `rossum_get` change), after grepping skills, reference packs, and tests for the tool name. `get_queue`/`get_hook`/`get_schema`/`get_rule` stay as spine objects. The `User-Agent` + `X-Rossum-MCP-Tool` headers were meant to make promotion data-driven, but **no telemetry sink was ever built and the headers are not read back** — so apply the bar by manual evaluation against a live org, never by claiming usage data.
- **Incorporating the API-coverage watch issue is a two-baseline job — record decisions before refreshing the snapshot.** The watch issue (`.github/workflows/api-coverage.yml` → `scripts/run_coverage_check.py`, read-only) reports two independent things: *Changes since the committed snapshot* = live spec vs `data/api-inventory.json`, and *Pending operations* = uncovered **writes** vs the curated `data/coverage-map.json` (GETs with no tool are implicitly covered by `rossum_get` and never pending). New endpoints therefore appear in both lists. Evaluate the new operations first and write the verdict into `coverage-map.json` — `covered` (with `tools`), `not_planned`, `deprecated`, or an explicit `pending` with a `reason` when a write is worth wrapping later but not now; a write left absent is implicitly pending, and a GET left absent is implicitly covered by `rossum_get` — then run `python scripts/run_coverage_check.py --apply` once to refresh the snapshot **and** regenerate `plugins/rossum-sa/skills/rossum-reference/api-coverage.md` in the same PR. `--apply` never reseeds the coverage map. Whether **your token** may call an endpoint is a separate question from coverage — a 403 in evaluation is not a `not_planned` verdict.
