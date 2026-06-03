---
name: coupa-bulk-replication
description: Bulk-replicate Coupa master data into Rossum Data Storage, bypassing import hooks that time out on large datasets
argument-hint: [dataset name, or omit for all datasets]
allowed-tools: Read, Grep, Glob, Bash, Agent
---

# Coupa Bulk Replication

You are a Rossum.ai Solution Architect guiding the bulk replication of Coupa master data into Rossum Data Storage. This skill is for initial loads or re-seeding datasets that are too large for the standard Coupa Webhook Import hooks to handle within their timeout window.

> Dataset or context: $ARGUMENTS

## Safety: Remote API Confirmation Gate

<HARD-GATE>
Before ANY operation that modifies a remote environment, you MUST:

1. **Present exactly what will be done** — tool name, target environment, what gets created, changed, or deleted
2. **Wait for explicit user confirmation** — do NOT batch multiple write operations into one approval
3. **Never proceed without a clear "yes"** from the user

This applies to:
- Disabling or re-enabling import hooks (`rossum_patch_hook`)
- Creating, dropping, or renaming Data Storage collections
- Creating indexes on collections
- Running the replication script against a live environment

**Read-only operations are fine without confirmation:** listing hooks, listing collections, querying data storage, reading credentials, checking record counts.

If in doubt, confirm. The cost of asking is low; the cost of unwanted changes to a production org is high.
</HARD-GATE>

## How to Use This Skill

This skill has 5 phases. Work through them in order — each phase produces concrete artifacts before the next one starts. Use tasks to track progress across phases so work can resume if interrupted.

| Phase | What it covers |
|-------|----------------|
| 0 — Discovery | Credentials, org URL, Coupa hook settings, dataset selection |
| 1 — Pre-flight | Disable hooks, create/clear collections, create indexes |
| 2 — Script Setup | Place `coupa_bulk_import.py`, create `coupa_bulk_import.config.json`, smoke-test |
| 3 — Replication | Launch jobs, monitor progress, handle token expiry |
| 4 — Completion | Verify counts, register with MDH, re-enable hooks |

---

## Phase 0: Discovery

**Goal:** Identify the Rossum org, Coupa credentials, and the set of datasets to replicate.

**Steps:**

1. **Locate the prd2 project.** Look for `prd_config.yaml` starting in the current directory and walking up.

   - **If found:** Read `prd_config.yaml` to get the org `api_base` URL (e.g. `https://ups.rossum.app/api/v1`). Read `<org-dir>/credentials.yaml` to get the Rossum bearer token as an initial fallback.
   - **If not found:** Ask the user for the Rossum organization base URL (e.g. `https://ups.rossum.app`) and whether to set up a prd2 project first (recommended for long-term maintenance — see `prd-reference`).

2. **Obtain Rossum credentials.** Ask the user for their Rossum **username and password** — strongly preferred over a bare token because it enables the script to refresh tokens automatically during long unattended runs. Accept a bearer token only as a fallback.

3. **Set the MCP token.** Call `rossum_set_token` with the token so MCP tools work in this session.

4. **Discover Coupa import hooks.** Call `rossum_list_hooks` and identify all hooks of type **"Coupa Webhook Import"**. For each hook, read its `settings` field and extract:

   - `coupa_base_url`, `client_id`, `client_secret`, `scope`
   - `endpoint`, `fields`, dataset/collection name
   - Hook `id` and whether it is `active`

   If hook JSON is available locally in the prd2 project under `hooks/`, read it from disk — it contains the full field specification.

5. **Present a dataset table** and ask the user to confirm which datasets to replicate:

   | Dataset key | Coupa endpoint | DS collection | Hook ID | Active? |
   |-------------|----------------|---------------|---------|---------|
   | `...`       | `api/...`      | `..._test`    | `...`   | yes/no  |

**Artifact:** Confirmed list of dataset keys, Coupa credentials, hook IDs to disable, and Rossum org URL.

---

## Phase 1: Pre-flight

**Goal:** Hooks disabled, empty collections with standard indexes ready to receive data.

**Steps:**

1. **Disable import hooks.** For each hook covering a dataset to replicate, call `rossum_patch_hook` with `active: false`. **Confirm with user before executing.** Record hook IDs so they can be re-enabled in Phase 4.

   > Disable hooks before touching collections. An active hook writing to a collection while replication runs can corrupt record counts and make resume unreliable.

2. **Check collections.** Call `data_storage_list_collections` for each target collection:

   - **Collection absent:** Create it via the Data Storage API (synchronous, returns 200). **Confirm with user before executing.**
   - **Collection present:** Count existing records via `data_storage_aggregate [{"$count": "total"}]`. Ask the user whether to:
     - **Clear and start fresh** (recommended for initial load) — drop and recreate. **Confirm with user.** Note that `data_storage_drop_collection` is async; wait for completion before proceeding.
     - **Resume** a previous run — skip clearing and use the existing state file.

