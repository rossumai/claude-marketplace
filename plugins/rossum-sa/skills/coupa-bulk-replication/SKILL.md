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

This skill has 6 phases. Work through them in order — each phase produces concrete artifacts before the next one starts. Use tasks to track progress across phases so work can resume if interrupted.

| Phase | What it covers |
|-------|----------------|
| 0 — Discovery | Credentials, token strategy, org URL, Coupa hook settings, dataset selection |
| 1 — Pre-flight | Disable hooks, create/clear collections, create indexes |
| 2 — Script Setup | Place `coupa_bulk_import.py`, create `coupa_bulk_import.config.json`, smoke-test |
| 3 — Replication | Supervised launch (`--supervise`), keep-awake, monitoring, resume |
| 4 — Completion | Verify counts, register with MDH |
| 5 — Handoff to continuous sync | Create or re-enable import hooks, delta seeding, canary |

---

## Phase 0: Discovery

**Goal:** Identify the Rossum org, Coupa credentials, and the set of datasets to replicate.

**Steps:**

1. **Locate the prd2 project.** Look for `prd_config.yaml` starting in the current directory and walking up.

   - **If found:** Read `prd_config.yaml` to get the org `api_base` URL (e.g. `https://ups.rossum.app/api/v1`). Read `<org-dir>/credentials.yaml` to get the Rossum bearer token as an initial fallback.
   - **If not found:** Ask the user for the Rossum organization base URL (e.g. `https://ups.rossum.app`) and whether to set up a prd2 project first (recommended for long-term maintenance — see `prd-reference`).

2. **Obtain Rossum credentials — check `auth_type` first.** Fetch the user object (`GET /auth/user`) and check `auth_type`:

   - **`password`:** username + password work — the script can refresh tokens automatically (`--username`/`--password`).
   - **`sso`:** `POST /auth/login` is unavailable to this user — the `--username`/`--password` auto-refresh CANNOT work. Use a password-auth **integration/service user** instead, or run purely on pre-staged long-lived tokens.

   Either way, mint a long-lived token up front:

   ```
   POST <org_url>/api/v1/auth/login
   {"username": "...", "password": "...", "max_token_lifetime_s": 583200}
   ```

   583200 s = 162 h, the platform maximum. **Verify a new token before relying on it** — three probes: identity (`GET /auth/user`), DS read (list collections), DS **write** (insert + delete a probe doc in a scratch collection).

   **Token heal path for running jobs:** the script reads the token from the config at start, re-reads it once on a DS 401, and every supervised relaunch re-reads the whole config. If a token dies mid-run: drop a fresh long-lived token into the config — running jobs self-heal, and the supervisor's auto-resume covers any that already died. No interactive step.

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

1. **Disable import hooks.** For each hook covering a dataset to replicate, call `rossum_patch_hook` with `active: false`. **Confirm with user before executing.** Record hook IDs so they can be re-enabled in Phase 5.

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

   Smoke records are harmless: documents are inserted with `_id` set to the Coupa id, so the full run skips them as duplicates (logged as `duplicate(s) skipped`). Exception: collections loaded by pre-supervision script versions hold auto-`_id` records the current script cannot dedupe against — wipe those collections before a full re-run.

**Artifact:** `coupa_bulk_import.py` + `coupa_bulk_import.config.json` present, gitignored, and smoke-tested.

---

## Phase 3: Replication

**Goal:** All selected datasets replicated into Data Storage with verified record counts.

**Steps:**

1. **Launch everything under supervision** — one command:

   ```bash
   mkdir -p logs
   nohup caffeinate -is python3 -u coupa_bulk_import.py --supervise --dataset all \
     >> logs/supervisor.log 2>&1 &
   ```

   (Linux: `systemd-inhibit --what=sleep --why="coupa bulk replication" python3 -u coupa_bulk_import.py --supervise ...`.)

   The supervisor spawns one child process per dataset (own log `logs/<ds>.log`, own state file), sweeps every 60 s, and applies the decision table: state file has `"completed": true` → done; child alive → fine; child died without the flag → relaunch with `--resume` — max 3 attempts per dataset, then give up on that dataset so a permanently broken one cannot crash-loop or hold the run hostage. Exit code 0 only when every dataset completed. Subset via `--dataset a,b,c`; tune with `--poll-interval` / `--max-restarts`; add `--username`/`--password` (password-auth users) for in-process token refresh in every child.

   **Keep-awake traps (laptop runs):**
   - Wrap the **supervisor**, not the workers: per-pid assertions (`caffeinate -w <pid>`) drop the moment that pid dies — and resumed jobs are new pids nothing covers.
   - Releasing an assertion on a machine idle for hours → sleep follows near-instantly (the idle timer counts from last input). There is no usable grace period.
   - Lid-close sleeps regardless of caffeinate: keep the lid open on AC power, or `pmset -a disablesleep 1` (remember to undo it).

