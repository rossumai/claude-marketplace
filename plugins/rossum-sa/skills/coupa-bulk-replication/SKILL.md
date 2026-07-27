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

This skill has 7 phases. Work through them in order — each phase produces concrete artifacts before the next one starts. Use tasks to track progress across phases so work can resume if interrupted.

| Phase | What it covers |
|-------|----------------|
| 0 — Discovery | Credentials, token strategy, org URL, Coupa hook settings, dataset selection, `--probe` sizing + worker calibration (from previous run summaries when they exist) |
| 1 — Pre-flight | Disable hooks, create/clear collections, create indexes |
| 2 — Script Setup | Place `coupa_bulk_import.py`, create `coupa_bulk_import.config.json`, smoke-test |
| 3 — Replication | Supervised launch (`--supervise`, partitioned workers), keep-awake, monitoring, migration journal, resume |
| 4 — Completion | Verify counts, register with MDH |
| 5 — Handoff to continuous sync | Create or re-enable import hooks, delta seeding, canary |
| 6 — Debrief | Read `run_summary.jsonl` + journal, route learnings to their durable homes |

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

6. **Calibrate from previous runs first.** Before probing, look for `logs/run_summary.jsonl` — in this environment's migration directory AND in sibling environments' (a test-org run calibrates the dev run; dev calibrates prod). Each line is one supervised run with per-unit measured `rec_per_s`, durations, restarts, and `coupa_429s`. Measured rates from a real full run beat fresh 3-page probe samples: derive worker suggestions from them (same ceiling math as below), flag any dataset whose past run logged 429s (lower the cap or workers), and only lean on the probe's sampled rate for datasets with no history. Still run `--probe` for the **counts** — sibling-org record counts drift.

7. **Size the run with `--probe`.** Coupa has no count endpoint, but the script's probe computes exact per-dataset counts by offset bisection (~45 cheap API calls per dataset) AND samples real throughput: `python3 coupa_bulk_import.py --probe` (needs the Phase 2 config; add `--dataset a,b` for a subset). Per dataset it prints the exact count, measured records/sec (3 sample pages with the dataset's real field list), estimated duration at 1/2/4/8 workers, and a config-ready `workers` suggestion. Always do this before planning the run — never plan against collection counts copied from another org (a presale/sibling org, when one even exists): field runs saw those off by 2.7×–27×. Re-run `--probe` mid-replication for exact %-complete — it reuses the run's anchor from the state file (summing partition state files for partitioned datasets). The probe also doubles as the keyset-query preflight: its sample pages use the exact query shape the workers will run. **If a dataset is meant to be a filtered slice of its endpoint, set its `extra_params` (Phase 2) BEFORE probing** — otherwise the count and the plan describe the wrong (unfiltered) population.

   **Calibrate workers from measured rates, not record counts.** Per-record cost scales with record width — field count, and especially nested association fields that force server-side joins. A field run saw ~10× between narrow `lookup_values` (4.77M records, fast) and wide PO lines (fewer records, much slower) — counts alone mislead. The probe's suggestion targets ~1 h per dataset (suggestion caps at 8 workers; floor is configurable via `min_partition`, default 50k records per worker); adjust it against context the script cannot see: the rate budget shared with live webhooks (see Phase 2 rate cap), how urgent the wall clock is, tenant load. Then set the chosen values in the config — each dataset block takes an optional `"workers": N` (default 1) — and run Phase 3 with `--supervise`. Claude applies the config edit on request.

   **Overriding the suggestion upward (the giant-dataset lever).** The 8-worker cap lives only in the *suggestion*; the config accepts any count the 50k-per-partition floor allows, and workers are latency-bound, not budget-bound, so scaling is near-linear under keyset. The ceiling: **max useful workers ≈ aggregate rate cap ÷ per-worker natural rate**, where natural rate = the probe's measured rec/s ÷ 50 (records per page — a hard Coupa cap: `limit` values above 50 are ignored, verified with `limit=100` and `limit=200`, so page size is not a tuning lever; concurrency is the only one). Example: 40.9 rec/s → ~0.82 req/s per worker → ~24 workers before a 20 req/s cap binds; 16 workers turn a 26.5 h dataset into ~1.7 h at ~13 req/s aggregate. The soft limit beyond that is Coupa's own (undocumented) concurrency tolerance — push upward gradually and watch for `[WARN] Coupa 429` lines, which are the tenant telling you where that limit is (backoff absorbs them; sustained 429s mean back off on workers). Small datasets finish in the first minutes regardless, so the whole run's wall clock is set by the biggest dataset's worker count — spend the budget there.

   Until the config exists, a run-ordering prior that holds across Coupa customers: transactional datasets dominate — purchase_order_lines > purchase_orders and lookup_values are typically the giants (hundreds of thousands to millions), suppliers/users mid-sized, and reference tables (uoms, payment_terms, tax_codes, account_types) finish in minutes regardless of order.

