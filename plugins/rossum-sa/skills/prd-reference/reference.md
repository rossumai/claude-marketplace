# prd2 (Project Rossum Deploy) Reference

prd2 is a CLI tool for tracking changes, deploying, and releasing Rossum platform configurations across environments. Source: https://github.com/rossumai/prd

IMPORTANT: The original prd (v1) from the `deployment-manager` package is deprecated. Always use prd2. Key differences: prd2 uses `prd_config.yaml` (not `credentials.json`), per-directory `credentials.yaml` (not centralized JSON), deploy YAML files (not `mapping.yaml`-based release), and supports additional object types (rules, labels, engines, email templates, workflows).

## Terms

- **source** — dev/test environment where the project is built and tested
- **target** — production (or next-stage) environment where the project is deployed
- **local** — files on the local machine (git repository)
- **remote** — objects in Rossum platform (the API)
- **org directory** — local directory mapped to a Rossum organization (e.g., `sandbox-org/`, `production-org/`)
- **subdirectory** — environment within an org directory (e.g., `dev/`, `test/`, `default/`)

## Prerequisites

- git
- Python 3.12+
- Rossum account with admin role credentials

## Installation

```bash
brew install pipx
pipx ensurepath
pipx install project-rossum-deploy
```

Reinstall: `pipx install project-rossum-deploy --force`

Self-update: `prd2 update`

## Commands

### `prd2 init [name]`

Creates a new project directory with basic files, initializes a git repo, and creates a `.gitignore`. Interactively prompts for organization directories, org IDs, API base URLs, tokens, and subdirectories with optional regex filters.

Default name: `.` (current directory).

### `prd2 pull [destinations...] [options]`

Downloads Rossum objects from remote organizations to local files.

```bash
prd2 pull sandbox-org --all        # pull entire org, overwrite local
prd2 pull sandbox-org/dev          # pull specific subdirectory
prd2 pull sandbox-org production-org  # pull multiple orgs
```

| Option | Description |
|--------|-------------|
| `-a` / `--all` | Download all remote files, overwriting local |
| `-c` / `--commit` | Auto-commit pulled changes |
| `-m` / `--message` | Custom commit message (default: "Sync changes to local") |
| `-s` / `--skip-objects-without-subdir` | Skip objects whose subdirectory cannot be determined |
| `--concurrency` | Max concurrent API requests |

Behavior:
- Compares `modified_at` timestamps to avoid overwriting unversioned local changes
- Removes local files for objects deleted in Rossum
- Extracts formula field code into separate `.py` files in `formulas/` directories
- Extracts hook code into separate `.py`/`.js` files alongside hook JSON
- **Editing rule:** Always edit the extracted `.py` file, never the JSON. This applies to both hook code (`.py` file next to the hook JSON — never edit the `code` field) AND formula code (`formulas/*.py` — never edit the `formula` property in `schema.json`). The `.py` file is the single source of truth — `prd2 push` merges it back into the JSON automatically.
- Supports all object types: organizations, workspaces, queues, inboxes, schemas, hooks, labels, rules, engines, engine fields, email templates, workflows, workflow steps

### `prd2 push [destinations...] [options]`

Uploads locally changed files to Rossum. Supports **update**, **create**, **delete**, and **rename** of Rossum objects.

```bash
prd2 push sandbox-org/dev          # push changes to dev
prd2 push sandbox-org/dev -f       # force push, ignore timestamps
prd2 push sandbox-org/dev -io      # push only git-indexed files
prd2 push sandbox-org/dev -y       # skip the create/delete confirmation prompt
```

| Option | Description |
|--------|-------------|
| `-a` / `--all` | Upload all local files, not just modified ones |
| `-f` / `--force` | Ignore newer remote timestamps, overwrite remote |
| `-io` / `--indexed-only` | Push only files added to git index |
| `-c` / `--commit` | Auto-commit after push |
| `-m` / `--message` | Custom commit message (default: "Pushed changes to remote") |
| `-y` / `--yes` | Skip the confirmation prompt shown when the plan contains CREATE or DELETE operations (required for non-interactive shells / CI) |
| `--concurrency` | Max concurrent API requests |