2. **Monitor progress.** Supervisor decisions (launches, deaths with the dead job's last log line, give-ups) go to `logs/supervisor.log`; per-dataset flush lines to `logs/<ds>.log`:

   ```
   flushed → total      5000  offset      5000  last updated_at: 2024-03-15T10:23:45Z
   ```

3. **Resume after interruption.** The supervisor already resumes crashed children. If the supervisor itself stopped, relaunch it with `--resume` — completed datasets are skipped, incomplete ones continue from their state files:

   ```bash
   nohup caffeinate -is python3 -u coupa_bulk_import.py --supervise --dataset all --resume \
     >> logs/supervisor.log 2>&1 &
   ```

   A dataset the supervisor **gave up on** needs its failure investigated first (its last log line is in `logs/supervisor.log`) — typically an expired token (fix the config; see Phase 0 token strategy) or a Coupa-side error — then rerun the command above.

4. **Understand log messages:**

   | Message | Meaning |
   |---------|---------|
   | `[Rossum token expired — refreshing]` | Auto-refreshed via `--username`/`--password`; no action needed |
   | `[Rossum 401 — re-reading token from config]` | No credentials passed; retried once with the (possibly refreshed) config token |
   | `[Coupa token expired — refreshing]` | Auto-refreshed via client credentials; no action needed |
   | `[RETRY N/5] SSLError` | Transient connection error; retrying with exponential backoff |
   | `N duplicate(s) skipped (expected after resume or smoke test)` | Deterministic `_id` dedup at work; healthy |
   | `[NOTICE] >=90% of the first batch already exists…` | Fresh run over an already-loaded collection — see "Re-replicating a dataset" |
   | `[WARN] N document(s) failed` | Real write failures; check payload size or field types |
   | `[WARN] N record(s) missing 'id'` | Records without the id_key — inserted without dedup protection |
   | `supervisor: <ds> died … resuming (attempt N/3)` | Child crashed; auto-resumed |
   | `supervisor: <ds> exceeded 3 restarts — giving up` | Investigate that dataset's log, then relaunch the supervisor |

5. **Verify no silent data loss.** After each dataset completes, compare **`total_inserted`** from `coupa_import_state_<dataset>.json` with the actual DB count (`data_storage_aggregate [{"$count": "total"}]`). `total_processed` counts everything handled *including* duplicate-skips — on resumed runs it legitimately exceeds the DB count; `total_inserted` is the number that must match.

**Artifact:** All target collections populated. State files show `"completed": true` for each dataset. DB counts match `total_inserted`.

---

## Phase 4: Completion

**Goal:** Datasets verified and, where needed, registered with MDH.

**Sizing estimates vs completion truth:** sibling-org (presale) collection counts are ESTIMATES for run-ordering only and can be wildly stale — a field run saw `lookup_values` estimated at 171k exceed 1.75M (10×+), while `users` came in under its estimate (differential sibling hooks only accumulate from whenever they were seeded). Completion is decided by Coupa returning an empty page, never by hitting the estimate. Frame progress % and ETA as lower bounds.

**Steps:**

1. **Final count check.** For each dataset, confirm `total_inserted` in the state file equals the actual document count in Data Storage. If they differ by more than one batch (5,000 records), investigate `[WARN]` lines in the logs before proceeding.

2. **Register collections with MDH** (if the collections need fuzzy search or UI visibility). A seed CSV with only the header row is sufficient — MDH manages the collection from then on:

   ```bash
   curl -X POST "<org_url>/svc/master-data-hub/api/v1/dataset/<name>" \
     -H "Authorization: Bearer <token>" \
     -F "file=@seed.csv;type=text/csv" -F "encoding=utf-8" -F "dynamic=true"
   ```

   See `mdh-reference` for full MDH dataset management details.

3. **Record the anchors, then clean up.** Phase 5's delta seeding needs each dataset's `anchor_updated_at` from its state file — copy them out BEFORE deleting state files. Then state files can be deleted and logs archived.

**Artifact:** Collections live in Data Storage with `total_inserted` == DB count, MDH registered (if applicable), anchors recorded for Phase 5.

---

## Phase 5: Handoff to Continuous Sync

**Goal:** Delta sync running via "Coupa Webhook Import" hooks — without triggering the full import this skill exists to avoid.

Prerequisite from Phase 4: each dataset's `anchor_updated_at` (the delta boundary below).

### Path A — the org already has import hooks (disabled in Phase 1)

Per dataset: confirm `total_inserted` == DB count, then re-enable with `rossum_patch_hook` `active: true`. **Confirm with user before executing.**

### Path B — fresh org: build the hooks from a reference org

Mirror settings from any org with working Coupa Webhook Import hooks — the CIB template org, a presale org derived from it, or the customer's existing org. Read the reference hook's JSON and copy it; per dataset, change only:

| Setting | Value for the new org |
|---------|----------------------|
| Service URL | `https://<cluster>.rossum.app/svc/scheduled-imports/api/coupa/v1/import` — resolve the new org's cluster via its DNS CNAME |
| `active` | `false` — activate only after verification (step 2) |
| Schedule / crons | Same cadence as the reference org |
| `token_owner` | The integration user (Phase 0) |
| `dataset_name` | The bulk-loaded collection name |
| `fields` | **Exactly the bulk config's `fields` list** — record shapes then match (both sides store Coupa's hyphenated-key JSON) |
| Coupa credentials | The new org's `coupa_base_url` / `client_id` / scope; `client_secret` via secrets (step 4) |
| `"updated-at[gt_or_eq]"` | The delta seeding value — step 1 |