**Artifact:** Confirmed list of dataset keys, Coupa credentials, hook IDs to disable, Rossum org URL, and per-dataset `workers` values calibrated from the probe report.

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

3. **Create indexes on empty collections.** Create all four indexes before loading any data — indexes on empty collections build instantly, whereas indexes on millions of records cause slow background builds that can impact query performance during replication. **Confirm with user before executing each index creation.**

   | Index | Keys | Type |
   |-------|------|------|
   | `__<id_key>_unique_idx` | `{"<id_key>": 1}` | Unique partial — options `{"unique": true, "partialFilterExpression": {"<id_key>": {"$exists": true}}}` |
   | `__digest_md5_idx` | `{"__digest_md5": 1}` | Regular |
   | `__dynamic_index` | `{"$**": 1}` | Wildcard |
   | `default` | dynamic mappings | Atlas Search |

   The **unique partial index on the dataset's `id_key`** is the root-cause duplicate fix: duplicates become impossible at the DB layer, even across races the script's pre-insert check cannot see (mid-run anchor-window entries, backdated writes, concurrent writers). DS support is live-verified: `POST /indexes/create` with those options returns 202 and the index lists back with both properties intact; a duplicate `id` insert is then rejected (as the usual opaque HTTP 400), while id-less documents still insert freely thanks to the partial filter. The **partial filter is not optional**: a unique-but-non-partial index on `id_key` would reject the second id-less document as a duplicate null (surfacing as poison failures) — the script flags such an index distinctly and recommends dropping and recreating it as partial. The script verifies the index at the start of every full run and **aborts when it is confirmed missing or non-partial** — override with `--no-unique-index-ok` to proceed on the per-batch check alone (concurrent-writer races unprotected; the flag is inherited by supervised children). A failed index *listing* only warns. It never auto-creates the index, because a collection loaded before this guidance may already hold duplicates that would fail the build (run the Phase 4 duplicate audit first).

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