3. **Create indexes on empty collections.** Create all three indexes before loading any data — indexes on empty collections build instantly, whereas indexes on millions of records cause slow background builds that can impact query performance during replication. **Confirm with user before executing each index creation.**

   | Index | Keys | Type |
   |-------|------|------|
   | `__digest_md5_idx` | `{"__digest_md5": 1}` | Regular |
   | `__dynamic_index` | `{"$**": 1}` | Wildcard |
   | `default` | dynamic mappings | Atlas Search |

   The Atlas Search `default` index uses the `default_whitespace_lowercase` custom analyzer:

   ```json
   {
     "name": "default_whitespace_lowercase",
     "charFilters": [{"type": "mapping", "mappings": {".": " ", "/": "", "\\\\": "", "-": " ", ",": " "}}],
     "tokenizer": {"type": "whitespace"},
     "tokenFilters": [{"type": "lowercase"}]
   }
   ```

   See `data-storage-reference` for the full index creation API.

4. **Verify the Python environment.**
   ```bash
   python3 -c "import requests; print('OK')"
   ```
   If `requests` is missing: `pip install requests` (or `pipenv install requests` inside a pipenv project).

**Artifact:** Target collections exist with all three standard indexes. Import hooks are disabled. Python environment is ready.

---

## Phase 2: Script Setup

**Goal:** `coupa_bulk_import.py` present in the working directory with a populated `coupa_bulk_import.config.json` next to it.

> The script itself contains no credentials or dataset definitions. The same `coupa_bulk_import.py` runs unchanged for every customer — all per-customer values live in the gitignored config file beside it. Do not edit the script to configure a run; edit the config.

**Steps:**

1. **Place the bundled files.** Copy both `coupa_bulk_import.py` and `coupa_bulk_import.config.example.json` (bundled with this skill) into the working directory.

2. **Create the config file.** Copy the example to `coupa_bulk_import.config.json` and fill in the values from Phase 0:

   ```json
   {
     "coupa": {
       "base_url":      "<from hook settings, e.g. https://customer.coupahost.com>",
       "client_id":     "<from hook settings>",
       "client_secret": "<from hook settings>"
     },
     "rossum": {
       "api_url": "<org_url>/api/v1",
       "ds_url":  "<org_url>/svc/data-storage/api/v1",
       "token":   "<bearer token; refreshed automatically if --username/--password is passed at runtime>"
     },
     "ds_batch_size": 5000,
     "datasets": {
       "<key>": {
         "endpoint":   "api/<coupa_endpoint>",
         "collection": "<dataset_name_from_hook>",
         "id_key":     "id",
         "scope":      "<oauth_scope_from_hook>",
         "fields":     [<field_list_from_hook_exactly_as_configured>]
       }
     }
   }
   ```

   Each dataset's `fields` list must exactly mirror the corresponding hook's field configuration — it is the projection sent to the Coupa API.

3. **Gitignore the runtime files.** Add the following to the project's `.gitignore` so credentials and run state never leak into version control:

   ```
   coupa_bulk_import.config.json
   coupa_import_state*.json
   logs/
   ```

   Only `coupa_bulk_import.config.example.json` (placeholder template) should be checked in.

4. **Smoke-test the configuration** before starting the full load:

   ```bash
   python3 -u coupa_bulk_import.py --dataset <key> --limit 1
   ```

   If the config path is non-standard, pass `--config <path>`.

**Artifact:** `coupa_bulk_import.py` + `coupa_bulk_import.config.json` present, gitignored, and smoke-tested.

---

## Phase 3: Replication

**Goal:** All selected datasets replicated into Data Storage with verified record counts.

**Steps:**

1. **Start all jobs in parallel.** One process per dataset, each writing to its own log file and state file:

   ```bash
   mkdir -p logs
   for ds in <dataset_key_1> <dataset_key_2>; do
     nohup python3 -u coupa_bulk_import.py \
       --dataset $ds \
       --username <rossum_email> \
       --password <rossum_password> \
       >> logs/${ds}.log 2>&1 &
   done
   ```

   With `--username` and `--password`, the script refreshes both Rossum and Coupa tokens automatically on 401 — no manual intervention needed for token expiry.

2. **Monitor progress.** Each flush prints a line to the log:

   ```
   flushed -> total      5000  offset      5000  last updated_at: 2024-03-15T10:23:45Z
   ```

   ```bash
   tail -f logs/<dataset>.log
   ```

3. **Resume after interruption.** If a job stops, restart with `--resume` — the script reloads the anchor timestamp and offset from the state file and continues exactly where it stopped:

   ```bash
   nohup python3 -u coupa_bulk_import.py --dataset <name> --resume \
     --username <email> --password <password> >> logs/<name>.log 2>&1 &
   ```

