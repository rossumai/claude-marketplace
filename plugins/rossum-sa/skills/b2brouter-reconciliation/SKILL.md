---
name: b2brouter-reconciliation
description: Use when checking whether e-invoices received on the B2Brouter network all landed in Rossum — "B2Brouter reconciliation", "did we lose any e-invoices", "reconcile e-invoices against Rossum", "are all invoices from the e-invoicing network in Rossum", "check for missing e-invoices", "invoice arrived at the network but not in Rossum".
argument-hint: [--ui-host <org>.rossum.app] [--show-discovery|--check-coverage]
allowed-tools: Read, Grep, Glob, Bash
---

# B2Brouter Reconciliation

## Overview

Drives `python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py`
to prove whether every invoice B2Brouter handed to an importer hook actually
landed in Rossum, exactly once. The CLI is deterministic; this skill supplies
the judgement it cannot encode — what the discovered scope means, when the
credentials are too weak to trust the report, and what an operator must never
do with the result. Full note taxonomy, all flags, and the CSV column list
live in `reference.md` next to this file — read that alongside this skill,
don't expect this document to restate it.

**Never guess the target or hunt for credentials.** Always ask the operator
for the environment and token; never search the filesystem for them.

## Credentials

For **each B2Brouter account group**, create a restricted API key with
**accounts-read and invoices-read only** — nothing else. B2Brouter keys are
scoped per account group, not per organization, so a real organization
normally supplies several keys, one per group. Each key is passed as its own
`B2B_API_KEY_<LABEL>` environment variable (`B2B_API_KEY` itself, plus any
number of `B2B_API_KEY_*`, are all picked up) — one key cannot see another
group's accounts, so a single key is never enough for an organization with
more than one group.

## Walkthrough

### 1. Establish the target

Ask the operator for the Rossum API base URL (`--base-url`, defaults to
`https://elis.rossum.ai`) and the UI host used for links (`--ui-host`,
required — Rossum's organization endpoint exposes no UI URL, so this can't be
discovered). Ask for `ROSSUM_TOKEN` and at least one `B2B_API_KEY` /
`B2B_API_KEY_<LABEL>` to be exported as environment variables (see
Credentials above).