**Artifact:** Target collections exist with all four standard indexes. Import hooks are disabled. Python environment is ready.

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
       "client_secret": "<from hook settings>",
       "max_requests_per_second": 20
     },
     "rossum": {
       "api_url": "<org_url>/api/v1",
       "ds_url":  "<org_url>/svc/data-storage/api/v1",
       "token":   "<bearer token; refreshed automatically if --username/--password is passed at runtime>"
     },
     "ds_batch_size": 5000,
     "min_partition": 50000,
     "datasets": {
       "<key>": {
         "endpoint":     "api/<coupa_endpoint>",
         "collection":   "<dataset_name_from_hook>",
         "id_key":       "id",
         "scope":        "<oauth_scope_from_hook>",
         "fields":       [<field_list_from_hook_exactly_as_configured>],
         "workers":      1,
         "extra_params": {}
       }
     }
   }
   ```

   Each dataset's `fields` list must exactly mirror the corresponding hook's field configuration — it is the projection sent to the Coupa API. (The script always adds `id` to the projection itself — the pagination cursor needs it even when `id_key` differs.)

   **`extra_params`** (optional, default `{}`): a dataset can be a **filtered slice** of its Coupa endpoint rather than the whole thing — e.g. a lookup slice needs `lookup[name][in]` + `active`, an invoice load needs a `created-at[gt_or_eq]` floor. Whatever key/value pairs go here are merged into **every** Coupa call the script makes for that dataset: fetching, `--probe` counts/throughput, and partition planning alike — so the count, the plan, and the load all describe the same slice instead of drifting apart. Keys the script itself manages (the cursor/anchor/projection set: `fields`, `order_by`, `dir`, `offset`, `limit`, `updated-at[lt_or_eq]`, `id[lt]`, `id[gt]`) are rejected at startup if present here — overriding one would corrupt keyset pagination or re-slice partitions mid-run.

   **Rate cap:** `max_requests_per_second` (default 20) is a self-imposed aggregate ceiling — Coupa allows 25 req/s per OAuth client with no rate headers and no Retry-After, so headroom beats brushing the limit (a 429 costs a blind multi-second backoff, and the budget is shared with everything else on that client: token mints, probes, retries, and any live import webhooks). The supervisor splits the cap evenly across its children. **Drop to ~15 when import webhooks are already active on the same OAuth client** (re-replication of a live org, or the Phase 5 early-handoff variant).

   **`min_partition`** (optional, default 50000): the floor below which `plan_partitions` and the `--probe` workers suggestion refuse to split a dataset further — no partition ever holds fewer than this many records, however many workers are requested. Field runs have seen this floor, not the Coupa API, be the binding constraint on worker count for a mid-sized dataset (e.g. ~200k records capped at 3–4 workers regardless of a faster measured rate); lower it if a dataset is mid-sized and CPU/latency-bound rather than API-bound, or raise it if per-worker overhead (token mints, planning probes) isn't paying for itself at the current floor.

   **Workers:** optional per dataset (`"workers": N`, default 1) — N supervised children crawling disjoint, count-balanced id ranges in parallel. Size from the Phase 0 probe report; requires `--supervise`. CLI `--workers N` overrides the config for every selected dataset (e.g. `--workers 1` forces a serial run without editing the config).

3. **Gitignore the runtime files.** Add the following to the project's `.gitignore` so credentials and run state never leak into version control:

   ```
   coupa_bulk_import.config.json
   coupa_import_state*.json
   logs/
   ```

   Only `coupa_bulk_import.config.example.json` (placeholder template) should be checked in.

4. **Smoke-test the configuration** before starting the full load:

   ```bash
   python3 -u coupa_bulk_import.py --dataset <key> --smoke
   ```

   If the config path is non-standard, pass `--config <path>`.

   `--smoke [N]` (default N=1) is self-cleaning: it inserts the newest N records, verifies them, then deletes **exactly the records its own insert landed** (never a concurrent writer's copy, never pre-existing data) and prints the collection's remaining doc count — no manual cleanup step, and no state file is written or overwritten. It exits non-zero on any failed insert, verification shortfall, or cleanup shortfall, so `--smoke <key> && <full run>` is a real gate. It fetches via the same keyset query the full run uses, so a passing smoke also validates the query shape per dataset. Every DS call shares the import path's 401 token heal, and a credentials-only config (empty `rossum.token` + `--username`/`--password`) mints a token up front. N must fit in one DS batch (`ds_batch_size`); leftovers of a hard-killed smoke are harmless — every batch of any later run is existence-checked, so they dedupe automatically. `--smoke` cannot be combined with `--supervise`, `--resume`, `--probe`, or `--limit` (the script refuses each), and `--limit` similarly refuses `--supervise` (a limit-stopped child never writes the completed flag).

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

   The supervisor spawns one child process per **unit** — a whole dataset, or one partition of a dataset whose config sets `workers > 1` (own log `logs/<ds>.log` or `logs/<ds>_p<k>of<W>.log`, own state file). For partitioned datasets it first runs a one-time **preflight** (id_key sanity on a sampled real-field page; populated-collection warning — partition children always run `--resume`, so the fresh-run guards live here, and a per-child collection check would false-positive on sibling partitions' writes), then plans count-balanced id ranges (one count bisection + W−1 boundary-rank probes, anchored to the run) and **pre-seeds each partition's state file before spawning** — restarts of a child or of the whole supervisor read the plan back from the state files and never re-slice. Workers are clamped so no partition holds fewer than 50k records (floor). It prints `N child(ren), per-child cap X req/s` (the config rate cap split evenly; a warning fires below 0.5 req/s per child — more workers than the budget can feed) and then sweeps every 60 s with the decision table: state file has `"completed": true` → done; child alive → fine; child died without the flag → relaunch with `--resume` — max 3 attempts per unit, then give up on that unit so a permanently broken one cannot crash-loop or hold the run hostage. Exit code 0 only when every unit completed. Subset via `--dataset a,b,c`; tune with `--poll-interval` / `--max-restarts`; add `--username`/`--password` (password-auth users) for in-process token refresh in every child.

   A partitioned run refuses to start over an **unpartitioned** state file with progress (finish it with plain `--resume --workers 1`, or delete it — already-loaded records dedupe). Existing partition state files are always reused, never re-planned — this also means a fresh (non-`--resume`) supervised run skips a completed partitioned dataset; **delete its partition state files to re-crawl or to re-plan with a different worker count**. A partial or inconsistent partition-file set (supervisor killed mid-planning, or files left over from a different worker count) is refused with the same delete-to-re-plan recovery.

   **Keep-awake traps (laptop runs):**
   - Wrap the **supervisor**, not the workers: per-pid assertions (`caffeinate -w <pid>`) drop the moment that pid dies — and resumed jobs are new pids nothing covers.
   - Releasing an assertion on a machine idle for hours → sleep follows near-instantly (the idle timer counts from last input). There is no usable grace period.
   - Lid-close sleeps regardless of caffeinate: keep the lid open on AC power, or `pmset -a disablesleep 1` (remember to undo it).

2. **Monitor progress.** Supervisor decisions (launches, deaths with the dead job's last log line, give-ups) go to `logs/supervisor.log`; per-unit flush lines to `logs/<ds>.log` / `logs/<ds>_p<k>of<W>.log`:

   ```
   flushed → total      5000  last_id 4821337  last updated_at: 2024-03-15T10:23:45Z
   ```

   `last_id` is the keyset cursor — the lowest Coupa id fetched and flushed so far; it counts DOWN toward the partition floor (or 0).

3. **Resume after interruption.** The supervisor already resumes crashed children. If the supervisor itself stopped, relaunch it with `--resume` — completed datasets are skipped, incomplete ones continue from their state files:

   ```bash
   nohup caffeinate -is python3 -u coupa_bulk_import.py --supervise --dataset all --resume \
     >> logs/supervisor.log 2>&1 &
   ```

   A unit the supervisor **gave up on** needs its failure investigated first (its last log line is in `logs/supervisor.log`) — typically an expired token (fix the config; see Phase 0 token strategy) or a Coupa-side error — then rerun the command above. Always resume a supervised run with `--supervise` again: supervised state is per-unit, and an unsupervised comma-list resume reads the shared state file and will not see it.

   **Version note:** state files written by pre-keyset script versions (they carry `offset`, no `last_id`) are refused on `--resume` with a clear message. Recovery: delete the state file and restart the dataset fresh — already-loaded records are skipped by the per-batch existence check, so the cost is re-fetch time, not duplicates.

4. **Understand log messages:**

   | Message | Meaning |
   |---------|---------|
   | `[Rossum token expired — refreshing]` | Auto-refreshed via `--username`/`--password`; no action needed |
   | `[Rossum 401 — re-reading token from config]` | No credentials passed; retried once with the (possibly refreshed) config token |
   | `[Coupa token expired — refreshing]` | Auto-refreshed via client credentials; no action needed |
   | `[RETRY N/5] SSLError` | Transient DS connection error; retrying with exponential backoff |
   | `[RETRY N/8] Coupa HTTP 429/503 — backing off Ns` | Coupa rate/availability blip; blind exponential backoff (no Retry-After exists), then the supervisor is the backstop |
   | `[WARN] Coupa 429 under the self-imposed cap…` | Another consumer is draining the OAuth client's 25 req/s budget (live webhooks? another run?) — consider lowering `max_requests_per_second` |
   | `[WARN] per-child rate under 0.5 req/s` | More workers than the aggregate cap can feed — reduce workers or raise the cap |
   | `N duplicate(s) skipped (expected after resume or smoke test)` | The per-batch existence check at work; healthy |
   | `[NOTICE] >=90% of the first batch already exists…` | Fresh run over an already-loaded collection — records are skipped, never updated; see "Re-replicating a dataset" |
   | `[WARN] N document(s) failed` | Real write failures; check payload size or field types |
   | `[WARN] N record(s) missing/falsy '<id_key>'` | Records without a usable id in this batch — inserted without dedup protection |
   | `Dataset '…': every record on the first page is missing a usable '<id_key>'` | Fail-fast abort — the dataset's `id_key` is almost certainly misconfigured |
   | `[WARN] collection '…' has NO unique index on '<id_key>'` (run aborts) | Create the Phase 1 unique partial index (audit old collections for duplicates first), or override with `--no-unique-index-ok` |
   | `[WARN] collection '…' has a unique index on '<id_key>' WITHOUT a partial filter` (run aborts) | Drop and recreate the index as partial — non-partial would poison-fail the second id-less document |
   | `supervisor: <ds> died … resuming (attempt N/3)` | Child crashed; auto-resumed |
   | `supervisor: <ds> exceeded 3 restarts — giving up` | Investigate that dataset's log, then relaunch the supervisor |
   | `[WARN] poison document skipped (<id_key>=…)` | A document DS rejects even alone (and whose id is confirmed absent); skipped after per-record isolation, run continues |
   | `[NOTE] collection '…' already holds N document(s)` (N < 100) | A few leftovers (e.g. hard-killed smoke) — they dedupe automatically; no action |
   | `[WARN] collection '…' already holds N document(s)` (N ≥ 100) | Fresh run over a loaded collection — existing records are skipped, never updated; see 'Re-replicating a dataset' |

5. **Keep a migration journal.** Append to `MIGRATION-NOTES.md` (next to the config) *at the moment* anything surprises: a probe estimate that misses, a 429 burst, an endpoint quirk, a manual intervention, anything you had to figure out. Timestamp each entry. Field experience: learnings reconstructed from logs days later lose the "why"; notes written live are what makes the Phase 6 debrief cheap and honest. The supervisor's exit summary (`logs/run_summary.jsonl`, one JSON line per invocation with per-unit durations, effective rec/s, restarts, and 429 counts) captures the *numbers* automatically — the journal captures the *judgment*.

6. **Verify no silent data loss.** After each dataset completes, compare **`total_inserted`** from its state file with the actual DB count (`data_storage_aggregate [{"$count": "total"}]`). For a partitioned dataset, sum `total_inserted` across all `coupa_import_state_<ds>_p*of*.json` files. `total_processed` counts everything handled *including* duplicate-skips — on resumed runs it legitimately exceeds the DB count; `total_inserted` is the number that must match.

**Artifact:** All target collections populated. State files show `"completed": true` for each dataset. DB counts match `total_inserted`.

---

## Phase 4: Completion

**Goal:** Datasets verified and, where needed, registered with MDH.

**Counts vs completion truth:** plan and report against `--probe`'s exact anchored counts (Phase 0), never against collection counts from another org — %/ETA are then facts, not guesses. Completion is still decided only by Coupa returning an empty page (per partition, when partitioned). The integrity check that matters afterwards: `total_inserted` (summed across a dataset's partition state files) == actual DB count.

**Steps:**

1. **Final count check.** For each dataset, confirm `total_inserted` (summed across partition state files for partitioned datasets) equals the actual document count in Data Storage. If they differ by more than one batch (5,000 records), investigate `[WARN]` lines in the logs before proceeding.

   Known benign skew: after a mid-batch token heal (a 401 struck while a batch was being written), `total_inserted` may **undercount by up to one batch** — records persisted just before the 401 are re-counted as duplicates by the healed retry. The DB count and the duplicate audit are authoritative; an up-to-one-batch shortfall next to a `[Rossum token expired / 401]` log line is expected, not data loss.

2. **Duplicate audit.** Per collection, run a DS aggregate grouping on the dataset's `id_key`:

   ```json
   [{"$match": {"<id_key>": {"$exists": true, "$nin": [null, "", 0]}}},
    {"$group": {"_id": "$<id_key>", "n": {"$sum": 1}}},
    {"$match": {"n": {"$gt": 1}}},
    {"$count": "dups"}]
   ```

   Expected result: empty (0 duplicates). A non-zero count means some records were double-inserted — investigate before handing off to continuous sync (and before creating the unique partial index on an old collection). The leading `$match` **excludes** records with a missing/falsy `id_key`: without it, two or more such records would bucket together (`_id: null` etc.) and false-positive as duplicates. Consequently the audit says nothing about those records — they are never dedup-protected, so a resume overlap can legitimately hold duplicate copies of exactly them (the run warns per batch: `[WARN] N record(s) missing/falsy '<id_key>'`). On multi-million-document collections add `"allowDiskUse": true` to the aggregate call — the `$group` can exceed the in-memory stage limit.

3. **Register collections with MDH** (if the collections need fuzzy search or UI visibility). A seed CSV with only the header row is sufficient — MDH manages the collection from then on:

   ```bash
   curl -X POST "<org_url>/svc/master-data-hub/api/v1/dataset/<name>" \
     -H "Authorization: Bearer <token>" \
     -F "file=@seed.csv;type=text/csv" -F "encoding=utf-8" -F "dynamic=true"
   ```

   See `mdh-reference` for full MDH dataset management details.

4. **Record the anchors, then clean up.** Phase 5's delta seeding needs each dataset's `anchor_updated_at` from its state file (a partitioned dataset's partitions all share one anchor — read any of its partition files) — copy them out BEFORE deleting state files. Then state files can be deleted and logs archived.

**Artifact:** Collections live in Data Storage with `total_inserted` == DB count and a clean duplicate audit, MDH registered (if applicable), anchors recorded for Phase 5.

---

## Phase 5: Handoff to Continuous Sync

**Goal:** Delta sync running via "Coupa Webhook Import" hooks — without triggering the full import this skill exists to avoid.

Prerequisite from Phase 4: each dataset's `anchor_updated_at` (the delta boundary below).

### Path A — the org already has import hooks (disabled in Phase 1)

Per dataset: confirm `total_inserted` == DB count (an up-to-one-batch shortfall can be the Phase 4 token-heal caveat — the DB count and duplicate audit are authoritative), then re-enable with `rossum_patch_hook` `active: true`. **Confirm with user before executing.**

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

### Variant — early handoff: activate hooks while the bulk run still crawls

When freshness matters more than a clean verification window, the hooks can go live **before** the bulk run finishes. This is sound because the two jobs' target sets are disjoint by construction: the bulk run only fetches `updated-at ≤ anchor`, and the hook is seeded with the literal `updated-at[gt_or_eq] = anchor` (step 1). The hook upserts by the id field and the bulk run skips existing ids, so any overlap converges to the correct final state — a record updated mid-run simply migrates from the bulk's set to the hook's, and the keyset cursor cannot skip anything because of it. (This variant was unsafe under the old offset pagination — mid-run set changes shifted the stream.)

Two costs, both manageable:

- **Shared rate budget:** the hooks drain the same Coupa OAuth client — lower `max_requests_per_second` to ~15 for the remainder of the run.
- **Verification:** the Phase 4 exact-count check stops being exact while hooks insert post-anchor records. Either verify a dataset *before* activating its hook, or filter the DB count to the bulk run's set: `{"updated-at": {"$lte": "<anchor>"}}` (lexicographic ISO-8601 comparison works). The duplicate audit is unaffected.

Default recipe stays verify-then-activate; use this variant deliberately.

**Artifact:** Hooks active for every dataset, first delta canary verified, `${last_modified_date}` placeholders restored.

---

## Phase 6: Debrief

**Goal:** Every learning from this migration lands in its one durable home, and the run's measurements are preserved for the next migration's calibration.

The raw material already exists by construction: `logs/run_summary.jsonl` (per-unit durations, effective rec/s, restarts, 429 counts — written automatically by every supervised run) and `MIGRATION-NOTES.md` (the live journal from Phase 3). This phase is optional in the sense that skipping it loses nothing *mechanical* — but each skipped debrief is a run the next migration cannot learn from.

**Checklist:**

1. **Estimates vs. reality.** Diff the probe's estimated durations against `run_summary.jsonl`'s actuals per dataset. A consistent miss means the sampled rate or the worker ceiling math needs adjusting — that is a *skill* learning, not a run anecdote.
2. **Grep the logs for pain.** `grep -c 'Coupa 429' logs/*.log`, give-ups in `supervisor.log`, `[WARN]` lines. Sustained 429s at N workers ≈ Coupa's real concurrency tolerance for this tenant — record the number.
3. **Doc-vs-observation diff.** Anything this SKILL.md *claims* that the run *contradicted* is a documentation bug — fix it, don't just note it.
4. **Route each learning to its home:**

   | Learning type | Durable home |
   |---|---|
   | Platform-general fact (Coupa behavior, DS behavior, script gap) | This skill / reference packs — marketplace PR |
   | Client-specific invariant (dataset sizes, tenant quirks, org decisions) | The client repo's CLAUDE.md / context notes |
   | Numbers for the next environment's run | Nothing to do — `run_summary.jsonl` stays in the migration dir; Phase 0 step 6 of the next run reads it |

5. **Keep `run_summary.jsonl` and `MIGRATION-NOTES.md`** when cleaning up state files (Phase 4 step 4 deletes state; these two survive — they are the migration's memory).

**Artifact:** Updated skill/docs where the run contradicted them, client context updated, summaries preserved.

---

## Key Technical Reference

### Re-replicating a dataset (e.g. the field list changed)

Insert-dedup is **not** upsert: a re-run over a loaded collection skips every existing record and **updates nothing** — the script flags this loudly (`[NOTICE]`) when a fresh run's first batch is ≥90% duplicates, and warns up front when the target collection holds 100+ documents. To re-replicate with a changed `fields` list:

- **Dev/UAT:** drop the collection (Phase 1 flow), then run fresh.
- **Live production collection:** blue-green — load into a temp collection (indexes build instantly on empty), swap via `data_storage_rename_collection`, then drop the old one. Avoids hours of empty/partial data under live MDH matching.

DS REST quirk while cleaning up: `find`/`aggregate` take `query`/`pipeline`, but `delete_one`/`delete_many` take `filter` — a 422 usually means the wrong key.

### Record identity and dedup (layered)

Records are inserted **exactly as received from Coupa**, with auto-generated Mongo `_id`s — structurally identical to records written by the Coupa import extension, which upserts by the `id` FIELD and never touches `_id`. (Two earlier designs were rejected in review: writing `_id = record[id_key]` broke that structural consistency — and DS's opaque-400 duplicate reporting made `_id`-based dup handling fragile anyway; scoping the existence check to the first flush only left holes — mid-run anchor-window entries and 401-heal retries landed in "provably fresh" batches unchecked.)

Duplicate protection is **three layers**, keyed on the id field (config `id_key`, default `id`):

1. **Unique partial index** (Phase 1) — the root guarantee: the DB rejects duplicates even across races the script cannot see (concurrent writers, records entering the frozen anchor window mid-run, backdated updates). The script verifies it at the start of every full run and aborts when it is confirmed missing or non-partial (`--no-unique-index-ok` overrides; a failed listing only warns).
2. **Pre-insert existence check before EVERY batch** — keeps accounting exact (`total_inserted`, duplicate counts) and avoids opaque-400 churn from index rejections. Necessary in its own right because the DS REST layer reports duplicate-key write errors as an opaque HTTP 400 ("batch op errors occurred") with no per-document detail (live-verified 2026-07) — and on collections without the unique index, double-inserts would not even error. The check costs ~one indexed aggregate per 5,000-record batch (~0.2% of wall time). It uses `$match`+`$group` (distinct values), immune to truncation by pre-existing duplicate copies.
3. **Phase 4 duplicate audit** — verification that the first two layers held.

Consequences:

- `total_inserted` (state file) counts actual inserts and must match the DB count; `total_processed` includes duplicate-skips.
- Records with a missing/falsy `id_key` value never enter dedup queries (a shared falsy id would collapse distinct records) and are exempt from the unique index (partial filter) — they always insert, are excluded from smoke-delete filters and from the Phase 4 audit, and are warned about per batch. A fresh run whose FIRST page is 100% missing/falsy ids aborts: the `id_key` is almost certainly misconfigured.
- Two datasets must not share a collection — the config loader rejects it.
- Smoke cleanup deletes exactly the id values its own insert landed (`BatchResult.inserted_values`), with the pre-insert snapshot as an intersection belt — a concurrent writer's record is never deleted.
- Re-inserts are skips, not updates — see "Re-replicating a dataset".

### Why `insert_many` instead of `bulk_write`

`bulk_write` is **async** (returns 202). Killing the script leaves queued operations still running — records keep arriving for minutes or hours after the process stops, making it impossible to cleanly drop and re-create collections.

`insert_many` is **synchronous** (returns 200). When the process stops, writes stop immediately. The state file count equals the DB count.

### Pagination strategy (keyset)

- **Query shape:** `order_by=id&dir=desc` + moving cursor `id[lt]=<lowest id fetched>` + `offset=0` always — every page is an indexed seek with **constant cost at any depth**. Partitioned workers add a static `id[gt]=<partition floor>`.
- **Why not offset:** `offset=N` makes Coupa skip N rows per page — cost grows linearly (field run: throughput halved from ~4,400/min to ~2,000/min past ~2M rows). Offset pagination also silently shifted the stream when a record's mid-run update moved it out of the anchored set — the keyset cursor is immune (a set change can never skip unrelated rows).
- **Sort direction:** id desc ≈ created-newest-first (Coupa ids are per-resource auto-increment), so the most match-relevant records land first and the early-handoff variant gets a usable dataset soonest.
- **Anchor:** `updated-at[lt_or_eq]=<timestamp set at run start>` — freezes the run's target set; also the delta boundary for Phase 5 seeding. Under keyset it no longer affects pagination consistency, only set membership.
- **Resume:** reload `anchor_updated_at` and `last_id` (plus the `partition` range, if any) from the state file and continue exactly where stopped. Pre-keyset state files (offset-based) are refused — delete and restart; dedup absorbs the re-fetch.
- **Counting/planning primitive:** `limit=1&offset=N` in `order_by=id asc` returns the record at rank N — used by `--probe`'s count bisection and by partition boundary planning (count-balanced slices regardless of id gaps).

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