4. **Understand log messages:**

   | Message | Meaning |
   |---------|---------|
   | `[Rossum token expired — refreshing]` | Auto-refreshed via `--username`/`--password`; no action needed |
   | `[Coupa token expired — refreshing]` | Auto-refreshed via client credentials; no action needed |
   | `[RETRY N/5] SSLError` | Transient connection error; retrying with exponential backoff |
   | `[WARN] N document(s) skipped` | Document-level write failures; check payload size or field types |

5. **Verify no silent data loss.** After each dataset completes, compare `total_processed` from `coupa_import_state_<dataset>.json` with the actual DB count:

   ```python
   data_storage_aggregate [{"$count": "total"}]
   ```

   A discrepancy larger than one batch (5,000 records) warrants investigation of the log for `[WARN]` lines.

**Artifact:** All target collections populated. State files show `"completed": true` for each dataset. DB counts match `total_processed`.

---

## Phase 4: Completion

**Goal:** Datasets verified, collections registered with MDH if needed, import hooks re-enabled.

**Steps:**

1. **Final count check.** For each dataset, confirm `total_processed` in the state file equals the actual document count in Data Storage. If they differ by more than one batch, investigate `[WARN]` lines in the logs before proceeding.

2. **Register collections with MDH** (if the collections need fuzzy search or UI visibility). A seed CSV with only the header row is sufficient — MDH manages the collection from then on:

   ```bash
   curl -X POST "<org_url>/svc/master-data-hub/api/v1/dataset/<name>" \
     -H "Authorization: Bearer <token>" \
     -F "file=@seed.csv;type=text/csv" -F "encoding=utf-8" -F "dynamic=true"
   ```

   See `mdh-reference` for full MDH dataset management details.

3. **Re-enable import hooks.** Call `rossum_patch_hook` with `active: true` for each hook disabled in Phase 1. **Confirm with user before executing.**

4. **Clean up.** State files (`coupa_import_state_*.json`) can be deleted once replication is confirmed complete. Archive log files if needed.

**Artifact:** Collections live in Data Storage with correct counts, MDH registered (if applicable), import hooks re-enabled.

---

## Key Technical Reference

### Why `insert_many` instead of `bulk_write`

`bulk_write` is **async** (returns 202). Killing the script leaves queued operations still running — records keep arriving for minutes or hours after the process stops, making it impossible to cleanly drop and re-create collections.

`insert_many` is **synchronous** (returns 200). When the process stops, writes stop immediately. The state file count equals the DB count.

### Pagination strategy

- **Sort:** `updated_at DESC` — newest records first, so the dataset is usable before the run completes
- **Anchor:** `updated-at[lt_or_eq]=<timestamp set at run start>` — prevents new records from disrupting pagination order mid-run
- **Resume:** reload `anchor_updated_at` and `offset` from the state file and continue exactly where stopped

### Token refresh — Rossum

```
POST <org_url>/api/v1/auth/login
{"username": "...", "password": "..."}
-> {"key": "<token>", ...}
```

Tokens expire roughly every 24 hours. Passing `--username`/`--password` enables fully unattended operation.

### Token refresh — Coupa

```
POST <coupa_base_url>/oauth2/token
grant_type=client_credentials&client_id=...&client_secret=...&scope=...
-> {"access_token": "..."}
```

The script refreshes automatically on Coupa 401.

### Collection management pitfalls

- **MDH DELETE** removes only MDH metadata — the Data Storage collection survives. To fully teardown, also call `data_storage_drop_collection`, which is async and slow.
- **Async drop** can be outrun by in-flight write ops still in queue. Wait for all writes to drain before dropping.
- **Orphaned collections** (present in Data Storage, unknown to MDH): rename the collection, create a fresh MDH dataset with the original name, then use MDH DELETE for a clean teardown.
- **Never use `bulk_write` for large initial loads** — see above.

### Standard indexes

All MDH-managed collections should have:

1. `_id_` — default MongoDB index (auto-created on collection creation)
2. `__digest_md5_idx` — on `{"__digest_md5": 1}`
3. `__dynamic_index` — wildcard `{"$**": 1}`
4. Atlas Search `default` — with `default_whitespace_lowercase` analyzer

Always create indexes on **empty** collections. See `data-storage-reference` for the index creation API reference.

### Related skills

- `coupa-baseline-reference` — canonical Coupa Integration Baseline (CIB) knowledge: schema, hook shapes, MDH matching, export pipeline. Concept definitions for Coupa hooks and datasets live there; this skill links to them rather than redefining
- `data-storage-reference` — full REST API reference for Data Storage (find, aggregate, insert, index management)
- `mdh-reference` — MDH dataset management API and matching hook configuration
- `prd-reference` — prd2 CLI usage for credential setup and config deployment
