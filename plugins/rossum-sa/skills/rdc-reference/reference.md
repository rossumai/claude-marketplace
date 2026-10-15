# rdc (Rossum Deployment as Code) — Full Reference

Verified against `rdc 0.9.0` (`rdc --help`, every subcommand's `--help`, and the scaffold files `rdc init` writes). rdc is moving fast: when a flag below is missing or behaves differently, trust `rdc <command> --help` over this page, and say so.

## Version

- Single binary. Check with `which rdc` and `rdc --version`.
- git is recommended (commit before every sync and migrate) but rdc does not require it.
- `rdc upgrade` replaces the binary in place and keeps the previous one as `<install_dir>/rdc.bak` (one-shot rollback). `rdc upgrade --check` only reports; `rdc upgrade --version <v>` pins / downgrades (may need a re-sync afterwards).
- Every project's scaffold (`CLAUDE.md`, `README.md`, `.gitlab-ci.yml`) is generated from the binary's templates. `rdc init --force` (no `--env`) refreshes them — `CLAUDE.md`, `README.md` and `.gitlab-ci.yml` are **overwritten**, so commit hand edits first.

## prd2 → rdc, side by side

| Task | prd2 | rdc |
|---|---|---|
| Project marker | `prd_config.yaml` | `rdc.toml` (`[envs.<env>]` with `api_base`, `org_id`) |
| Download remote state | `prd2 pull <dir>` | `rdc sync <env> --no-push` |
| Upload local edits | `prd2 push <dir>` | `rdc sync <env>` (bidirectional) or `--no-pull` (push only) |
| Preview | push diff prompt | `rdc sync <env> --dry-run` |
| Promote env → env | `prd2 deploy run <deploy_file>` (remote) | `rdc migrate <src> <tgt>` (local files) → review `git diff` → `rdc sync <tgt>` |
| Per-env differences | deploy-file `attribute_override` | `envs/<tgt>/overlay.toml` + `envs/<tgt>/overlay/<relpath>` code shadows |
| Cross-env object pairing | deploy-file `targets` ids | automatic by slug; exceptions in `.rdc/mapping.toml` |
| File naming | `Name_[<id>].json` | `<slug>.json` (slug from the object's `name`) |
| Object references | API URLs | `rdc://<kind>/<slug>` |
| New object | `Name_[].json` placeholder | a JSON file with no lockfile entry |
| Delete object | `prd2 purge` / delete + push | delete the file, then `rdc sync <env> --allow-deletes` |
| Credentials | `credentials.yaml` / `ROSSUM_*` | `RDC_TOKEN_<SUFFIX>` or `rdc auth <env>` → `secrets/<env>.secrets.json` |
| Local tests | — | `pytest -q` via `testkit/` |

## Project layout

```
rdc.toml                                  env definitions (no project name)
CLAUDE.md, README.md, .gitlab-ci.yml      scaffold; `# >>> rdc:` / `<!-- >>> rdc:` regions are generated
testkit/                                  formula/hook test harness (real txscript runtime)
tests/                                    project tests go here
requirements-dev.txt                      pinned pytest + txscript
secrets/<env>.secrets.json                cached API token (gitignored)
secrets/<env>.hook-secrets.json           hook secret values (gitignored)
envs/<env>/
  _index.md                               generated inventory — read first, never edit
  organization.json
  overlay.toml                            optional per-env overrides applied by `rdc migrate`
  overlay/<relpath>                       optional per-env code shadows (hook/rule .py/.js, formulas/<field>.py)
  workspaces/<ws>/
    workspace.json
    queues/<q>/
      queue.json
      schema.json
      formulas/<field_id>.py              extracted from schema content
      inbox.json
      email-templates/<slug>.json
  hooks/<slug>.json  (+ <slug>.py | <slug>.js for function hooks)
  rules/<slug>.json  (+ <slug>.py trigger_condition)
  labels/<slug>.json
  engines/<engine>/engine.json, fields/<field>.json
  workflows/<workflow>/workflow.json, steps/<step>.json
  mdh/<dataset>/collection.json, indexes.json (+ data.jsonl when "data": "manual")
.rdc/
  state/<env>.lock.json                   slug ↔ id + base hashes — rdc owns it, never edit
  mapping.toml                            cross-env slug names (hand-editable)
```

Notes:
- **Slugs** are derived from object names. After a rename on the remote, `rdc doctor <env>` moves the files (cascade-aware — renaming a workspace or queue moves its whole subtree).
- **References** inside JSON are portable `rdc://<kind>/<slug>` strings — kinds seen in practice: `workspaces`, `queues`, `schemas`, `inboxes`, `hooks`, `rules`, `labels`, `engines`, `engine_fields`, `email_templates`. The object's own `url` is also written as `rdc://<kind>/<slug>`.
- **Redacted fields:** `queue.counts` is replaced by a sentinel on disk to keep diffs quiet and stripped from outgoing PATCHes.
- **MDH datasets** are part of the snapshot (`mdh/<dataset>/`) — collection and index definitions, and the rows only when the dataset is marked `"data": "manual"`.

## `rdc sync [env]`

Reconciles local snapshot and remote in one pass with a three-way merge (local · base · remote). Pulls remote edits, pushes local edits, creates new objects, writes server-assigned `id` / `url` back into new files, and regenerates `_index.md`.

| Flag | Effect |
|---|---|
| `--dry-run` | Print the plan (including a `deletes:` section) and exit. No writes anywhere. |
| `--no-push` | Audit mode: pull into local, never write the remote. |
| `--no-pull` | Deploy mode: push local edits, never overwrite local files. |
| `--allow-deletes` | Permit local tombstones → remote DELETE without per-object prompts. Mandatory in non-TTY when tombstones exist. |
| `--conflict use-remote\|keep-local\|skip` | Resolve every both-diverged conflict non-interactively. `keep-local` force-pushes local over the remote's change. |
| `--yes` | Skip prompts; conflicts → shadow files. Auto-on when stdin is not a TTY. Does **not** bypass deletes. |
| `--watch [--poll-interval 60s] [--no-poll]` | Continuous reconcile on file events + remote polling. Interactive; not for agent use. |

Without `<env>` it picks interactively, or auto-selects when `rdc.toml` has one env. **Always pass the env explicitly** — never rely on auto-selection in a multi-env project.

**Conflicts.** On a TTY the resolver offers `[k] keep local · [r] use <env> · [e] edit · [s] skip · [a] abort` (plus `[h]` hunk-by-hunk). Non-TTY: the remote version is written as a shadow file `<file>.<env>` next to the local one, and the local stays. A shadow file is unfinished work — either fold its changes into the real file or delete it; never commit it. The same drift check runs before each PATCH on the push side (`[k]` force-push, `[r]` adopt remote, `[s]` skip, `[a]` abort).

**Creating objects.** Add the JSON (and sidecar code) under the right directory. Cross-references must point at objects already known to the lockfile — e.g. a new hook's `queues` must reference queues that exist on the remote. Creates run in dependency order.

**Deleting objects.** The snapshot declares absence too: removing a file leaves a tombstone (the lockfile entry remains). The DELETE needs two acts — removing the file and either a `y` at the TTY batch prompt or `--allow-deletes`. Deletes run after creates/updates in reverse dependency order (`engine_fields → engines → labels → rules → hooks → email_templates → inboxes → queues → schemas → workspaces`). A 404 counts as success. If the remote changed since the last sync, a `[k]eep delete / [s]kip / [a]bort` resolver opens.

### Agent workflow for pushing a change

1. `git status` — know what is local-only before touching anything.
2. Edit the sidecar `.py` / JSON. Run `pytest -q` if formulas or hooks changed.
3. `rdc sync <env> --dry-run` — show the user the plan.
4. On explicit approval: `rdc sync <env>` (or `--no-pull` when the user wants local untouched).
5. Report any shadow files or refused deletes rather than resolving them.
6. Commit the resulting tree (rdc writes back ids/urls and `_index.md`).

## `rdc migrate [src] [tgt]` — promotion

Pure local, zero remote calls: copies `envs/<src>/` into `envs/<tgt>/`, renames slugs per `.rdc/mapping.toml`, rewrites `rdc://` refs from src slugs to tgt slugs, then applies the target's `overlay.toml` and `overlay/` shadows.

| Flag | Effect |
|---|---|
| `--dry-run` | Print the per-file remap and prunes; write nothing. |
| `--mirror` | Also delete target files the source lacks (local removals → remote deletes on the next `sync --allow-deletes`). Default is additive. |
| `--only <kind>/<slug>` | Limit scope; repeatable; `*` globs within the slug (`hooks/*`, `schemas/cost-*`, `*/invoices`). |
| `--migrate-score-thresholds` | Carry `score_threshold` / `default_score_threshold`. **Ignored by default** — tuned per env. |
| `--migrate-email-prefixes` | Carry inbox `email_prefix`. **Ignored by default** — promoting it re-addresses the target mailbox. |

The canonical chain (generated into each multi-env project's `CLAUDE.md`):

```sh
rdc sync <src> && rdc migrate <src> <tgt> --mirror && rdc sync <tgt> --allow-deletes
```

**Promotion order is a project rule, not an rdc rule.** A typical project promotes one hop at a time — `dev → test`, then `test → prod` — and forbids `dev → prod`: everything reaching prod must have passed through test. rdc will happily migrate any pair, so read the project's `README.md` / `CLAUDE.md` / team guide for the allowed chain and refuse to skip a stage. When the project documents nothing, assume `dev → test → prod` and ask before promoting anything else.

Team guides often write the push step as a bare `rdc sync <tgt>`. That works for a human on a TTY, because rdc asks before each batch of deletes. From an agent shell (non-TTY) the same command **refuses** when `--mirror` left deletions — report the listed deletions to the user and add `--allow-deletes` only when they approve them.

That chain **deletes** objects in `<tgt>` that `<src>` lacks. As an agent: run `rdc sync <src> --no-push`, then `rdc migrate <src> <tgt> --dry-run`, show it, then the real migrate, stop and show `git diff envs/<tgt>/`, then `rdc sync <tgt> --dry-run`, and only on approval the real sync. Drop `--mirror` / `--allow-deletes` when the target legitimately has extra objects.

### `overlay.toml`

Deliberate per-env differences only — raw ids are remapped by slug automatically.

```toml
version = 1

[hooks."*"]                        # kind-wide default
token_owner = "https://<org>.rossum.app/api/v1/users/<user-id>"

[hooks.sftp-export]                # one hook, dotted paths into the JSON
settings.credentials.host = "<target-host>"
settings.environment = "test"

[inboxes.<queue-slug>]             # inbox email prefix, set per env
email_prefix = "<prefix>"
```

Code differences go in `envs/<tgt>/overlay/<relpath>` (e.g. `overlay/hooks/<slug>.py`); a shadow that overrides no source sidecar aborts the migration.

### `.rdc/mapping.toml`

One hand-editable file for every env pair, naming each env's own slug for an object when they differ. Identical slugs need no entry. Check `rdc migrate --dry-run` to see how objects paired before editing it.

## Other commands

- **`rdc init [--env <env>=<api_base>:<org_id>]…`** — bootstrap a project, or add an env to an existing one (runs the wizard → auth → first sync). `--force` alone refreshes scaffold files only.
- **`rdc auth [env] [--token T | --username U]`** — validates and caches a token in `secrets/<env>.secrets.json` (mode 0600). With `--username` the password comes from stdin/TTY and the token expiry is 162 h. With neither, the token is read from stdin. Ask the user to run it as `! rdc auth <env>`; never put a token in argv.
- **`rdc doctor [env] [--dry-run]`** — offline: lists local changes not yet pushed, renames files whose slug no longer matches the JSON `name`, prunes orphan base-cache entries. A cheap "what's unpushed?" check: `rdc doctor <env> --dry-run`.

## Testing with `testkit/`

`pip install -r requirements-dev.txt && pytest -q`. The CI `pytest` job runs the same command and the deploy buttons depend on it.

```python
from testkit import evaluate_formula, load_hook

FORMULAS = "envs/dev/workspaces/<ws>/queues/<q>/formulas"

def test_total_is_net_plus_tax():
    assert evaluate_formula(f"{FORMULAS}/amount_total.py",
                            amount_net="100", amount_tax="21") == 121.0
```

- Field values are keyword arguments named after the schema id; the queue's `schema.json` supplies types.
- `date` fields must be `YYYY-MM-DD` — anything else reads as empty, as it would in the org.
- Table columns: `rows={"<multivalue_id>": [{...}, {...}]}`; the formula returns one value per row.
- `load_hook("envs/<env>/hooks/<slug>.py")` imports a function hook so its handlers can be called directly.

## CI (`.gitlab-ci.yml`)

Generated per env: a scheduled `archive` job that keeps the committed snapshot current, a `pytest` job, and one manual `deploy:<env>` button that runs the migrate + sync steps with `--yes`. Its header lists the CI variables to set (`RDC_TOKEN_<SUFFIX>` etc.).

## Known limitations

- **One schema and one inbox per queue on disk.** A schema or inbox shared by several queues is written once per consuming queue, with lockfile entries per queue slug pointing at the same remote id. Pushing works (the id is the truth); `rdc migrate` may show confusing diffs.
- `--watch` and the interactive resolvers need a TTY — not usable from an agent shell.

## How the rossum-sa skills apply to an rdc tree

The analysis skills read files and work on `envs/<env>/` once you translate paths (`<slug>.json` instead of `Name_[<id>].json`, `rdc://` refs instead of URLs, ids from `_index.md` or the lockfile). Skills that are tied to prd2 artefacts:

| Skill | In an rdc project |
|---|---|
| `refine-deployment` | prd2 deploy files only. rdc's equivalents are `overlay.toml` and `.rdc/mapping.toml` — edit those directly. |
| `init-claude-md` | Not needed: `rdc init` already generates `CLAUDE.md`. Put project notes under its `## Project-Specific Notes` section, which regeneration preserves. |
| `dead-code` | Point `detect.py` at `envs/<env>/` — hooks, rules, labels and orphan formulas work. **Engines are a false positive:** the detector matches queue → engine by the numeric id in an API URL, and rdc writes `"engine": "rdc://engines/<slug>"`, so every engine is reported unused. Check engines by hand against `_index.md`. |
| `iterate` | Works unchanged — it drives annotations through the MCP. Push code changes with `rdc sync <env>` instead of `prd2 push`. |