The tool probes each key on its own: a key that fails its probe (revoked,
stale, typo'd, wrong group) is reported — by variable name, never its
value — and skipped, while the other keys still cover whatever they can; it
does not abort the whole run by itself. If you see that warning, tell the
operator which variable to check rather than treating it as a reason the run
failed outright.

If a Rossum token is already active in the session, call `rossum_whoami`
first and state the organization back to the operator before doing anything
else — don't assume the active session is the org they mean.

If a run fails with a certificate/SSL error, the first remedy is a CA
bundle: set `SSL_CERT_FILE` to a bundle including the intercepting proxy's
CA (see `reference.md`'s TLS interception section). If the error specifically
mentions **key usage**, that CA is already trusted but omits the Key Usage
extension Python's strict X.509 checks now require by default — the
remedy there is `--relax-x509-strict`, not a bigger bundle. Where IT can
offer a proxy exception for this tool's traffic instead, prefer that: it
needs no relaxation on this end at all.

### 2. Discover the scope first, always

Run `--show-discovery` before any full run, every time:

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/b2brouter-reconciliation/recon.py \
  --ui-host <org>.rossum.app --show-discovery
```

Read every line back to the operator: hook id, active/**INACTIVE**, name,
queues, account count, B2Brouter host. Two things to say out loud, not just
note silently:

- An **INACTIVE** hook means those invoices are not being imported at all
  right now — that's a finding in itself, independent of anything the
  reconciliation later reports.
- The account count is the reconciliation's **blast radius**. If it looks
  smaller than the operator expects, stop here and go investigate the hook
  configuration. Do not run a full report on an under-scoped discovery — it
  will look complete while quietly covering less than it claims.

### 3. Prove credentials cover that scope — before trusting any numbers

Run `--check-coverage` right after `--show-discovery`, before any full run:
"what is in scope?" then "can I actually see all of it?" then, only after
both check out, the run itself. It needs the same credentials as a real run,
probes each supplied key's visible accounts per host, and fetches zero
invoices — cheap to run every time. It prints each channel's covered/total
count, and lists the uncovered account ids by name when any exist, then
exits 0 if everything is covered or 1 otherwise. A key that failed its own
probe is named explicitly and **forces exit 1 by itself**, even when every
account happens to be covered by one of the other keys — the operator ran
this mode to ask "are my credentials right?", and one of them is not, so
that can't be waved off just because the accounts are covered another way.

If any account is uncovered, the run is not a reconciliation — it's a
credentials diagnostic. Do **not** proceed to a full run. Tell the operator
exactly what to request: a B2Brouter key with **accounts read + invoices
read** that can *enumerate* invoices for every listed account, not merely
fetch one by id — those are different capabilities, and a key can have the
second without the first. Accounts often span more than one B2Brouter group;
that needs one key per group, supplied as separate `B2B_API_KEY_<LABEL>`
variables.

### 4. Handle "reads one invoice but enumerates none"

If a channel's source enumerates **zero** invoices while Rossum already
holds e-invoices for that channel's queues, the tool cannot tell whether
that's real (nothing arrived) or the key is broken — so it declares the
whole channel unverified rather than guessing. It cannot produce per-invoice
rows for those accounts, precisely because it could not enumerate the
invoices behind them, so instead it writes **one `UNVERIFIED_SOURCE` row per
account**, naming the account and the reason.

The same check also applies **one account at a time**, because a single
listed invoice anywhere in the channel would otherwise disarm the
channel-wide version entirely — and it is **precise**, not sibling-based.
The Rossum-side index is deliberately built wider than the reporting window
(so an invoice at the window's edge still finds its annotation), so before
anything is counted, ids whose only annotation predates the window are set
aside as a separate, disclosed count — not treated as missing. Only among
the remaining, in-window ids does the tool count how many no listed invoice
matched; each of those is then looked up in B2Brouter **by id** to learn
which account actually owns it, and *only that account* gets its own
`UNVERIFIED_SOURCE` row. An account that simply listed nothing this window,
with no unmatched id of its own, is **never** flagged for a sibling's
shortfall — some accounts legitimately receive only a handful of invoices a
year, and an earlier, sibling-based version of this check flagged quiet
accounts like that on its first live run. Each channel's summary prints both
counts: how many in-window ids matched no listed invoice, and how many ids
were outside the window and excluded — a few unmatched is normal at window
edges, a large number means the source listing is not returning everything
those queues received — say those numbers back to the operator rather than
skipping past them.

This is the tool's central promise, worth stating to the operator plainly:
those rows exist so the CSV discloses its own incompleteness. Someone who
only ever sees the CSV — emailed, filed, opened in a spreadsheet — never
sees stderr or the exit code, so the file has to say "this account could not
be listed at all" on its own. An `UNVERIFIED_SOURCE` row means that
account's true invoice state is **unknown**, not that it's fine.

Discriminate before concluding anything: read one *known* invoice by id,
directly, with the same key. If the single-id read works while the list call
returns nothing, the key lacks list/index capability — the endpoint isn't
broken, the key is narrower than it looks. That's a credentials
conversation, not a code change.

### 5. Agree the window

Default is the last 30 days. For an incident, use the incident's own window
(`--from`/`--to`, ISO date or datetime). For a periodic control, use month
boundaries. Don't default to "last 30 days" silently for either of those —
ask which applies.

### 6. Run it, then read the result back in plain terms

Run the full window once coverage is proven. Report, in plain language: how
many invoices total, how many `ok` (plus `ok +xml_twin`), and **every
exception row named individually by its `einvoice_id`** — open the CSV (use
`--only-exceptions` to make that easy) and list them. Never report an
exception *count* without the ids next to it: each row is a real invoice
somebody is waiting for, not a statistic.

### 7. Triage by note

Use the note-meaning-action table in `reference.md` — don't re-derive it
here. Five rules from that table are easy to get backwards and expensive
when gotten backwards, so state them explicitly every time recovery comes
up:

- **On a `MISSING_IN_ROSSUM` row, read `acked_at` correctly before saying
  which side is at fault.** A timestamp means B2Brouter's own job for that
  invoice is done — it was collected and then lost on our side, and the
  source will **not** re-deliver it; escalate with the einvoice id. A
  genuinely empty value means the source never handed it over at all — that
  fault is theirs, and it may still arrive. `not checked` (the value most
  rows carry — the detail lookup only runs for exception rows, capped per
  channel) means neither has been established yet: never report a
  `not checked` row as "never acknowledged" or as anything else conclusive
  — narrow the window or re-run instead.
- **`DELETED` and `DELETED_AS_DUPLICATE` are not exceptions to chase —
  neither is an action item, by explicit product decision.** This tool does
  not reason about *why* an annotation was deleted, and neither should you:
  don't speculate about a "possible loss" or investigate the cause.
  `DELETED_AS_DUPLICATE` means a search actually confirmed a healthy
  annotation for that invoice number exists elsewhere — the invoice is
  accounted for. `DELETED` means either a search confirmed no surviving
  copy exists, OR nobody has checked yet (no invoice number to search by,
  the per-channel verification cap was reached, or the search itself
  failed) — the label alone does not tell those two apart; only the channel
  summary's verified/not-verified counts do. Either way, `DELETED` is not
  `MISSING_IN_ROSSUM` and must not be treated or reported as one.
- **Never re-drive a stranded or failed-import row without first checking
  whether a healthy annotation already exists for the same invoice id.**
  Re-driving one that already succeeded double-posts it downstream.
- **Never re-drive a `SPLIT_CONTAINER` row at all.** It is a container that
  was split into child annotations — the children are the real documents,
  and the invoice may have already arrived through them perfectly well.
  Inspect the children before touching the parent; re-driving the container
  risks double-posting them, exactly the fault this recovery discipline
  exists to prevent.
- **Recovery must be serialised** — one invoice at a time, verifying each
  before the next — because the stranding itself is caused by concurrent
  importer runs colliding. Recovering in parallel recreates the exact fault
  being fixed.

Recovery is a deliberate write operation and is **out of scope for this
skill**. Say so plainly; the operator authorises and performs it separately.

### 8. Close the loop

State: where the CSV landed (`--out`, default `b2brouter_reconciliation.csv`),
and what the exit code means — `0` means nothing missing, nothing unverified
and nothing needing action (every note is `ok`, `ok +xml_twin`, `PENDING`,
`DELETED`, or `DELETED_AS_DUPLICATE` — the last two because this tool does
not treat either as an action item, by explicit product decision, whether
or not the row's `DELETED` -> `DELETED_AS_DUPLICATE` upgrade was actually
verified); `1` means at least one row needs action (**any** other note,
including `STRANDED_CREATED`, `FAILED_IMPORT`, `SPLIT_CONTAINER`,
`DUPLICATE`, `MISSING_IN_ROSSUM`, `UNVERIFIED_SOURCE` and
`UNKNOWN_STATUS:*`) or a channel could not fully enumerate its accounts;
`2` means the run could not be performed — missing credentials, an invalid
window, or no channels discovered at all. Exit `2`
also covers credentials
that were *rejected*, not just absent — an expired or invalid token or key
(HTTP 401/403) exits `2` the same as a token that was never set. **If the run was incomplete (any `UNVERIFIED_SOURCE` row, any "no API key can see it"
line, exit 1) — or never ran at all (exit 2) — say explicitly that the
reconciliation has not been established, and name exactly what's needed to
finish it** (which account(s), which channel, what kind of key). An incomplete run is never
"all good" — don't let a written CSV and a clean-sounding summary line
substitute for actually checking exit status and stderr.

## Guarantees to state when asked

- The tool only reads. Its one `POST` is guarded to Rossum's annotation
  search endpoint and refuses every other path.
- The B2Brouter acknowledgement parameter (`ack`) is always sent as
  `ack=true` — required to see the full invoice index rather than just the
  importer's pending queue — and measured to be a read filter that does not
  change which invoices the importer collects. `ack=false` is never sent.
- A partial source is never reported clean: any uncovered or erroring
  account makes the whole run exit non-zero.

## Known limitations

- Verified against one production organization, one B2Brouter deployment and
  one account-group structure; discovery is designed to generalise but that
  is a design claim, not a measurement.
- `in_workflow` is a real Rossum annotation status that the classifier does
  not place in either of its two sets; such rows surface as `UNKNOWN_STATUS`
  rather than being miscounted, but they are not classified.
- In the CSV a `DELETED` row reads the same whether its verification search
  ran and found nothing or never ran; only the run summary distinguishes
  them.
- One code path — the fallback to the newer B2Brouter API response shape,
  where the account id sits under `account` rather than `project` — has
  never been exercised against a live response.

## Red flags — stop and correct course

| Temptation | Why it's wrong |
|---|---|
| "The CSV was written, exit code doesn't matter" | Exit 1/2 means the report is incomplete, the scope is wrong, or rows need action — a written file is not proof of a clean run. |
| "Nothing is missing, so it's a clean run" | Exit 0 requires nothing missing **and** nothing unverified **and** nothing needing action. Stranded, failed-import, duplicate and unknown-status rows all exit 1 — each is an invoice somebody is waiting for. |
| "It's just one stranded invoice, I'll re-drive it now" | Not without checking for a healthy sibling first — re-driving a succeeded invoice double-posts it. |
| "Recovering all of them in parallel is faster" | Parallel recovery is what caused the stranding; it must be serialised. |
| "The token's already active, I'll assume the org" | Confirm with `rossum_whoami` and state the org back before proceeding. |
| "I'll grep for an API key on disk to save the operator a step" | Never search the filesystem for credentials — always ask. |
