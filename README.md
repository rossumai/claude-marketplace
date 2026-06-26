# 🧰 Rossum toolkit for Claude Code

Turn Claude into a Rossum implementation partner — audit hooks, analyze schemas, query Data Storage, upgrade extensions, and generate SOWs, all from your terminal.

16 skills · 13 reference packs · 75 MCP tools — [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces) for Rossum.ai.

<!-- TODO: add a terminal demo GIF here (e.g. invoice extraction or hook audit) -->

## 🚀 Quick start

You need the [Claude Code CLI](https://code.claude.com/) and a Rossum account.

```bash
/plugin marketplace add rossumai/claude-marketplace
/plugin install rossum-sa@rossumai-claude-marketplace
```

> **Note:** Auto-updates are off by default for third-party marketplaces. Enable them in `/plugin` → **Marketplaces** tab.

**Optional:** For a better experience when developing and implementing projects, install the [superpowers](https://github.com/obra/superpowers) plugin — it adds structured planning, TDD workflows, and code review skills that pair well with `rossum-sa`:

```bash
/plugin install superpowers@claude-plugins-official
```

## 🧭 Two ways in

- **Explore a live org** — connect to a running Rossum environment and ask questions about it. Anyone with a Rossum login can do this; it's the lowest-barrier way to start.
- **Work on an implementation** — pull a project to a local checkout with `prd2` and use the skills to audit, document, upgrade, and build. The day-to-day SA loop.

## 🔍 Explore a live org

```
Connect to Rossum and map out the entire org — workspaces, queues, hooks,
schemas — and draw an ASCII architecture diagram. Add emoji health
indicators next to each component (🟢 healthy, 🟡 warning, 🔴 broken).
```

Claude prompts for your credentials when it connects, so nothing sensitive goes into the chat:

- **Rossum staff / admins** — paste a connection string (the `curl` snippet from the Rossum admin UI), or an API token.
- **Implementation partners** — your Rossum username + password.
- Either way, give the **base URL** (`https://elis.rossum.ai`, or `https://<your-org>.rossum.app`). No organization ID needed — the base URL identifies the org.

See **What can you do with this?** below for more live-org prompts.

## 🛠️ Work on an implementation

For real implementation work, pull a project to a local checkout with `prd2`, then let Claude work on the files:

```bash
prd2 pull <org-dir>          # needs prd2 + admin-role credentials (Rossum staff: a service token)
```

```
/rossum-sa:init-claude-md    # teach Claude this checkout is a Rossum project
/rossum-sa:analyze           # find configuration errors
/rossum-sa:dead-code         # prune unused hooks, formulas, and rules
/rossum-sa:document          # generate a reference doc for the project
```

From here the full skill set applies — see **Skills** below. New to `prd2`? The autoloaded `prd-reference` knowledge covers install, config, and credentials — just ask Claude how to set it up.

## ⚡ Skills

### `rossum-sa`

| Skill | Description |
|-------|-------------|
| `/rossum-sa:write-sow` | Generate a Statement of Work from project requirements |
| `/rossum-sa:analyze [path]` | Check an implementation for configuration errors |
| `/rossum-sa:document [path]` | Produce a queue-focused reference document |
| `/rossum-sa:implement` | Plan and execute an integration project end-to-end |
| `/rossum-sa:refine-deployment [deploy-file] [impl-path]` | Enhance prd2 deploy files with target IDs and attribute overrides |
| `/rossum-sa:upgrade [path]` | Upgrade deprecated extensions to formula fields and bump old Python runtimes on function hooks to `python3.12` |
| `/rossum-sa:iterate [annotation-id]` | Tight inner loop on a deliverable — re-fire a hook/formula/rule against one annotation and check the result until the goal is met |
| `/rossum-sa:test-behavioral-equivalence [path]` | Snapshot-replay-diff an implementation against a baseline env to verify a change preserved behavior |
| `/rossum-sa:dead-code [path]` | Find unused hooks, formulas, rules, labels, and engines with a deterministic detector |
| `/rossum-sa:queue-engine-binding [mode] [queue-id]` | Bind queues to custom extraction engines — convert from generic, create engine-bound queues, attach to an existing engine, or revert |
| `/rossum-sa:evaluate-namings [path]` | Audit naming conventions across workspaces, queues, hooks, schema fields, and MDH datasets |
| `/rossum-sa:coding-best-practices [path]` | Review serverless hook functions (custom Python extensions) for code quality, security, and correctness |
| `/rossum-sa:init-claude-md [path]` | Generate a project-specific `CLAUDE.md` for a pulled prd2 project so future Claude Code sessions recognize it as a Rossum implementation |
| `/rossum-sa:coupa-bulk-replication [dataset]` | Bulk-replicate Coupa master data into Data Storage when import-hook timeouts block standard sync — resumable, sync writes, automatic token refresh |
| `/rossum-sa:render-export-template [hook-id] [annotation-id]` | Render a Custom Format Templating export hook against a real annotation to preview the exact file it outputs, then extract/edit/generate the Jinja2 template — legacy template export, not the Request Processor |
| `/rossum-sa:automation-report [queue-id-or-url]` | Diagnose what blocks queue automation and produce an actionable report with threshold, schema, and rule recommendations |

## 📚 Autoloaded references

When `rossum-sa` is enabled, Claude automatically gets domain knowledge for:

- **Rossum platform** — queues, schemas, hooks, annotations, workflows
- **MongoDB** — query syntax, aggregation pipelines
- **Master Data Hub (MDH)** — matching, scoring, collections
- **Data Storage API** — CRUD, indexing, search
- **TxScript & Serverless Functions** — formula fields, extension development
- **SAP Integration** — connector setup, mapping
- **Export Pipeline (Request Processor)** — multi-stage API integration engine, SFTP export, auth, response handling
- **Coupa Integration Baseline (CIB)** — schema, MDH matching, export pipeline, business rules
- **prd2 CLI** — deployment and management commands
- **Structured Formats Import (SFI)** — XML/JSON import setup, XPath/JMESPath selectors, e-invoicing (ZUGFeRD, X-Rechnung)
- **Approval workflows** — read-only workflow/step/run/activity API + the `reset` action, step modes & conditions, approver assignees (a paid, Rossum-configured feature)
- **Business rules & validation** — native Rules vs. the legacy Business Rules Validation extension, conditions, actions, automation blocking
- **Email Body Converter** — hosted webhook that turns email HTML bodies (and HTML/TXT attachments or uploads) into PDF documents for extraction — setup, full settings schema, regional availability, body-only email recipes

## 💡 What can you do with this?

More read-only prompts to run against a connected org:

**🕵️ Who's been busy?** — Pull a year of audit logs and surface suspicious activity patterns.
```
Connect to Rossum, pull all audit logs for the last year, and print a histogram
of user activity. Highlight suspicious patterns.
```

**🔗 Find broken hooks** — Audit your hook chains across all queues.
```
Connect to Rossum and list all hooks. Group them by queue and flag any that are inactive,
have no queues attached, or have a broken run_after chain.
```

**📊 Spot missing indexes** — Catch Data Storage performance problems before they bite.
```
Connect to Rossum and check all Data Storage collections. List their indexes and search
indexes, flag any missing __dynamic_index or duplicate/redundant indexes.
```

**🎯 Tune fuzzy matching** — Optimize MDH search scores with real data.
```
Connect to Rossum and find the $search query in the MDH matching extension. Verify and
calibrate the score thresholds against real data in the collection. Use at least 1000 samples.
```

**🔀 Detect schema drift** — Find fields that diverged across queues.
```
Connect to Rossum and compare schemas across all active queues. List fields that exist in
one schema but not another.
```

## 🔌 MCP tools (`rossum-api`)

The MCP server starts automatically when `rossum-sa` is enabled. Write and destructive tools require explicit user approval.

#### Connection

| Tool | Description |
|------|-------------|
| `rossum_set_token` | Authenticate with a Rossum environment (API token, username+password, or pasted curl connection string) |
| `rossum_whoami` | Show authenticated user, organization, and role |
| `rossum_get` | Read-only GET of any API resource without a dedicated tool (engines, labels, automation_blockers, …) |

#### Rossum API

| Tool | Description |
|------|-------------|
| `rossum_list_workspaces` | List workspaces |
| `rossum_get_workspace` | Get full workspace details |
| `rossum_list_queues` | List queues (filter by workspace, status) |
| `rossum_get_queue` | Get full queue details |
| `rossum_get_automation_insights` | Queue automation analytics: rates, blockers, per-field statistics (compact digest by default, full payload cached) |
| `rossum_get_automation_projections` | Simulate automation at recalibrated confidence thresholds; degrades gracefully when the queue has too little reviewed data |
| `rossum_get_schema` | Get queue schema (datapoints, sections, tables) |
| `rossum_patch_schema` | ✏️ Update a schema (name, content, metadata) |
| `rossum_list_schemas` | List all schemas |
| `rossum_list_hooks` | List hooks/extensions (filter by queue, active) |
| `rossum_get_hook` | Get full hook details including code and config |
| `rossum_create_hook` | ✏️ Create a new hook (serverless function or webhook) |
| `rossum_create_hook_from_template` | ✏️ Create a hook from a hook template (catalog) — template supplies the base, you supply name/queues/settings |
| `rossum_duplicate_hook` | ✏️ Clone an existing hook (created inactive; queues/secrets/dependencies copied only on request) |
| `rossum_delete_hook` | ⚠️ Delete a hook |
| `rossum_patch_hook` | ✏️ Update an existing hook (code, events, active, queues) |
| `rossum_extract_export_template` | Pull a Custom Format Templating export template out of a hook's `export_configs` into editable text |
| `rossum_generate_export_settings` | Turn a local Jinja2 template into the `export_configs` settings block to push back |
| `rossum_generate_export_payload` | Generate an annotation's export payload (feeds the local render preview) |
| `rossum_list_hook_logs` | List hook execution logs (filter by hook, annotation, queue, status) |
| `rossum_test_hook` | ✏️ Test a hook in isolation: auto-generate a payload (event/action) and execute it (dry-run; optional config override) |
| `rossum_invoke_hook` | ⚠️ Invoke a hook for REAL with a custom payload (live execution — irreversible external side effects; not a dry-run like test) |
| `rossum_list_rules` | List business rules (filter by queue) |
| `rossum_get_rule` | Get full rule details (trigger_condition, actions, queues) |
| `rossum_create_rule` | ✏️ Create a new business rule (trigger_condition + actions) |
| `rossum_patch_rule` | ✏️ Update an existing rule (trigger_condition, enabled, actions, queues) |
| `rossum_delete_rule` | ⚠️ Delete a business rule |
| `rossum_list_rule_execution_logs` | List rule execution logs (filter by rule, queue, annotation, event, result, time) |
| `rossum_list_annotations` | List annotations in a queue (filter by status) |
| `rossum_search_annotations` | Search annotations across queues (filter by status, date range, workspace) |
| `rossum_search_annotations_advanced` | Content/field-value + full-text search via `POST /annotations/search` (MongoDB-subset `query` + `query_string`); read-only despite being a POST |
| `rossum_get_annotation` | Compact merged view: metadata + extracted fields + tables + resolved automation_blocker items + recent hook logs in one call. Caches raw payload to `.rossum-cache/annotations/<id>.json`. |
| `rossum_get_annotation_meta` | Raw annotation metadata only (status, timestamps, URLs) — use when you want the unprojected resource |
| `rossum_get_annotation_content` | Raw content tree (extracted data) — use when you need the unprojected nested structure |
| `rossum_patch_annotation` | ✏️ Update annotation status or metadata (confirm, reject, export) |
| `rossum_start_annotation` | ✏️ Start a review session (transitions to `reviewing`, locks to caller) |
| `rossum_cancel_annotation` | ✏️ Cancel a review session (releases the lock) |
| `rossum_confirm_annotation` | ⚠️ Confirm an annotation (`POST /confirm`) — transitions to exported/exporting/confirmed and FIRES THE EXPORT |
| `rossum_validate_content` | ✏️ Fire the hook chain via `content/validate` with specified actions |
| `rossum_upload_document` | ✏️ Upload a local document (PDF/image/etc.) into a queue via the modern async `/uploads` API; polls task→upload→annotation and returns the new annotation id |
| `rossum_refire_annotation` | ✏️ Inner-loop re-fire primitive (`mode=validate\|toggle\|reupload`) with try/finally cancel, dedup auto-restore, and merged compact response |
| `rossum_get_task` | 👁️ Retrieve an async task object (no-follow GET; surfaces status behind the /tasks 303 redirect) |
| `rossum_delete_annotation` | ⚠️ Delete annotations (reversible soft-delete; optional irreversible purge with poll-to-purged) — cleanup for test/iteration loops |
| `rossum_update_annotation_content` | ✏️ Write field values via bulk content-operations (replace/add/remove); self-managing start→ops→release |
| `rossum_get_document` | Get document metadata (filename, MIME type) |
| `rossum_list_connectors` | List export connectors (filter by queue) |
| `rossum_list_emails` | List emails (filter by queue, type) |
| `rossum_get_email` | Get full email details (subject, body, attachments) |
| `rossum_list_email_threads` | List email threads (filter by queue) |
| `rossum_list_groups` | List available user roles (groups) and their IDs |
| `rossum_list_users` | List organization users |
| `rossum_create_user` | ✏️ Create a new user in the organization |
| `rossum_list_audit_logs` | Query audit logs (admin only) |

#### Data Storage

| Tool | Description |
|------|-------------|
| `data_storage_healthz` | Check API reachability |
| `data_storage_list_collections` | List collections |
| `data_storage_find` | Query documents with filter/projection/sort |
| `data_storage_aggregate` | Run MongoDB aggregation pipelines |
| `data_storage_list_indexes` | List collection indexes |
| `data_storage_list_search_indexes` | List Atlas Search indexes |
| `data_storage_insert` | ✏️ Insert one or more documents into a collection |
| `data_storage_update_one` | ✏️ Update first document matching a filter |
| `data_storage_update_many` | ✏️ Update all documents matching a filter |
| `data_storage_replace_one` | ✏️ Replace first document matching a filter |
| `data_storage_delete_one` | ⚠️ Delete first document matching a filter |
| `data_storage_delete_many` | ⚠️ Delete all documents matching a filter |
| `data_storage_bulk_write` | ✏️ Perform multiple write operations atomically |
| `data_storage_create_index` | ✏️ Create a database index |
| `data_storage_create_search_index` | ✏️ Create an Atlas Search index |
| `data_storage_drop_index` | ⚠️ Drop a database index |
| `data_storage_drop_collection` | ⚠️ Drop a collection and all its indexes |
| `data_storage_rename_collection` | ⚠️ Rename a collection |
| `data_storage_drop_search_index` | ⚠️ Drop an Atlas Search index |

✏️ = write (requires approval) · ⚠️ = destructive (requires approval)

## 🧪 Running from a local checkout (feature branch)

To test a feature branch before it's published to the marketplace, clone the repo and point Claude Code at the plugin directory with `--plugin-dir`.

```bash
git clone -b <feature-branch> https://github.com/rossumai/claude-marketplace.git rossum-claude-plugin

claude --plugin-dir rossum-claude-plugin/plugins/rossum-sa
```

## 📄 License

[MIT](LICENSE) © Rossum
