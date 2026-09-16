# sftp-folder-probe

A manual-invocation function hook that runs an SFTP client **inside Rossum**, because the customer
host is IP-allowlisted and your laptop is not on the list.

## When you need it

**Symptom.** A local `sftp` or paramiko connect to the customer host hangs or dies during banner
exchange, while the Rossum import/export extensions against that same host keep working. That is an
IP allowlist, not a credential problem — do not go debugging credentials.

**Diagnosis.** This hook prints its public egress IP as the first line of output, *before* it tries
to connect. That IP is what the customer has to allowlist, and it is the single most useful thing to
know before debugging anything else.

Once it runs, it answers what no local tool can: where the files actually are, whether an export
really landed, what a landed file actually contains, and whether an incumbent importer is about to
claim the new entity's data.

## ⚠️ Ask the user before you create it

**Never deploy this unsupervised.** It can write to and move files on a customer's SFTP. Propose it
in one line, say plainly what it can do, and wait for an explicit yes:

> "The SFTP is IP-allowlisted so I can't reach it from here. I can deploy a manual-invocation probe
> hook into the <org> org that runs from Rossum's egress — it lists the tree, reads files back, and
> can stage or quarantine files. Create it?"

## Deploy shape

| field | value |
|---|---|
| `type` | `"function"` |
| `runtime` | `"python3.12"` |
| `events` | `["invocation.manual"]` |
| `queues` | `[]` — attaching it to a queue would fire it on real documents |
| `third_party_library_pack` | must include `paramiko` |
| name | prefix `ZZ ` so it sorts to the bottom of the hook list and reads as a throwaway |
| password | hook **Secrets**, never `settings` — so it never reaches config, git, or a transcript |

Fill the `«entity_marker»` seam before deploying. Everything else is runtime settings:

```json
{
  "host": "sftp.example.com",
  "username": "rossum",
  "roots": ["/"],
  "max_nodes": 400, "max_depth": 5, "sample_files": 8,
  "skip_walk": false,
  "probe_paths": ["/out/ap/material"],
  "cat_paths": ["/out/ap/material/INV_123.csv"],
  "write_files": [{"path": "/in/ap/vendors/TEST_vendors.CSV", "content": "..."}],
  "move_files": [{"from": "/in/ap/vendors/BAD.CSV", "to": "/in/ap/vendors/archive/BAD.CSV"}]
}
```

Invoke with `POST /hooks/{id}/invoke`.

**Read the hook LOG, not the response.** The report is printed to stdout. `/invoke` does return the
dict, but anyone running it from the UI sees only the log — which is why the report goes to stdout
at all.

**Delete the hook when the investigation is done**, and tell the user you have.

## The four capabilities, and why each is here

- **`probe_paths`** — direct listdir of named paths, skipping the walk (`skip_walk: true`). One
  round trip instead of every directory above the target.
- **`cat_paths`** — read a file back. Listing proves an export *ran*; only content proves its
  FORMAT. It reports lines and fields-per-line, which is what catches a template that emitted each
  field on its own line: fine field-by-field, unparseable as CSV.
- **`write_files`** — stage a known-good file, to prove the import pipeline end to end.
- **`move_files`** — quarantine a file an importer keeps retrying. The SFTP import method schema
  accepts only `path`, `id_keys`, `file_format`, `file_match_regex` and `encoding` — there is **no
  failure action**, so a file that fails to parse is retried on every cron tick forever and no
  configuration stops it. Moving it out of the watched folder is the only remedy.

## Traps this was built around

- **An unbounded walk times out and returns NOTHING** — not a partial map, nothing, because the hook
  dies before it prints. Hence the `max_nodes`/`max_depth` budget and `SKIP_DIR_NAMES`: one
  `archive` folder held 41,192 files, and listing it is what pushed a prod account past the timeout.
  Budgets are overridable per run because different chroots have wildly different tree sizes.
- **Extension CASE decides whether an active importer claims a file.** An importer matching
  `.*\.CSV` ignores `.csv`. The same listing therefore means either "inert files sitting in a
  folder" or "the new entity's data is replacing the incumbent's" — one letter apart. That is why
  the report tallies `.CSV` vs `.csv` per directory instead of only listing filenames.
- **A chrooted account lands somewhere other than `/`**, so an absolute path read off one account's
  map is meaningless against another's. `sftp.normalize('.')` costs one round trip and makes every
  path in the report interpretable.
- **`POST /hooks/{id}/invoke` stops waiting at ~50s** even when `config.timeout_s` is longer. The
  run completes server-side but returns HTTP 400 "Hook invocation timed out" and the response is
  lost. Never conclude the run failed from that 400 — read the hook log. Keep the probe's own budget
  under ~40s.
- **A successful empty listing proves nothing** on S3-backed SFTP: every path lists as "exists,
  empty". Only a folder that already holds files is proven real.
