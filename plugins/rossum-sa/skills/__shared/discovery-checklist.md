# Rossum Implementation Discovery Checklist

Use this checklist to systematically discover all components of a Rossum implementation. Implementations typically follow a multi-environment directory structure.

**Prefer built-in tools over scripts for document introspection.** When exploring project files, configs, or data structures, use Grep, Glob, and Read instead of writing and executing Python (or other) scripts. Scripts add unnecessary files and runtime dependencies; the built-in tools are faster and leave no artifacts.

## Discovery Process

Use the provided path (or current directory if none given). Discover and internalize all components before producing any output.

1. **Project structure** — environments (dev/test/prod), organizations, workspaces
2. **Queues** — `queue.json` files: name, automation settings, hook references, rule references
3. **Schemas** — `schema.json` files: what fields are extracted, line item structure, field types
4. **Extensions** — `hooks/*.json` files: what each hook does, its trigger events, its settings (especially MDH matching configs, export configs, SFTP configs) — and always note the `active` flag and `queues` list: an inactive hook, or one not attached to the queue under review, describes behavior that does not run
5. **Formulas** — `formulas/*.py` files: calculations, normalizations, export mappings
6. **Rules** — `rules/*.json` files: validation conditions and actions
7. **Inboxes** — `inbox.json` files: how documents arrive (email addresses, filtering)
8. **Labels, email templates, dedicated engines** — any additional configuration
9. **Deployment setup** — `deploy_files/*.yaml`, `prd_config.yaml`, environment structure
10. **Existing documentation** — README files, inline comments, any markdown docs

## Project Layout

Typical top-level structure:

```
project/
├── production-org/          # Production environment
│   ├── organization.json
│   ├── credentials.yaml
│   └── default/
│       ├── hooks/           # Webhook/serverless definitions (JSON)
│       ├── rules/           # Validation rules (JSON)
│       ├── labels/          # Labels/tags (JSON)
│       └── workspaces/
│           └── [Name]_[ID]/
│               ├── workspace.json
│               └── queues/
│                   └── [QueueName]_[ID]/
│                       ├── queue.json
│                       ├── schema.json
│                       ├── inbox.json
│                       ├── formulas/          # Python formula files
│                       └── email_templates/   # Notification templates (JSON)
├── sandbox-org/             # Sandbox (dev + test environments)
│   ├── organization.json
│   ├── credentials.yaml
│   ├── dev/                 # Same structure as default/ above
│   └── test/                # Same structure as default/ above
├── deploy_files/            # Deployment YAML configs (env-to-env mappings)
├── deploy_secrets/          # Encrypted secrets
├── deploy_states/           # Deployment state snapshots
├── __resources/             # Reference materials, examples, XML templates
├── prd_config.yaml          # Global deployment configuration
└── README.md
```

**Naming convention**: Rossum entities use `[EntityName]_[NumericID]` format in directory and file names (e.g., `Cost Invoices (AT)_[2280450]`, `Validator: invoices_[899773].json`). The numeric ID is the Rossum API object ID.

## Configuration Files to Find

- **Organization** — `organization.json` at the root of each org directory
- **Workspaces** — `workspace.json` inside workspace directories
- **Queues** — `queue.json` with hook references, rule references, automation settings (`default_score_threshold`, `automation_level`, `automation_enabled`)
- **Schemas** — `schema.json` containing `category`, `datapoint`, `multivalue`, `section` keys defining the extraction data model
- **Inboxes** — `inbox.json` with email routing, filtering, and template references
- **Hooks/extensions** — JSON files in `hooks/` directories; look for `hook_type`, `config`, `sideload`, `settings_schema`; MDH matching configs and SFTP export configs are embedded inside hook settings. **When editing hook code, always edit the `.py` file, never the `code` field in the JSON** — `prd2` manages the synchronization.
- **Rules** — JSON files in `rules/` directories with validation conditions, triggers, and actions (automation blockers, messages, labels)
- **Labels** — JSON files in `labels/` directories defining tags for categorization (priority, status, department)
- **Formula files** — Python `.py` files in `formulas/` subdirectories of each queue; these implement field calculations, data normalization, export mappings, and MDH lookup logic. **Always edit the `.py` file, never the `formula` property in `schema.json`** — `prd2 push` syncs `.py` files into the schema JSON automatically.
- **Email templates** — JSON files in `email_templates/` subdirectories for notification/rejection templates
- **Dedicated Engines** — JSON files in `engines/` directories (custom AI extraction models)
- **Deployment configs** — YAML files in `deploy_files/` defining environment-to-environment mappings (e.g., `dev_test.yaml`, `test_prod.yaml`); `prd_config.yaml` at the root defines org-level settings
- **Credentials** — `credentials.yaml` in each org directory (API tokens, SFTP keys)
- **Resources** — `__resources/` or similar directories with example documents, XML templates, business logic references
- **Existing documentation** — README files, CLAUDE.md, analysis/documentation markdown files

## Discovery Commands

### Glob patterns — specific files first

```
**/schema.json          — Schema definitions (one per queue)
**/queue.json           — Queue configurations
**/inbox.json           — Inbox configurations
**/workspace.json       — Workspace configurations
**/organization.json    — Organization configurations
**/hooks/*.json         — Hook/extension definitions
**/rules/*.json         — Validation rules
**/labels/*.json        — Labels/tags
**/formulas/*.py        — Formula field implementations (Python)
**/email_templates/*.json — Notification templates
**/engines/*.json       — Dedicated Engine definitions
**/deploy_files/*.yaml  — Deployment mappings
**/credentials.yaml     — Credential files
**/prd_config.yaml      — Global deployment config
```

### Glob patterns — broad sweeps

```
**/*.json               — All JSON configuration files
**/*.py                 — All Python formula/serverless code
**/*.yaml               — All YAML deployment configs
**/*.md                 — All documentation
**/*.xml                — XML export templates or example documents
```

### Grep patterns — find specific configuration types

```
"category"              — Schema section markers
"datapoint"             — Schema field definitions
"multivalue"            — Schema line item (table) definitions
"hook_type"             — Extension type (webhook, function, etc.)
"hook_url"              — Webhook endpoints
"default_score_threshold" — Automation confidence thresholds
"automation_blocker"    — Logic that blocks automatic processing
"rir_field_names"       — AI extraction field mappings
"match_config"          — Master Data Hub matching configurations
"dataset"               — MDH dataset references
"$search"               — MongoDB Atlas Search queries in MDH configs
"$regex"                — MongoDB regex queries in MDH configs
"aggregate"             — MongoDB aggregation pipelines in MDH configs
"sftp"                  — SFTP import/export configurations
"enum_value_type"       — Numeric enum type hints for MDH
"formula"               — Formula field definitions in schemas
"score_threshold"       — Per-field confidence thresholds
"automation_level"      — Queue automation settings
"condition"             — Rule conditions
"actions"               — Rule or hook action definitions
```
