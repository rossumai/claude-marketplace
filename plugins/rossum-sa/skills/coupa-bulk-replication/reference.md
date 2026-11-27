# Coupa bulk replication — reference

Detail for `coupa-bulk-replication`, loaded on demand from SKILL.md.

## Contents

- Phase 5 handoff troubleshooting
  - Unresolved: a hook that imports nothing despite a verified non-zero delta
  - Variant — early handoff: activate hooks while the bulk run still crawls
  - Restoring `${last_modified_date}` — only where a data-bearing run is confirmed
  - A hook that fails every run: the 60-minute kill and the frozen window
- Technical reference
  - Re-replicating a dataset (e.g. the field list changed)
  - A collection first-synced by the import hook
  - Record identity and dedup (layered)
  - Why `insert_many` instead of `bulk_write`
  - Pagination strategy (keyset)
  - Token refresh — Rossum
  - Token refresh — Coupa
  - Collection management pitfalls
  - Standard indexes

## Phase 5 handoff troubleshooting

### Unresolved: a hook that imports nothing despite a verified non-zero delta

Recorded because it cost a field run hours and a full re-load, and because the cause was **not** established — do not assume any of the eliminated candidates below. That run predates the operation log (step 3): **read the dataset's operation log first** — a streak of `failed` / `Terminated by signal` rows means you are in "A hook that fails every run" below, which has a known mechanism and a recovery.

Symptom: for two datasets (the two largest, both with deeply nested `fields` projections), Coupa demonstrably held qualifying records (verified by direct query against the hook's own filter, ids above the bulk crawl's ceiling), the hook was active with a literal anchor covering them, dispatches logged clean `INFO`, and **zero records were ever imported** across many cron firings and manual invokes. Other datasets in the same org, same OAuth client, same run, imported correctly.

Eliminated with evidence, so don't re-spend time on them: the unique partial index (dropped it — no change); the projection/`fields` list (the bulk script fetched millions of records with those exact lists); `dir: desc` and `order_by` (removed/varied — no change *in this case*; `dir: desc` IS the cause of a different failure, the first-page hang below); collection size; `__digest_md5` absence on bulk-inserted rows (the service updates digest-less rows fine and does not re-stamp the digest on updates); MDH registration; malformed source data. A clone of the failing hook, identical except for `dataset_name`, imported its delta correctly on the first try — so the hook's own configuration is not at fault.

If you hit this: confirm the expected delta from Coupa (step 3), confirm it is genuinely absent from the collection by id, then treat it as service-side and escalate with those two observations. Meanwhile the data itself is recoverable without the hook — a bulk re-run picks the records up, since its fresh probe re-counts from Coupa.

### Variant — early handoff: activate hooks while the bulk run still crawls

When freshness matters more than a clean verification window, the hooks can go live **before** the bulk run finishes. This is sound because the two jobs' target sets are disjoint by construction: the bulk run only fetches `updated-at ≤ anchor`, and the hook is seeded with the literal `updated-at[gt_or_eq] = anchor` (step 1). The hook upserts by the id field and the bulk run skips existing ids, so any overlap converges to the correct final state — a record updated mid-run simply migrates from the bulk's set to the hook's, and the keyset cursor cannot skip anything because of it. (This variant was unsafe under the old offset pagination — mid-run set changes shifted the stream.)

Two costs, both manageable:

- **Shared rate budget:** the hooks drain the same Coupa OAuth client — lower `max_requests_per_second` to ~15 for the remainder of the run.
- **Verification:** the Phase 4 exact-count check stops being exact while hooks insert post-anchor records. Either verify a dataset *before* activating its hook, or filter the DB count to the bulk run's set: `{"updated-at": {"$lte": "<anchor>"}}` (lexicographic ISO-8601 comparison works). The duplicate audit is unaffected.

Default recipe stays verify-then-activate; use this variant deliberately.

### Restoring `${last_modified_date}` — only where a data-bearing run is confirmed

Step 1 says to flip the literal anchor back to `${last_modified_date}` "after the first successful run". Be strict about what counts: the placeholder is safe once the service has recorded a successful operation for that `dataset_name`, and if it has **not**, the placeholder resolves to epoch and the next cron attempts the full import this whole skill exists to avoid. Require both kinds of evidence: a **`finished` row in the operation log** (step 3) **and records having landed** from that job. A `finished` row alone is not enough: a job over an empty window finishes in under a second with nothing to import, and whether such a row counts as history is not established.

