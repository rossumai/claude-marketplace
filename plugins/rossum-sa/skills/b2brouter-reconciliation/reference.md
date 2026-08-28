# B2Brouter reconciliation — reference

Full note taxonomy, all flags, exit codes, and the measured API gotchas
behind `recon.py`. Read alongside `SKILL.md`, which covers the operator
walkthrough; this document doesn't repeat that.

`recon.py` is a read-only command-line tool that reconciles invoices
received on the [B2Brouter](https://www.b2brouter.net/) e-invoicing network
against the annotations that landed in [Rossum](https://rossum.ai/). An
importer hook pulls invoices from B2Brouter into a Rossum queue; if that
pipeline drops a document, stalls partway through, or double-imports it,
nothing inside Rossum tells you — Rossum only knows what it received, not
what B2Brouter sent. This tool queries both sides independently and joins
them by e-invoice id, so it can report the one thing a Rossum-only view
cannot prove: that every invoice the network handed over actually arrived,
and arrived exactly once.

It only ever reads. It writes a CSV report and process output; it never
modifies an annotation, a document, or an invoice on either system.

## Requirements

- Python 3.10 or later (the code uses `X | None` type hints). No third-party
  packages — everything is standard library, so there is nothing to
  `pip install`.
- A Rossum API token with read access to hooks, annotations, and documents.
- One or more B2Brouter API keys, each with **accounts read** and
  **invoices read** permissions.

A key needs more than "can read one invoice by id" — it must be able to
**enumerate** (list) every invoice on an account, and it must be able to
list the account itself. Those are different capabilities, and a key can
have the second without the first: a key that can fetch a known invoice but
cannot list an account's invoices will silently make that account
impossible to reconcile.

B2Brouter API keys are scoped **per account group**, not per organization,
so a real organization routinely has several groups and therefore supplies
**several keys** — this is the normal case, not an edge case. Each key goes
in its own `B2B_API_KEY_<LABEL>` variable (see Usage below).

Coverage matters at the account level too. The tool discovers which
B2Brouter accounts are in scope from your organization's own importer
hooks — you don't configure this. Every one of those accounts must be
visible to at least one of the keys you supply. An account that no key
covers is never silently skipped: it is reported as unverified, and the run
exits non-zero, because a report that quietly omits an account is worse
than no report at all.

With several keys in play, one going bad (revoked, stale, typo'd, or simply
the wrong account group) must not sink the whole run. Each key's visibility
is probed individually: a key that fails is named — by its **variable
name**, never its value — in a warning, and skipped, while the remaining
keys still cover whatever they can. Its accounts fall through to the same
"no key can see it" path as any other uncovered account, so the report only
ever becomes *more* cautious, never falsely clean. The one exception: if
*every* supplied key fails its probe on a given host, the tool aborts for
that host instead of quietly reporting zero coverage as a clean result —
naming every failed variable so you know which credentials to check.

## Usage

### Set your credentials first

**Recommended: a credentials file, so keys never have to go through a chat
with an agent.** Generate a template once, fill it in yourself, then let the
tool read it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
  --init-credentials
```

This writes a template to `~/.config/rossum-b2brouter-recon/credentials.json`
(pass a path to put it somewhere else) with owner-only (`0600`) permissions,
and refuses outright if that file already exists — a filled-in file can
never be clobbered by a stray re-run. It prints the path and nothing else;
it never prints the file's contents. Open that path yourself and replace
every `--PASTE...HERE--` placeholder — **never paste a token or key into
this chat**, and the tool itself never prints one back either. Then run
normally, pointing at the file:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
  --credentials ~/.config/rossum-b2brouter-recon/credentials.json \
  --show-discovery
```

Once a file exists at the default path, every run picks it up automatically
— `--credentials` is only needed to point at a different path. `base_url`
and `ui_host` in the file are used as defaults; the matching CLI flags
still override them, and `--ui-host` stops being required once the file
supplies `rossum.ui_host`. See "Credentials resolution order" below for the
exact precedence, and the `keys` shape a moment below.

**The alternative, for CI or anywhere a file isn't practical:** environment
variables, exactly as before this flag existed —

```bash
export ROSSUM_TOKEN=<rossum-api-token>
export B2B_API_KEY=<b2brouter-api-key>
# additional keys, e.g. for a second account group, region, or environment:
export B2B_API_KEY_EU=<another-b2brouter-api-key>
```

B2Brouter keys are scoped per account group, so having several of these is
normal — one per group, each in its own `B2B_API_KEY_<LABEL>` variable
(`B2B_API_KEY` itself, plus any number of `B2B_API_KEY_*`, are all picked
up). If one of them turns out to be revoked, stale, or the wrong group, it
is reported by variable name and skipped — the rest of the run still
proceeds on the keys that work; see Requirements above. Every group still
needs its own restricted key with **accounts-read and invoices-read only**,
whichever route supplies it.

### Credentials resolution order

1. `--credentials PATH`, if given — used wholesale; a missing, unreadable,
   malformed, or incompletely-filled-in file at this path is a hard refusal
   (exit 2), never a fall-through to environment variables.
2. Otherwise, `~/.config/rossum-b2brouter-recon/credentials.json`, if it
   exists — same all-or-nothing handling.
3. Otherwise, environment variables (`ROSSUM_TOKEN`, `B2B_API_KEY*`),
   exactly as before this flag existed.

A file, once selected by either of the first two steps, is never partially
trusted: a required field left as its `--PASTE` placeholder aborts the run
rather than silently falling back to whatever the environment happens to
hold. An account-group entry under `b2brouter.keys` whose *value* is still a
placeholder is simply skipped (not an error) — so the template's two example
groups don't become two bogus keys when only one is filled in. Any JSON key
beginning with `_` is a comment and is ignored, at any depth. Key values are
never logged, printed, or included in any error message — only their
labels are.

**Always start with `--show-discovery`, before any full run.** It prints
every discovered channel — one importer hook per line, with its queues,
its account count, and the B2Brouter host it talks to — with no report
generated. That listing is the blast radius of the reconciliation: if a
channel or an account is missing from it, it will be missing from every
report too.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
  --ui-host example-org.rossum.app \
  --show-discovery
```

```
hook 55501 [active] Purchase invoices — EU: queues=[111, 112] accounts=3 base=https://app.b2brouter.net
hook 55502 [active] Purchase invoices — staging: queues=[113] accounts=1 base=https://staging.b2brouter.net
```

**Then run `--check-coverage`, before any full run.** `--show-discovery`
answers "what is in scope?"; `--check-coverage` answers "can I actually see
all of it?" — it probes, per channel, how many of its accounts the supplied
keys can see, names any that are not covered, and exits without listing a
single invoice. Fixing coverage here is far cheaper than discovering it
from an `UNVERIFIED_SOURCE` row after a full run.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
  --ui-host example-org.rossum.app --check-coverage
```

```
Purchase invoices — EU: 3/3 accounts covered
Purchase invoices — staging: 0/1 accounts covered
    uncovered: 900301
```

Once coverage looks right, run a full report. With no `--from`/`--to`, the
window defaults to the last 30 days:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
  --ui-host example-org.rossum.app \
  --out recon-30d.csv
```

To investigate one channel over an explicit window, and see only the rows
that need attention:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
  --ui-host example-org.rossum.app \
  --channel 55501 \
  --from 2026-08-01T00:00:00Z --to 2026-08-08T00:00:00Z \
  --only-exceptions \
  --out recon-exceptions.csv
```

`--channel` accepts either a hook id or a case-insensitive substring of the
hook's name. `--only-exceptions` drops every row whose note is `ok` or
`ok +xml_twin`, so the CSV holds only the rows worth reading.

### All flags

| flag | meaning | default |
|---|---|---|
| `--ui-host` | host used to build clickable annotation links, e.g. `example-org.rossum.app` (required unless a credentials file supplies `rossum.ui_host`) | — |
| `--base-url` | Rossum API base URL | `https://elis.rossum.ai`, or a credentials file's `rossum.base_url` |
| `--init-credentials [PATH]` | write a credentials template to PATH and exit (refuses to overwrite an existing file; never prints file contents) | `~/.config/rossum-b2brouter-recon/credentials.json` |
| `--credentials PATH` | read credentials from this file instead of the environment — see "Credentials resolution order" above | — |
| `--from` | window start, ISO date or datetime | 30 days before `--to` |
| `--to` | window end, ISO date or datetime (must not precede `--from`) | now |
| `--channel` | hook id or name substring to restrict to | all discovered channels |
| `--out` | CSV output path | `b2brouter_reconciliation.csv` |
| `--only-exceptions` | write only non-`ok` rows | off |
| `--grace-minutes` | how long a just-arrived invoice may go unmatched before it is `MISSING_IN_ROSSUM` instead of `PENDING` | `30` |
| `--show-discovery` | print discovered channels and exit; no report is run | off |
| `--check-coverage` | print per-channel account coverage (and the uncovered ids) and exit; fetches no invoices | off |
| `--relax-x509-strict` | opt-in: clear `ssl.VERIFY_X509_STRICT` for both Rossum and B2Brouter connections; see [TLS interception](#tls-interception) | off |

## Reading the report

Each row is one B2Brouter invoice, joined to whatever Rossum annotation(s)
share its e-invoice id, plus one synthetic row per account that could not be
enumerated at all (see the `UNVERIFIED_SOURCE` entry below). The `account`
column names the B2Brouter account the row belongs to — useful on its own
for routing a row to the right receiving entity, and it is the only column
populated on a synthetic `UNVERIFIED_SOURCE` row. The `note` column is the
verdict:

| note | meaning | action |
|---|---|---|
| `ok` | one healthy annotation | none |
| `ok +xml_twin` | healthy, plus a `failed_import` XML sibling | none for the invoice; the twin can be deleted as cleanup |
| `PENDING` | arrived within the grace window | re-run later |
| `MISSING_IN_ROSSUM` | the source has it, Rossum has nothing | check `acked_at`: a timestamp → it was handed over and lost, escalate with the einvoice id; genuinely empty → it was never collected, which is a source-side fault; `not checked` → no conclusion available yet (see the `acked_at` column below), re-run or narrow the window |
| `STRANDED_CREATED` | only `created` annotations, no healthy sibling | the importer did not finish; recover one at a time, verifying each |
| `FAILED_IMPORT` | only `failed_import`, no healthy sibling | the XML was processed as a scan instead of being claimed; recover as above |
| `SPLIT_CONTAINER` | only a `split` annotation, no healthy sibling | the document was split into children; inspect the children before touching the parent — do **not** re-drive the container, its children are the real documents and re-driving it risks double-posting them |
| `DUPLICATE` | two or more healthy annotations | check whether both exported downstream; deduplicate at the destination |
| `DELETED` | every annotation for this invoice is deleted/purged, and no surviving copy of that invoice number was confirmed anywhere in Rossum | none. Recorded for visibility only — no cause is investigated or implied. **Note:** `DELETED` is also what an *unverified* row keeps (no invoice number to search by, the verification cap reached, or the search itself failed) — see below for how to tell those apart |
| `DELETED_AS_DUPLICATE` | another healthy annotation for this same invoice **number** was found elsewhere in Rossum, so the invoice is present under a different id | none — the invoice is accounted for |
| `UNVERIFIED_SOURCE` | that account's invoices were not established: it could not be listed at all, a Rossum-side e-invoice id inside the window was traced directly to this account by a B2Brouter lookup and matched no listed invoice, or the per-channel fallback/attribution lookup cap was reached before this invoice or id could be checked | the report is incomplete for this account (or this invoice) — the true state is unknown, not that it's fine; the `b2b_state` column carries the reason (e.g. the API error, "no API key can see it", the fallback-cap message, or "attributed to this account by a direct B2Brouter lookup"); fix credentials, narrow the window, or re-run before drawing any conclusion |
| `UNKNOWN_STATUS:x` | a status the tool does not know | classify it into `ARRIVED_STATUSES` or `NOT_ARRIVED_STATUSES` in `match.py` |

### The `acked_at` column

`acked_at` carries three distinct values, and the difference is the whole
point of the column, not a formatting detail:

- **A timestamp** — B2Brouter's own per-invoice acknowledgement time,
  fetched from the per-invoice *detail* endpoint, never the listing. It
  means the importer collected the invoice and B2Brouter's own job for it is
  done — the source will **not** re-deliver it. On a missing invoice this is
  the "our side lost it" signal: escalate with the einvoice id, don't wait
  for the source to resend, it never will.
- **A genuinely empty cell** — the detail lookup succeeded and the invoice
  really has no acknowledgement yet. On a missing invoice this is the
  "the source never handed it over" signal, and it may still arrive.
- **`not checked`** — nobody looked, so no conclusion should be drawn either
  way. This is the value every row starts with, because the LIST endpoint
  this tool otherwise uses returns `ack_at: null` for *every* invoice
  regardless of the truth (see Gotchas below) — a bare blank cell here would
  be indistinguishable from "genuinely empty" above and would misread as
  "never acknowledged" even when it was.
- **`lookup failed`** — a detail lookup was attempted for this row but did
  not resolve (network failure, 404, or an unrecognised response shape). Not
  a verdict on acknowledgement either way; re-run to get a real answer.

The detail lookup is only ever spent on **exception rows** — every note
other than `ok`, `ok +xml_twin`, and `PENDING` — because the
handed-over-and-lost vs. never-handed-over distinction is only actionable on
a row already flagged as needing attention; a clean row has nothing to act
on regardless of which one it would turn out to be, so it stays `not
checked`. This keeps the cost to a handful of calls per run instead of one
per invoice. Like the fallback and attribution tiers above, it is capped
(`ACK_LOOKUP_CAP` in `recon.py`) and the channel summary reports how many
lookups were used out of the cap; if the cap is reached, the remaining
exception rows are left `not checked` and the run says so explicitly rather
than leaving them silently blank.

### `DELETED` and `DELETED_AS_DUPLICATE`

This tool does not reason about *why* an annotation was deleted — that is
a deliberate choice, not an omission. When every annotation for an invoice
is deleted or purged, the row gets one of exactly two flat, neutral labels,
distinguished purely by one piece of evidence: **was a surviving copy of
this invoice found elsewhere?** Neither label counts toward "rows need
action" and neither drives a non-zero exit; both are still reported, as
rows and in the per-channel note summary (`DELETED` always gets its own
summary line — it is never folded into the `DELETED_AS_DUPLICATE` count),
because they are real information about channel overlap.

The row starts out `DELETED` — that is all classify() itself ever knows,
since it only sees one invoice's own annotations and has no way to tell
whether a *different* e-invoice id elsewhere in the organization carries a
survivor. `DELETED_AS_DUPLICATE` is never assumed; it is only ever **earned**
by an actual check: for every `DELETED` row with a non-empty
`invoice_number`, this tool searches Rossum by that invoice **number**
(`field.document_id.string`, the extracted content — not the e-invoice id)
for a healthy annotation anywhere in the organization, including outside
this channel's own queues. The survivor may have arrived under a completely
*different* e-invoice id (a re-import, a manual upload, a different
channel), which is exactly why pairing by e-invoice id alone can never
resolve this bucket — measured live, it was the largest single bucket in
the report (222 of 246 previously-actionable rows under the old taxonomy).

- If a healthy annotation is found, the row is promoted to
  `DELETED_AS_DUPLICATE` — the word "duplicate" is earned here, because
  another copy demonstrably exists.
- If none is found anywhere, the row **stays** `DELETED` — now a confirmed
  absence rather than an unexamined one, though the label itself does not
  change to say so.
- If the row has no `invoice_number` to search by, the per-channel
  verification cap (`DUPLICATE_VERIFY_CAP` in `recon.py`, set high enough
  that a normal month's volume is checked in full) was already spent before
  this row's turn, or the search itself failed, the row is left **exactly
  as it was** — `DELETED`, unchanged, never silently promoted or silently
  treated as a confirmed absence. The channel summary reports how many rows
  were actually verified, and says plainly how many were not.

**This is the one place the label can mislead if you don't know the
distinction: `DELETED` on an unverified row means "we did not look", not
"nothing exists".** A row that was never checked (no invoice number, the
cap reached, or a failed search) is indistinguishable *in the CSV* from one
that was checked and confirmed to have no surviving copy — both simply say
`DELETED`. To tell them apart, read the channel summary's verified/
not-verified counts printed for that run; the label alone does not carry
that distinction.

A row can be `UNVERIFIED_SOURCE` even though its own invoice reconciled
fine. Once a Rossum-side e-invoice id inside the window has been traced to
an account, *every* row that account contributed this run is marked
`UNVERIFIED_SOURCE`, not just the row for the attributed id. That is
deliberate, not a bug: if that account's own listing failed to return an
invoice that Rossum demonstrably has, the listing has a proven gap, and
nothing else it reported this run can be trusted to be complete either — the
same reasoning already applied to an account that could not be enumerated at
all (uncovered, or an outright API error).

## Guarantees

- **No writes, anywhere.** The tool only issues `GET` requests, with one
  narrow exception: a single `POST` to Rossum's annotation search endpoint,
  used because it is the only endpoint that actually filters by creation date
  and by filename prefix (see Gotchas) — the tool deliberately does *not*
  filter on the e-invoice flag, because a `failed_import` XML twin is not
  flagged and those are exactly the rows that matter. That POST helper checks
  the request path, requiring it to *start* with the search path, and refuses
  to send anywhere else — there is no code path that can turn it into a
  write.
- **B2Brouter's `ack` parameter is always sent as `ack=true`, and verified
  not to mutate anything.** Despite the alarming name, `ack=true` is a read
  filter that selects the full invoice index rather than just the
  importer's unacknowledged queue (see Gotchas below); `ack=false` is never
  sent. Three consecutive `ack=true` list calls were measured to leave a
  pending invoice's `ack_at` and `updated_at` unchanged, and a follow-up
  `ack=false` call still returned that same invoice — running a report does
  not affect the importer's pipeline.
- **A partial run is never reported as clean.** If any account cannot be
  fully enumerated — bad credentials, a key without list access, a
  transient failure — that channel is marked incomplete and the process
  exits non-zero, rather than producing a report that looks complete but
  silently left out an account's invoices.
- **The per-id fallback tier is capped.** An invoice absent from the search
  index is chased with an exact-filename lookup, which costs two or more
  sequential requests. That tier is bounded per channel
  (`FALLBACK_LOOKUP_CAP` in `recon.py`, reported in every channel summary):
  in a mass incident, or whenever the index comes back empty, an unbounded
  fallback would make the run unfinishable exactly when it matters most —
  and a run that gets killed writes no CSV at all. Invoices past the cap are
  reported `UNVERIFIED_SOURCE` with a reason naming the cap, never
  `MISSING_IN_ROSSUM`: they were not checked, and claiming they are missing
  would over-report, which is its own kind of wrong.
- **The enumeration-contradiction check is per account, not per channel —
  and, per account, it is precise, not sibling-based.** A channel whose
  source listed *nothing at all* while Rossum holds e-invoices for its
  queues is reported unverified as a whole. But that check alone is
  disarmed by a single listed invoice anywhere in the channel, so a second,
  per-account check also runs. It is **window-scoped**: `einvoice_index()`
  is deliberately built wider than the reporting window (see the Gotchas
  entry below on the lookback), so before anything is counted, ids whose
  newest annotation predates the window are set aside as lookback-tail
  context, not orphans. Only among the remaining, in-window ids does the
  channel summary print the count that matters —

      ```
          2  Rossum e-invoice id(s) matched by no listed invoice (window-scoped)
         40  Rossum e-invoice id(s) outside the window (lookback-only, skipped from the count above -- deliberate, not a gap)
      ```

  — and the second line is not a shortfall: it says how many ids the index
  only holds because of the deliberate lookback, and were therefore excluded
  from the count above on purpose. Each of the in-window unmatched ids is
  then looked up in B2Brouter **by id** (one GET each, capped by
  `ATTRIBUTION_LOOKUP_CAP` in `recon.py` and reported the same way as the
  per-id fallback cap above) to learn which account owns it *and when the
  invoice itself arrived* — printed as a third context line —

      ```
          1  unmatched id(s) whose B2Brouter invoice arrived before the window (re-import artefact, not a gap -- excluded from attribution)
      ```

  — because an unmatched id can be the mirror image of the lookback tail:
  its Rossum *annotation* is inside the window (that's how it became
  "unmatched" at all), but the *invoice*, per B2Brouter's own record, arrived
  long before it — typically a re-import of an old document. No account's
  listing for this window could ever have returned it, however complete
  that listing is, so it is counted in the line above and its account is
  **not** flagged. Only when the invoice is confirmed to have arrived
  *inside* the window — or B2Brouter has no record of its arrival date at
  all, which is treated conservatively the same as "inside" rather than
  guessed to be benign — does the owning account get flagged
  `UNVERIFIED_SOURCE`. A 404 on the by-id lookup (the key cannot see that
  invoice at all) flags no account either, for the same "don't guess"
  reason, but stays counted in the window-scoped unmatched line above rather
  than being quietly moved into the artefact line — it is unresolved, not
  explained. An account that simply listed nothing during the window, with
  no unmatched id of its own, is **never** flagged — some accounts
  legitimately receive only a handful of invoices a year, and the old
  sibling-based version flagged several such quiet accounts across multiple
  channels on its first live run for exactly that reason.

## Exit codes

- **0** — clean run: nothing missing, nothing unverified, and nothing
  needing action. Every account was fully enumerated and every row's note is
  `ok`, `ok +xml_twin`, `PENDING`, `DELETED`, or `DELETED_AS_DUPLICATE`.
- **1** — at least one row needs action — *any* note other than the five
  above, including `STRANDED_CREATED`, `FAILED_IMPORT`, `SPLIT_CONTAINER`,
  `DUPLICATE`, `MISSING_IN_ROSSUM`, `UNVERIFIED_SOURCE` and
  `UNKNOWN_STATUS:*` — or at least one channel could not fully enumerate its
  accounts. The count is taken over every row produced, so
  `--only-exceptions` changes what the CSV holds, never what the exit code
  means.
- **2** — the run could not be performed: `ROSSUM_TOKEN` is not set, no
  `B2B_API_KEY*` variable is set, `--ui-host` is missing and no credentials
  file supplied `rossum.ui_host` either, the window is invalid (e.g. `--from`
  after `--to`), no importer hooks (channels) were discovered at all, or
  Rossum/the invoicing network rejected the token or key outright (HTTP
  401/403) — printed as one plain-language line naming which system rejected
  it, not a raw traceback. The same code covers every credentials-FILE
  failure too (see Credentials below): missing, unreadable, not valid JSON,
  or a required field (`rossum.token`) still left as its `--PASTE`
  placeholder — a bad file is a hard refusal, never a partial run.

Exit 1 on an incomplete run is deliberate, not conservative box-ticking. A
report that silently drops an unenumerable account, but still prints "0
exceptions" and exits 0, is exactly the failure this tool exists to catch:
it would read as clean while quietly not checking part of the traffic it
was asked to check. An incomplete run must look incomplete.

`PENDING` is the one non-`ok` note that keeps a run clean because its
verdict is simply not due yet — re-run after the grace window. `DELETED`
and `DELETED_AS_DUPLICATE` are the OTHER kind of non-`ok` note that keeps a
run clean, by the user's explicit decision: this tool does not reason about
*why* an annotation was deleted, so neither of these two flat, neutral
labels (see "`DELETED` and `DELETED_AS_DUPLICATE`" above) implies an action
is needed, whichever one a row lands on. They are still reported, as rows
and in the per-channel note summary — `DELETED` always on its own summary
line, never folded into the `DELETED_AS_DUPLICATE` count — because they are
real information about channel overlap. Matching them into the clean set is
by EXACT note string, never a `startswith`/substring test — the same
discipline `ok +xml_twin` already gets. Measured live: of 246 rows
previously counted as needing action under the old taxonomy, 222 were in
this bucket, against only 24 genuine items (12 `DUPLICATE`, 9
`FAILED_IMPORT`, 3 `STRANDED_CREATED`) — burying the real count under one
ten times too large, which is exactly the kind of inflated headline that
makes a reader stop trusting the report, the same failure mode already
fixed twice before (the unmatched-id count, the per-account
`UNVERIFIED_SOURCE` flags).

Every OTHER note is either a lost invoice, a duplicate, a document stuck
partway through, or a row nobody has verified, and none of those may exit
0. A run that reported dozens of stranded and failed-import documents used
to exit 0, which is the report reading as clean while naming the exact
incident it was run to find.

## Gotchas

These are measured API behaviours the implementation works around. Each one
is the kind of thing that returns a plausible-looking wrong answer instead
of an error, so getting them wrong is silent, not loud.

- **`GET /annotations` ignores its own filters.** `created_at__gte`,
  `created_at__lte`, and `einvoice=true` are all silently accepted and
  silently ignored — the endpoint returns HTTP 200 with the full,
  unfiltered result set. Any date windowing has to go through
  `POST /annotations/search` instead, which does filter correctly. (This tool
  windows on `created_at` and filters on the filename prefix there; it never
  filters on the e-invoice flag, which a `failed_import` XML twin does not
  carry.) The `created_at`/`updated_at` half of this is a general platform
  behaviour, not specific to this tool — see `rossum-reference` → *`created_at`
  / `updated_at` do not filter — silently ignored* for the platform-wide
  measurement and the correct replacement.
- **`GET /documents` only matches an exact filename.** The
  `original_file_name__startswith` and `__contains` query variants are
  ignored; they silently return every document in the organization rather
  than erroring or narrowing the result. General platform behaviour, not
  specific to this tool — see `rossum-reference` → *`original_file_name`
  filtering only matches exact filenames* for the measured row count.
- **The e-invoice filename convention has a suffixed variant, and the
  pattern accepts it.** The naming convention itself — `einvoice<invoice
  id>.pdf`/`.xml`, the embedded id being the source network's own invoice
  id, and the measured `_<annotation id>` suffixed variant — is documented
  in `sfi-reference` → *E-Invoice Filename Convention*, since it's produced
  by the e-invoice importer extension, not by B2Brouter. What's specific to
  this tool: `EINVOICE_FILENAME_RE` in `rossum.py` accepts an optional
  `_<digits>` suffix for exactly that variant, and always takes the invoice
  id from the *first* number only. Without it, such a document was
  invisible to the index — and if the matching invoice showed up in the
  B2Brouter listing, the tool reported `MISSING_IN_ROSSUM` for a document
  that was sitting right there. The suffix acceptance is deliberately
  narrow (still strictly `\d+` on both sides, not a loosened prefix match):
  don't tighten it back without re-confirming the variant no longer occurs.
- **Search pagination is a cursor, not a page number.** Passing `?page=N`
  to `/annotations/search` is ignored and always returns the first page
  again. The only way to advance is to follow the `next` link in the
  response and re-POST the same query body against it. General platform
  behaviour — see `rossum-reference` → *Annotation search — measured
  gotchas*.
- **`/annotations/search` rejects a single-clause query with HTTP 400.**
  Measured directly: `{"query": {"field.document_id.string": {"$eq":
  "..."}}}` on its own always 400s — the same trap as `GET /documents`'
  filename-only query above, just louder. The working form pairs the
  content clause with a second one under `$and` (this tool's
  `has_surviving_original` in `rossum.py` pairs it with an explicit
  `status.$in` naming the full status list, `ALL_STATUSES`). This one is
  worth flagging twice: on a live run, the 400 was swallowed by this tool's
  OWN per-row error handling (a failed verification search is deliberately
  treated as "not verified" rather than aborting the run — see "`DELETED`
  and `DELETED_AS_DUPLICATE`" above) into an unusually large "not verified"
  count, with nothing else in the summary looking wrong. Zero of 222 rows
  verified in that run before this was caught. A defensive "don't abort on
  one bad request" design and a silently-wrong request shape combine into
  exactly the kind of failure that looks like nothing happened at all —
  which is why the fix for this one also added a test that asserts the
  emitted query body's shape, not just that a search was issued. The 400
  itself is a general platform behaviour — see `rossum-reference` →
  *Annotation search — measured gotchas* (this tool's own `$and` wrapping
  already satisfies it, so it is unaffected).
- **The annotation search's status coverage cuts both ways, so the index is
  built from two queries.** With an explicit `status.$in` clause the search
  returns rows the default omits (measured: 17,897 rows with the clause,
  17,891 without). But that clause can only name statuses this tool already
  models, so an annotation in any other status — an approval-workflow
  state, anything added later — would never be returned at all, and the
  `UNKNOWN_STATUS` guard would never see it: the invoice would
  read as having no annotation, or as plain `ok` beside a healthy sibling.
  The platform's status enum cannot be authoritatively listed from outside
  (even first-party tooling omits statuses that occur live), so the index
  issues both queries — one with the clause, one with no status clause at
  all — and unions them by annotation id. General platform behaviour — see
  `rossum-reference` → *Annotation search — measured gotchas*.
- **The search index is eventually consistent.** An annotation that was
  just imported can take a few seconds to appear in search results. The
  grace window (`--grace-minutes`) exists to absorb that lag instead of
  reporting a fresh arrival as missing. General platform behaviour — see
  `rossum-reference` → *Annotation search — measured gotchas*.
- **The Rossum-side index is deliberately built wider than the reporting
  window, and that widening must not be mistaken for the window itself.**
  `einvoice_index()` subtracts a fixed 24-hour lookback (`INDEX_LOOKBACK` in
  `rossum.py`) from `since` before querying, so an invoice that arrived
  right at the window's edge still finds an annotation created slightly
  later — the two systems' clocks are independent, and this is correct and
  must stay. But it means the index also returns ids whose *only*
  annotation predates `since`: pure lookback-tail context, not something
  that arrived now. Any consumer that treats every id in the index as if it
  belonged to the window over-counts — measured on a live run over a short
  window, only a small minority of the ids the index returned had an
  annotation actually inside the window; the rest were lookback-tail only.
  Both the unmatched-id count and the per-account attribution check (see
  Guarantees above) scope to the window first for exactly this reason, and
  report the excluded tail count separately rather than dropping it
  silently.
- **Channels are discovered by account presence, not by the hook's
  extension URL.** A channel is any hook whose settings carry a
  B2Brouter account id — deliberately not a match on the hook's
  `config.url`. That URL is an implementation detail of the extension and
  has already changed between releases; matching it would fail silently by
  discovering zero channels, which reads as "nothing to reconcile" rather
  than as an error.
- **The invoice listing endpoint is `received.json`, not `invoices.json`.**
  Received invoices are listed via `/projects/{account}/received.json`.
  `invoices.json` is not a working alias for this data — measured directly
  against the live API, it returns `total_count: 0` for every account and
  every parameter combination tried.
- **Omitting `ack` (or sending `ack=false`) silently returns only the
  importer's pending queue, which looks like an empty account.** The
  `received.json` endpoint defaults to unacknowledged-only results when
  `ack` is not sent — a handful of rows out of what can be thousands — and
  `ack=false` returns that same small pending queue. This tool always sends
  `ack=true` to see the full index; see Guarantees above for the evidence
  that `ack=true` does not mutate anything.
- **`received.json` returns `ack_at: null` for every invoice, whether or not
  it was actually acknowledged.** Measured directly: the same invoice came
  back `ack_at: null` from the LIST endpoint and a real timestamp
  (`"2026-08-17T16:30:55Z"`, arrived at `16:25:26` — acknowledged 5m29s
  later) from the per-invoice DETAIL endpoint. Reading the listing's
  `ack_at` at face value would silently read "collected then lost" as
  "never handed over" — exactly backwards, and on a live run it actively
  misled the reader: nine failed invoices were reported as never
  acknowledged when all nine had, in fact, been acknowledged 8–9 days
  earlier. A real acknowledgement timestamp can only ever be read from the
  per-invoice detail endpoint (`get_invoice` in `b2brouter.py`); see the
  `acked_at` column section above for how this tool now uses it, and why
  only for exception rows.
- **`received.json`'s `date_from`/`date_to` filters key on issue date, not
  arrival.** A single-day server-side date query has been observed
  returning rows whose `created_at` (arrival) was up to several days later.
  Since reconciliation windows on arrival and issue-to-arrival skew is
  unbounded in principle, this tool never sends those filters: it pages
  every account's full index and applies the `[since, until]` window
  client-side against `created_at` instead.
- **A server-declared invoice count is never trusted to end pagination.**
  `total_count` is never a stop signal, whatever it says — only a short page
  ends the loop. A server that under-declares (says 2 while holding more)
  would otherwise truncate the authoritative (source) side of the
  reconciliation on a full page. The declared count is still used in the
  other direction: if it is materially *greater* than the rows actually
  walked, the run fails loudly rather than reporting the shortfall as the
  whole account.
- **Page fullness is measured against the limit the server echoes back.**
  Legacy list endpoints commonly clamp the requested page size. Comparing
  the row count to the size we *asked* for would read every clamped-but-full
  page as the final short page and stop at the first one. If the echoed
  `limit` is smaller than the requested page size, the request was not
  honoured and the run fails for that account: an account reported
  `UNVERIFIED_SOURCE` is far better than one reported complete from
  truncated data.

## TLS interception

On a network that intercepts and re-signs TLS traffic (a corporate proxy or
inspection appliance), Python's HTTPS requests can fail with a certificate
verification error even when `curl` or a browser on the same machine
connects fine — Python uses its own bundled CA trust store, not the
operating system's, so an OS-level trusted root does not automatically
extend to it.

If you hit this, set the `SSL_CERT_FILE` environment variable to a CA
bundle that includes the intercepting proxy's certificate, or run the tool
from a network path that is not intercepted. Note that installing the
proxy's root certificate into your OS trust store is not always enough by
itself: some inspection appliances present a certificate chain that
OpenSSL rejects even when the root is otherwise trusted, so the bundle
pointed to by `SSL_CERT_FILE` may need to include the intermediate the
proxy actually presents.

This tool has no flag to skip certificate verification, and will not gain
one. It reads invoice and supplier data across a network boundary, and a
"skip verification" switch in a released tool eventually gets used in
anger during an unrelated outage — at which point it silently removes the
only protection against a genuine man-in-the-middle. Fix the trust store
instead of bypassing it. `--relax-x509-strict`, below, is not that switch —
it does not touch verification at all, only one specific RFC-permitted
strictness check.

### `--relax-x509-strict`

`SSL_CERT_FILE` fixes the common case above: an intercepting CA that Python
does not yet trust. A second, narrower failure can survive adding that CA
to the trust bundle. Some corporate TLS-inspecting proxies re-sign with a
CA certificate that has Basic Constraints `CA:TRUE` and **no Key Usage
extension**. RFC 5280 permits that — an absent Key Usage means
unrestricted, not disallowed — but Python 3.13+ enables OpenSSL's
`VERIFY_X509_STRICT` by default, and OpenSSL 3.6 in strict mode rejects
that RFC-legal CA outright, with an error that specifically mentions key
usage. The same host and the same bundle succeed against `curl` and
against a LibreSSL-linked Python, neither of which enforces this check —
it is a Python 3.13+/OpenSSL 3.6 strictness default, not a real trust
problem.

`--relax-x509-strict` clears **only** `ssl.VERIFY_X509_STRICT`, reverting
that one check to the Python ≤3.12 default. Nothing else changes: chain
verification (`verify_mode`) stays `CERT_REQUIRED` and hostname checking
(`check_hostname`) stays enabled, exactly as `ssl.create_default_context()`
already sets them — a certificate must still chain to a trusted root and
match the hostname it is presented for. As evidence that trust is still
enforced, not weakened: run the same relaxed context against a CA bundle
that does **not** include the corporate proxy's CA, and the connection is
still correctly refused. The flag only changes the outcome for a CA your
bundle already trusts.

The honest security note is this: **trusting a corporate interception CA
at all is what allows that proxy to present a valid certificate for any
hostname it intercepts** — that is a property of adding the CA to your
trust store via `SSL_CERT_FILE`, decided by your organization, not
something this flag adds to or takes away from. `--relax-x509-strict`
changes nothing about that decision; it only lets a CA your organization
has already decided to trust pass one RFC-permitted certificate shape that
Python's newest strict default happens to reject.

The flag is opt-in and off by default, applies to both the Rossum and the
B2Brouter connection (either one can sit behind the same proxy), and is
never an environment variable — it stays an explicit, visible part of the
command line you run.