Behavior:
- Uses `git status` to detect changed files
- Merges formula field code and hook code changes back into their JSON before pushing
- Compares `modified_at` timestamps unless `-f` is used
- When the change set contains any CREATE or DELETE, prints a plan and prompts for confirmation before applying. Use `-y` to skip the prompt
- Validates up front: required fields per type, missing parent objects, hook runtime/code mismatch, duplicate names, "create under a folder being deleted". Errors are reported before any API call
- On per-directory failure, the post-push pull and the auto-commit are skipped so the working tree is left dirty for inspection
- Otherwise, automatically does a `pull` after successful push to sync timestamps

#### Creating new objects (`_[]` placeholder)

To create a brand-new object via push, name the file or folder with the `_[]` placeholder instead of `_[<id>]`:

```
workspaces/MyWorkspace_[]/                   # new workspace folder
  workspace.json                             #   {"name": "...", "organization": "<url>"}
  queues/MyQueue_[]/                         # new queue folder under it
    queue.json
    schema.json                              # required sibling of any new queue
    inbox.json                               # optional; needs email_prefix or email
hooks/MyHook_[].json                         # new hook
hooks/MyHook_[].py                           # serverless code companion
rules/MyRule_[].json                         # new rule
engines/MyEngine_[]/
  engine.json
  engine_fields/MyField_[].json
```

Notes on the placeholder convention:
- The placeholder lives on the segment that owns the object's identity: the folder name for workspace/queue/engine; the file stem for hook/rule/engine_field.
- `schema.json` and `inbox.json` keep their fixed filenames — they're identified by living inside a queue folder (placeholder or already-real).
- A descendant inherits its newness from the placeholder ancestor: a `schema.json` under `MyQueue_[]/` is treated as new even though the file's own name has no `_[]`.
- The created object's id/url is written back to JSON after POST and the file/folder is renamed to `_[<real-id>]` automatically. The local working tree is mutated in place.
- For a new object, the JSON must NOT contain `id` or `url` — push will reject it with a clear error.
- CREATEs run sequentially in dependency order: `workspace → engine → engine_field → schema → queue → inbox → hook → rule`. Within a single push you can create a whole workspace + queues + schemas + inbox + hooks atomically — parent/sibling URLs (`queue.workspace`, `queue.schema`, `schema.queues`, `inbox.queues`, `engine_field.engine`) are wired up automatically by walking the local filesystem, so you don't write them by hand.

Required fields the user must populate on the JSON for new objects (filesystem-derived ones are filled in automatically):

| Type | Required |
|------|----------|
| Workspace | `name`, `organization` |
| Queue | `name` (sibling `schema.json` must exist) |
| Schema | `name`, `content` |
| Inbox | `name`, one of `email_prefix` or `email` |
| Hook | `name`, `events`, `queues`, `config` (and `config.runtime` if any code is present) |
| Rule | `name` |
| Engine | `name`, `description` |
| EngineField | `name` |

#### Deleting objects

Delete the file (or folder) locally and run push. Push detects the deletion via `git status`, recovers the object's id from the `_[<id>]` segment in the path, and issues the DELETE. For `schema.json` and `inbox.json` (no id in the filename), the id is recovered from git history (staged or HEAD) — files that were never committed cannot be deleted via push.