So restore the placeholder per dataset, as each earns it:

- **Confirmed data-bearing run** (a `finished` operation, and you watched the collection grow or verified specific delta ids arrived) → restore `${last_modified_date}`. Then confirm the **next** job finishes too: on a healthy delta it takes seconds (a field run: fixed-date jobs over a ~370-record window took 22–32 s; the first job on the restored placeholder took 1 s, because its window started at the previous job's start).
- **Not yet confirmed** → leave a literal anchor, set to the *most recent* bulk-run anchor for that dataset. It carries **zero** epoch risk and the upserts are idempotent, so it is safe *as a holding position* — but it is not a resting state: only the anchor's lower bound is fixed, so the window it re-pulls **grows with every hour that passes**. A literal anchor left in place for weeks eventually re-imports weeks of changes on every single cron firing.

Treat any dataset still on a literal anchor as an open item with a deadline, not a finished state — the whole point is to earn the placeholder as soon as a delta demonstrably flows.

### A hook that fails every run: the 60-minute kill and the frozen window

Field-verified on a live org where three Coupa import hooks failed every run for weeks — one since its handoff day — while their hook logs showed nothing but clean `INFO` rows.

**How the extension and the service divide the work:**

1. The **hook** is only a timer. On each cron tick it posts its settings to the scheduled-imports **service**, which answers 202 at once. That is the whole hook log row.
2. The service runs **one job per dataset at a time**. A dispatch that arrives while that dataset's job still runs is refused — recorded as a `failed` operation with `"A different operation is waiting or running already"`, or, in later observations, not recorded at all. Jobs for different datasets run in parallel (a field check: every one of 623 such refusals coincided with the same dataset's own running job; 296 jobs of one dataset overlapped another dataset's jobs without conflict).
3. The job resolves `${last_modified_date}`, pages through Coupa, and **upserts each page into Data Storage as it goes**.
4. The service **kills a job after 60 minutes** — operation `failed`, `error.text: "Terminated by signal"`, duration 60.0 min almost to the second. (Some kills come earlier; cause unknown.) The killed hooks carried no `job_run_settings`; the CIB 2.0 import shape in `coupa-baseline-reference` shows `job_run_settings.max_run_time_s: 36000`, so 3600 s may be the default — **unverified**, do not rely on raising it as the fix.
5. **A killed job keeps what it wrote but does not count as a success.** `${last_modified_date}` stays put, the next job's window is wider, and it is killed again. The loop never ends on its own.

**Why it hides:** each job writes its first pages before it dies, and the walk order puts the *newest-created* records first — so new records keep arriving and the collection looks fresh. What goes stale is **old records that change**: they sit at the far end of the walk, which no job reaches. A dataset can sit in this state for weeks.

**Signature:** in the operation log (step 3), a long streak of `failed` rows at ~60.0 min, the newest `finished` row weeks old, and the dispatch log still all `INFO`.

**Two causes seen, with different fixes:**

| Cause | Evidence | Fix |
|---|---|---|
| **The window outgrew one job** — a Coupa-side mass change (here, ~320k records created in one month) made a single delta bigger than 60 min of paging; every failure then widened it further | `--probe` with the hook's own filter plus `updated-at[gt_or_eq]=<newest finished create_ts>` (set via `extra_params`) counted ~495k records; nothing created after the last success existed in the collection | Literal `updated-at[gt_or_eq]` close to now, so one job finishes; gap-fill the skipped interval separately (below) |
| **`"dir": "desc"` on the hook query** — the service processed the first page and then hung until the kill, whatever the window size | A literal window of 371 records (8 pages) still ran to the 60-min kill. Records created after the job started landed within 4 s of its `create_ts` (readable from the inserted documents' ObjectId timestamps); nothing else landed for the rest of the hour. All 149 stale records were in the oldest part of the window. Removing **only** `"dir": "desc"` → the next job finished in 28 s and all 149 became current | Remove `dir` from the hook query. The bulk script's `dir=desc` is unrelated — it is its own keyset client |

**Diagnosis, in order:**

1. Operation log: confirm the failed streak; take the newest `finished` row's `create_ts` (the window's real start).
2. Size that window from Coupa: `--probe` on a scratch config whose dataset has the hook's filter plus that `updated-at[gt_or_eq]` in `extra_params` (point `collection` at an unused name — the probe does not write). Time one page with the hook's exact query (2–4 s per 50-record page is normal for wide, nested projections).
3. Compare by id: fetch `id` + `updated-at` for that window from Coupa (`fields=["id","updated_at"]`, cheap) and look each id up in Data Storage. Classify current / stale / missing, and look at *where* the stale ones sit in the hook's walk order.
4. Rule out the hang: if the window is a few pages and the job still dies at 60 min, look for `dir` in the query.

**Recovery:**

1. Set a literal `updated-at[gt_or_eq]` whose window one job can finish. If the stale set is small, set it far enough back to cover the stale records — the job then **is** the backfill (the field run: a date ~4 weeks back, 371 records, all 150 stale/missing ids current after one job). If the window would be too large, set it near now and gap-fill the earlier interval separately: the bulk script with `extra_params` `updated-at[gt_or_eq]` inserts the records that are **missing**, but it skips existing ids and so does **not** apply updates — load the interval into a scratch collection and upsert by `id` from there.
2. Wait for a `finished` operation, then verify the stale ids by `updated-at` in Data Storage.
3. Restore `${last_modified_date}` and confirm the next job finishes in seconds (see the section above).

A job that started before your PATCH runs on the old settings and blocks the dataset until it is killed — expect to wait up to an hour plus one cron interval before the first job on the new settings.

**Artifact:** Hooks active for every dataset, first delta canary verified, and `${last_modified_date}` restored on every dataset with a confirmed data-bearing run (others explicitly parked on a literal anchor). A day after handoff, every dataset's operation log shows `finished` rows, not a `failed` streak.

---

## Technical reference

### Re-replicating a dataset (e.g. the field list changed)

Insert-dedup is **not** upsert: a re-run over a loaded collection skips every existing record and **updates nothing** — the script flags this loudly (`[NOTICE]`) when a fresh run's first batch is ≥90% duplicates, and warns up front when the target collection holds 100+ documents. To re-replicate with a changed `fields` list:

- **Dev/UAT:** drop the collection (Phase 1 flow), then run fresh.
- **Live production collection:** blue-green — load into a temp collection (indexes build instantly on empty), swap via `data_storage_rename_collection`, then drop the old one. Avoids hours of empty/partial data under live MDH matching.

DS REST quirk while cleaning up: `find`/`aggregate` take `query`/`pipeline`, but `delete_one`/`delete_many` take `filter` — a 422 usually means the wrong key.

### A collection first-synced by the import hook

The scheduled-imports service pages with **offset** and the CIB 1.x `order_by: created_at`, which is not unique — a historical bulk load puts up to ~40 records in one second. Records that tie on `created_at` reshuffle between offset pages, so a record served twice displaces one that is never served. On one ~49k-row slice the hook landed **exactly** Coupa's row count, yet ~2.7% of ids were byte-identical pairs (written ~1 s apart in the same crawl) and the same number of records were missing. A keyset gap-fill inserted exactly the missing count, and the recovered records cluster on tied timestamps.

- **Verify by distinct ids, never by row count:** compare the number of distinct `id_key` values (a `$group` on `id_key`, then `$count`) with `--probe`'s exact count, and run the Phase 4 duplicate audit. Do this **before** creating the unique partial index — its build fails on the duplicates.
- **The unique index blocks only half of it:** it stops the duplicates, but the missing records stay silent.
- **Prefer this script for any large first sync,** even one the hook could finish: keyset paging on `id` cannot skip or repeat records.
- **Repair (field-verified):** delete the later `ObjectId` of each duplicate pair by `_id` `$in` (count first, as a dry run) → create the unique partial index → a fresh, non-`--resume` run of the script over the populated collection acts as the gap-fill (existing ids are skipped, missing ones inserted). Three cautions:
  - **Delete the dataset's old state files first** (`coupa_import_state_<ds>*.json`). A fresh run does not clear them before its first flush, and a leftover `completed: true` makes the supervisor count the unit as done at once.
  - **This index is built on a populated collection** — the exception to "create indexes on empty collections". Expect a slower background build; it can only succeed after the duplicates are gone.
  - **Verify by distinct ids, not `total_inserted`.** After a gap-fill, `total_inserted` is only the number of missing records, so the usual `total_inserted == DB count` check (Phase 4, Phase 5) does not apply: check distinct `id_key` values == `--probe` count and a clean duplicate audit instead.

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
