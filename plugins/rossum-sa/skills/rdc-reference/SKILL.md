---
name: rdc-reference
description: rdc (Rossum Deployment as Code) CLI reference — the newer alternative to prd2 for snapshotting a Rossum org as files and deploying it. Covers telling an rdc project from a prd2 project, the envs/<env>/ tree with slug-named files and rdc:// references, `rdc sync` (three-way merge, dry-run, --no-push audit, --no-pull deploy, --allow-deletes, conflict shadows), `rdc migrate` promotion with overlay.toml and .rdc/mapping.toml, `rdc auth`, `rdc doctor`, the testkit formula/hook harness, and how the rossum-sa skills map onto an rdc tree. Use whenever the working directory has an rdc.toml, or the user mentions rdc, rdc sync, rdc migrate, overlay.toml, _index.md, or rdc:// refs.
user-invocable: false
---

# rdc (Rossum Deployment as Code) Reference

`rdc` is a single-binary CLI that mirrors a Rossum org's configuration as plain files under `envs/<env>/` and reconciles them with the remote in one command. It replaces prd2's pull / push / deploy trio with `sync` and `migrate`. Full details in [reference.md](reference.md).

## First: which tool owns this project?

Decide from the files, never from habit. The two trees are **not** interchangeable — running the wrong tool against a project corrupts its state or does nothing useful.

| Marker at the project root | Tool | Load |
|---|---|---|
| `rdc.toml` + `envs/` + `.rdc/` | **rdc** | this pack |
| `prd_config.yaml` (+ `<org>/<subdir>/` dirs with `_[<id>]` file names) | **prd2** | `prd-reference` |

Never run `prd2` in an rdc project or `rdc` in a prd2 project. If both markers exist, ask the user which one is authoritative.

## Safety rules

- **`rdc sync <env>` writes to the remote.** Without flags it pulls *and* pushes. Treat it like `prd2 push`: run `rdc sync <env> --dry-run` first, show the plan, and wait for explicit approval before the real run.
- **Read-only against the remote:** `rdc sync <env> --dry-run`, and `rdc sync <env> --no-push` (pulls remote changes into local files, never writes the org — the rdc equivalent of `prd2 pull`). Both are fine without asking; `--no-push` does rewrite local files, so check `git status` first.
- **Never pass `--allow-deletes` or `--conflict keep-local` / `use-remote` on your own.** Deletes and conflict resolution each need the user's explicit say-so. `--yes` does not authorise deletes.
- `rdc migrate` and `rdc doctor` make **zero remote calls** but rewrite local files — preview with `--dry-run`, review with `git diff`.
- Claude's shell is non-TTY, so rdc behaves as if `--yes` were passed: conflicts fall back to shadow files (`<file>.<env>`), pending deletes make the sync refuse. Report those; do not resolve them silently.

## Editing rules

- **Code lives in sidecars.** Hook code is `hooks/<slug>.py` / `.js`, a rule's trigger condition is `rules/<slug>.py`, a formula is `workspaces/<ws>/queues/<q>/formulas/<field_id>.py`. The JSON carries no `code` / `formula` body — never add one.
- **References are `rdc://<kind>/<slug>`, not API URLs** (`"queues": ["rdc://queues/invoices"]`). Write new references in that form, pointing at slugs that exist.
- **Find things through `envs/<env>/_index.md`** — the generated inventory of every object, its path, name and cross-references. Do not hand-edit it.
- **Never edit** `_index.md`, `.rdc/state/*`, `secrets/`, or the generated `<!-- >>> rdc:… -->` regions of `CLAUDE.md` / `README.md` / `.gitlab-ci.yml`.
- **After a direct API write** (any `rossum_patch_*` / `rossum_create_*` MCP call), run `rdc sync <env> --no-push` so the change lands in the snapshot and git — otherwise the next sync sees it as remote drift.
- **Test formulas and hooks locally** with `pytest -q` (the `testkit/` harness runs the real txscript runtime) before syncing.

## Credentials

`rdc` reads `RDC_TOKEN_<SUFFIX>` (or `RDC_USER_<SUFFIX>` + `RDC_PASS_<SUFFIX>`) where the suffix is listed in the project's `CLAUDE.md` env table, and caches a validated token in `secrets/<env>.secrets.json`. When a token is missing or expired, ask the user to run `! rdc auth <env>` (reads the token from stdin) — never put a token on the command line or in an inline env assignment.

Use this knowledge when:
- The working directory contains `rdc.toml`, or the user mentions rdc, `rdc sync`, `rdc migrate`, `overlay.toml`, `.rdc/mapping.toml`, `_index.md`, or `rdc://` references
- Pulling, pushing, promoting, or auditing a Rossum configuration in an rdc project
- Mapping a prd2-era instruction (pull / push / deploy file / attribute override) onto rdc