DELETEs run in reverse dependency order: `rule → hook → inbox → queue → schema → engine_field → engine → workspace`. Queue DELETE uses `?delete_after=0` (skips Rossum's soft-delete grace window) and polls until the queue returns 404 before deleting its schema/inbox, so cascade-related 409s are avoided. If the queue isn't gone within ~30s the rest of the push aborts so the user can re-run later.

#### Renames

`git mv old new` (or any rename) is detected as a paired delete + create with the same id. The planner collapses the pair into a single PATCH on `name` — no DELETE/POST round-trip, no orphaned children. The timestamp check is skipped for the rename UPDATE because the path-keyed `non_versioned_object_attributes.json` has no entry for the post-rename path yet.

#### Plan output

The plan is rendered **only when the change set contains at least one CREATE or DELETE**. A push that contains only UPDATEs prints no plan and skips the confirmation prompt — it just applies the updates as before.

When a plan is rendered, it groups operations into `WILL CREATE`, `WILL UPDATE`, `WILL DELETE` (each in resolved dependency order). The `WILL UPDATE` section therefore only appears alongside structural ops; updates that ride along on a create/delete push are surfaced here so the user sees the full picture before confirming.

Rossum 4xx field-validation errors are pretty-printed as a per-field list rather than a raw HTTP error.

### `prd2 deploy template create [options]`

Interactive wizard that creates a deploy YAML file. Prompts for source/target directories, workspace selection, queue selection (with schema/inbox auto-detection), hook selection, rule selection, engine selection, attribute overrides, secrets file, and deploy state file.

| Option | Description |
|--------|-------------|
| `-mf` / `--mapping-file` | PRD v1 mapping file for reusing IDs and attribute overrides |

### `prd2 deploy template update <deploy_file> [options]`

Updates an existing deploy YAML file, adding new objects or modifying existing ones.

```bash
prd2 deploy template update -i ./deploy_files/dev_test.yaml
```

| Option | Description |
|--------|-------------|
| `-i` / `--interactive` | Allow interactive changes |
| `-mf` / `--mapping-file` | PRD v1 mapping file for reusing IDs |

### `prd2 deploy template reverse <deploy_file>`

Creates a reversed deploy file (swaps source and target) for reverse deployment.

### `prd2 deploy run <deploy_file> [options]`

Executes a deployment based on a deploy YAML file.

```bash
prd2 deploy run --prefer=source ./deploy_files/dev_test.yaml
prd2 deploy run -y ./deploy_files/test_prod.yaml  # auto-apply
```

| Option | Description |
|--------|-------------|
| `--prefer` | Conflict resolution: `source` or `target` |
| `--no-rebase` | Skip rebase prompts from target |
| `-y` / `--auto-apply` | Apply plan without confirmation |
| `-c` / `--commit` | Auto-commit after deploy |
| `-m` / `--message` | Custom commit message (default: "Deployed changes to target organization") |
| `--concurrency` | Max concurrent API requests |

Behavior:
1. Validates deploy file
2. Authenticates source and target clients
3. Initializes all deploy objects (org, hooks, labels, email templates, rules, engines, workspaces, queues)
4. Performs 3-way merge comparison (last applied state vs current source vs remote target)
5. Shows deploy plan with colorized diffs for each object
6. Two-phase deploy: first creates/updates objects, second pass resolves cross-references
7. Saves deploy state and timestamps
8. Pulls target directory to sync local files

Not automatically migrated (must be set manually for newly created objects):
- `queue.dedicated_engine` and `queue.generic_engine`
- `queue.users` and `queue.workflows`
- `hook.secrets` (use `secrets_file` in deploy file instead)

### `prd2 deploy revert <deploy_file> [options]`

Deletes all target objects found in the deploy file. Shows a plan first and requires confirmation.

| Option | Description |
|--------|-------------|
| `-c` / `--commit` | Auto-commit after revert |
| `-m` / `--message` | Custom commit message |

### `prd2 hook payload <hook_path> [options]`

Generates a hook payload by calling the Rossum API.

| Option | Description |
|--------|-------------|
| `-au` / `--annotation-url` | URL or ID of annotation for payload |

### `prd2 hook test <hook_path> [options]`

Tests a hook locally using the Rossum API test endpoint. Uses the latest local `.py` code if available.

| Option | Description |
|--------|-------------|
| `-pp` / `--payload-path` | Path to payload JSON |
| `-au` / `--annotation-url` | Annotation URL for auto-generated payload |

### `prd2 hook sync template`

Creates a new sync template YAML for hook synchronization with a remote Git repository.

### `prd2 hook sync run <sync_file>`

Syncs local hook Python files with a remote Git source.

### `prd2 purge [object_types...] [options]`

Destructive deletion of objects from a Rossum organization. Shows a deletion plan and prompts for confirmation.

```bash
prd2 purge all                    # delete everything
prd2 purge unused_schemas         # delete schemas not assigned to queues
prd2 purge hooks workspaces       # delete specific object types
```

Object types: `workspaces`, `queues`, `hooks`, `schemas`, `inboxes`, `email_templates`, `labels`, `rules`, `engines`, `engine_fields`, `workflows`, `workflow_steps`, `all`, `unused_schemas`.

| Option | Description |
|--------|-------------|
| `--concurrency` | Max concurrent API requests |

### `prd2 update [version_tag]`

Self-updates prd2 from GitHub releases. Optionally specify a version tag.

## Configuration Files

### prd_config.yaml

Main project configuration. Created by `prd2 init`. Maps local directories to Rossum organizations.

```yaml
directories:
  sandbox-org:
    org_id: '285704'
    api_base: https://my-org.rossum.app/api/v1
    subdirectories:
      dev:
        regex: ''
      test:
        regex: ''
  production-org:
    org_id: '293441'
    api_base: https://my-org.rossum.app/api/v1
    subdirectories:
      default:
        regex: ''
```

- Each key under `directories` is a local folder name
- `org_id` — Rossum organization ID
- `api_base` — Rossum API base URL
- `subdirectories` — environments within the org, each with optional `regex` filter for routing objects

### credentials.yaml

Per-organization directory. Contains the API token. Never committed to git (auto-added to `.gitignore`).

```yaml
token: <YOUR_TOKEN>
```

Each org directory (and optionally each subdirectory) has its own `credentials.yaml`.

### Deploy File (YAML)

Stored in `deploy_files/`. Defines what to deploy from source to target and how.

```yaml
target_url: https://my-org.rossum.app/api/v1
source_dir: sandbox-org/dev
target_dir: sandbox-org/test
source_url: https://my-org.rossum.app/api/v1

token_owner_id:
deployed_org_id: 285704
patch_target_org: false

secrets_file: deploy_secrets/dev_test_secrets.json
deploy_state_file: deploy_states/dev_test_fb3154.json
last_deployed_at: '2026-03-02T09:58:46.704109Z'

workspaces:
  - id: 700852
    name: DEV Workspace
    targets:
      - id: 743213
        attribute_override:
          name: TEST Workspace

queues:
  - id: 2137275
    name: Cost Invoices (AT)
    base_path: sandbox-org/dev/workspaces/DEV Workspace_[700852]
    ignore_deploy_warnings: false
    targets:
      - id: 2278122
        attribute_override: {}
    schema:
      id: 1824379
      targets:
        - id: 1917224
          attribute_override: {}
    inbox:
      id: 813566
      targets:
        - id: 771322
          attribute_override: {}

hooks:
  - id: 856489
    name: 'Validator: invoices (DEV)'
    targets:
      - id: 898551
        attribute_override:
          name: \(DEV\)$/#/(TEST)

rules:
  - id: 2597
    name: E-invoice Validation Warning
    targets:
      - id: 2716
        attribute_override: {}

engines: []
unselected_hooks: []
```

Key fields:
- `target_url` / `source_url` — API base URLs
- `source_dir` / `target_dir` — local directory paths
- `deployed_org_id` — auto-set on first deploy, validates subsequent deploys go to the same org
- `patch_target_org` — whether to update target org attributes from source
- `secrets_file` — path to deploy secrets JSON
- `deploy_state_file` — path to deploy state snapshot (for 3-way merge)
- `targets` — array of target mappings (supports 1:N deployment)
- `target_id: null` — object will be created on first deploy
- `base_path` — for queues, the local filesystem path to the queue's parent workspace
- `ignore_deploy_warnings` — suppress non-critical warnings for this object
- `unselected_hooks` — hook IDs to exclude from deployment even if attached to selected queues

### Deploy Secrets (JSON)

Stored in `deploy_secrets/`. Maps hook names to their secrets (SFTP keys, API tokens, etc.). Never committed to git.

```json
{
  "SFTP import: hourly master data (DEV)_[847529]": {
    "type": "sftp",
    "ssh_key": "-----BEGIN RSA PRIVATE KEY-----\n..."
  }
}
```

### Deploy State (JSON)

Stored in `deploy_states/`. Snapshot of the last deployed state for each object. Used for 3-way merge during subsequent deploys to detect conflicts between local changes and remote changes.

## Attribute Override

Used in deploy files under `targets[].attribute_override` to modify attributes when deploying to target.

### Static override

Directly set a value:

```yaml
attribute_override:
  name: My Production Hook
```

### Regex replacement with `/#/` separator

Replace parts of a value using regex. Pattern: `<regex_match>/#/<replacement>`.

```yaml
attribute_override:
  name: \(DEV\)$/#/(TEST)
  # "Validator: invoices (DEV)" → "Validator: invoices (TEST)"
```

Can also match at the start of strings:

```yaml
attribute_override:
  settings.import_rules[*].dataset_name: ^DEV_/#/TEST_
  # "DEV_vendors" → "TEST_vendors"
```

### `$prd_ref`

Replaces source IDs or URLs with their target counterparts from the deploy mapping:

```yaml
attribute_override:
  settings.configurations[*].queue_ids: $prd_ref
```

### `$source_value`

References the original source value. Useful for composing target-specific values:

```yaml
attribute_override:
  name: "$source_value - PROD"
```

Override keys use JMESPath query syntax for nested paths and array wildcards (e.g., `settings.configurations[*].queue_ids`, `content[*].children[?(@.id == 'my_field')].formula`).

## Project Folder Structure

```
project_root/
  prd_config.yaml                        # main project configuration
  deploy_files/                          # deploy YAML templates
    dev_test.yaml
    test_prod.yaml
  deploy_secrets/                        # secrets for deployments (gitignored)
    dev_test_secrets.json
  deploy_states/                         # state snapshots from past deploys
    dev_test_abc123.json
  sandbox-org/                           # org directory
    credentials.yaml                     # API token (gitignored)
    organization.json                    # org-level metadata
    dev/                                 # subdirectory (environment)
      organization.json
      non_versioned_object_attributes.json
      hooks/
        HookName_[ID].json
        HookName_[ID].py                 # or .js (serverless code)
      labels/
        LabelName_[ID].json
      rules/
        RuleName_[ID].json
      engines/
        EngineName_[ID].json
      workspaces/
        WorkspaceName_[ID]/
          workspace.json
          queues/
            QueueName_[ID]/
              queue.json
              schema.json
              inbox.json
              formulas/                  # formula field code
                field_id.py
              email_templates/
                TemplateName_[ID].json
    test/                                # another subdirectory
      (same structure as dev)
  production-org/                        # another org directory
    credentials.yaml
    organization.json
    default/
      (same structure)
```

Key differences from prd v1:
- Schemas are stored inside queue directories as `schema.json` (not in a separate `schemas/` directory)
- Rules, labels, engines, and email templates are tracked as separate object types
- Each org directory has its own `credentials.yaml`
- No top-level `mapping.yaml` — deployment mappings are in `deploy_files/`

## Important Notes

- prd2 does NOT automatically make git commits — use `-c` flag to opt in
- Always run `prd2 pull` before making local edits to ensure latest remote state
- After creating or modifying resources via MCP/API (not through prd2), always run `prd2 pull` to sync local files — never hand-write hook JSON files, let prd2 be the source of truth for local file state
- Use `prd2 deploy run --prefer=source` to prefer local source changes over remote target on conflicts
- Use `prd2 deploy run -p` (plan-only equivalent) or review the shown plan before confirming
- The concurrency limit defaults to 5 parallel requests (override with `--concurrency` or `PRD2_CONCURRENCY` env var)
- After `prd2 purge`, run `prd2 pull` to sync local state

## Typical Workflow

1. `prd2 init my-project` — create project, configure orgs and subdirectories
2. Add API tokens to `credentials.yaml` in each org directory
3. `prd2 pull sandbox-org --all` — download current state
4. Make changes in Rossum UI or edit local files
5. `prd2 pull sandbox-org/dev` — sync any remote changes
6. `prd2 push sandbox-org/dev` — upload local changes to source
7. Test in source environment
8. `prd2 deploy template create` — create deploy file (first time)
9. `prd2 deploy template update -i ./deploy_files/dev_test.yaml` — update deploy file if objects changed
10. `prd2 deploy run --prefer=source ./deploy_files/dev_test.yaml` — deploy to target
11. Verify in target environment