1. **Dodge the epoch-fallback trap (delta seeding).** `${last_modified_date}` resolves to the start of the last successful operation per (org, `dataset_name`) in the scheduled-imports service's own operation log. A brand-new hook has **no history → epoch → the first activation attempts a FULL import.** Field-verified fix: where the reference hook has `${last_modified_date}` (the `"updated-at[gt_or_eq]"` query value), put the dataset's **literal** `anchor_updated_at` instead. The first activation then pulls only the post-anchor delta AND establishes operation history. After the first successful run, flip the value back to `${last_modified_date}`.

2. **Verify-then-activate.** Per dataset: `total_inserted` == DB count, then `rossum_patch_hook` `active: true`. **Confirm with user before executing.**

3. **Canary the first hook.** Fire it manually: `POST /hooks/{id}/invoke` (`invocation.manual`; returns 202 — an async long-running job). Detection subtlety: in `method: update` mode the service UPSERTS — the record count does not change and updates carry no `__digest_md5` stamp. The reliable check: query Coupa directly for records updated since the anchor, then confirm those exact ids carry the new `updated-at` in Data Storage.

4. **Secrets.** Hook `secrets` are write-only (GET returns null) — set via `PATCH {"secrets": {"client_secret": "..."}}`. ALWAYS set `secrets_schema` too (typed properties, `additionalProperties: false`); without it the UI shows no secret field at all. A hook with secrets but no `secrets_schema` is a defect.

5. **Coupa OAuth gotchas.** Probe every dataset's scope before activating anything — a scope not granted to the OAuth client fails the token endpoint with 400 `invalid_scope` (field case: the contracts scope was missing). Whitespace in a pasted `client_secret` → 401 `'client_secret' missing` (the bulk script strips config credentials; the hook PATCH is on you).

**Artifact:** Hooks active for every dataset, first delta canary verified, `${last_modified_date}` placeholders restored.

---

## Key Technical Reference

### Re-replicating a dataset (e.g. the field list changed)

Insert-dedup is **not** upsert: a re-run over a loaded collection skips every existing record and updates nothing — the script flags this loudly (`[NOTICE]`) when a fresh run's first batch is ≥90% duplicates. To re-replicate with a changed `fields` list:

- **Dev/UAT:** drop the collection (Phase 1 flow), then run fresh.
- **Live production collection:** blue-green — load into a temp collection (indexes build instantly on empty), swap via `data_storage_rename_collection`, then drop the old one. Avoids hours of empty/partial data under live MDH matching.

DS REST quirk while cleaning up: `find`/`aggregate` take `query`/`pipeline`, but `delete_one`/`delete_many` take `filter` — a 422 usually means the wrong key.

### Deterministic `_id` and dedup

Documents are inserted with `_id = record[<id_key>]` (raw Coupa id, no type coercion). Before every batch insert the script checks which `_id`s already exist and skips them — necessary because the DS REST layer reports duplicate-key write errors as an opaque HTTP 400 ("batch op errors occurred") with no per-document detail (live-verified 2026-07). The effect: re-inserting an existing record is a skip, not a second copy — smoke tests, resume overlap, and fresh runs over partial loads are all self-healing. Consequences:

- `total_inserted` (state file) counts actual inserts and must match the DB count; `total_processed` includes duplicate-skips.
- Records missing the id are inserted with an auto `_id` and warned about — never `_id: null` (two nulls would dedupe against each other).
- Two datasets must not share a collection — the config loader rejects it.
- Mixed `_id` types are fine: hook-written records carry ObjectIds, bulk records carry Coupa ids; the sync service matches on business keys, not `_id`.

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
{"username": "...", "password": "...", "max_token_lifetime_s": 583200}
-> {"key": "<token>", ...}
```

583200 s (162 h) is the platform maximum lifetime. SSO users cannot call this endpoint — use a password-auth integration user (see Phase 0). Passing `--username`/`--password` enables fully unattended refresh; without credentials the script re-reads the config token once on a DS 401, and supervised relaunches re-read the whole config.

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
