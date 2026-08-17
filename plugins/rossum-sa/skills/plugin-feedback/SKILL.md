---
name: plugin-feedback
description: Report friction with the rossum-sa plugin as a sanitized, human-confirmed GitHub issue or anonymous maintainer-reviewed report. Use when the SA hits a capability gap, a tool bug, or a wrong/ missing Rossum fact, or when the friction detector offers to log it. Triggers on "log plugin feedback", "report this bug", "the plugin got this wrong", "/rossum-sa:plugin-feedback".
argument-hint: [optional free-text description]
allowed-tools: Read, Bash, Grep, Glob
---

# Report plugin feedback (sanitized, ONE confirmation stop)

You help a Rossum SA turn friction with THIS plugin into a sanitized,
deduplicated report — fast. The whole flow costs the SA ONE reply: they see
the draft and pick the channel in the same message. You NEVER send anything
before that reply, and you NEVER include customer data.

## 0. Read the friction state (if any)
The detector hook may have written a state file for this session:
`~/.cache/rossum-sa/friction/<session_id>.json` (honor `$XDG_CACHE_HOME`).
Read it if present for deterministic signal metadata. If absent, proceed from
the SA's description and the current conversation. If you were started from
the detector's offer, the SA's "yes" only starts this flow — it is NOT
approval to send.

## 1. Classify into exactly ONE route — do not ask
- `tool-request` — a Rossum capability the generalist tools could not do cleanly
  (a write/action gap, or an awkward read). NOT clean `rossum_get` usage.
- `agent-bug` — a tool returned/did the wrong thing.
- `knowledge-gap` — a Rossum fact/behavior the plugin got wrong or didn't know.
Pick the best fit yourself; the SA corrects it at the single stop below.

## 2. Build the sanitized draft — metadata ONLY
Use ONLY the fields in `reference/payload-contract.md`. Never include raw
payloads, field values, document content, annotation/org IDs, emails, or file
contents. Scrub the SA's free text for anything resembling customer data and
redact it.

## 3. Dedup — silently, before the stop
Search open issues in `target_repo` (from `feedback-config.json`) by signature
(`METHOD /endpoint` for tool-request; tool + error class for agent-bug; pack +
section for knowledge-gap). With `gh`, use `gh search issues`; without it,
`curl -sG --data-urlencode "q=repo:<target_repo> state:open <signature>" https://api.github.com/search/issues`.
If the search fails, skip dedup and continue — never block on it. On a
confident match, plan to propose commenting + 👍 instead of a new issue.

## 4. ONE stop: draft + channel in a single message, then send
Read `feedback-config.json`. If `form_url` is empty (e.g. a fork, or the form
not yet provisioned), do not offer (b)/(c) — say why, and offer (a) + the
clipboard fallback only. Compose ONE message containing:
1. the route you picked (one line, correctable),
2. the FULL rendered draft — the SA seeing it is the privacy gate,
3. the dedup note, if any ("matches #N — I'd comment + 👍 instead"),
4. the channel question:

> How do you want to send this?
> (a) Public GitHub issue — anyone can see it, and you can follow progress.
>     Needs a GitHub account.
> (b) Private anonymous message to the plugin team — nothing public, no
>     account needed.
> (c) Same as (b), plus your email so the team can reply to you.

Present the options in exactly these plain words — no transport mechanics
(curl, forms, entry IDs) unless the SA asks.

The SA's one reply is BOTH the draft approval and the channel choice ("a",
"b", or "c" plus their email; or a correction — apply it, re-show only what
changed, and ask again). Then send immediately, with no further questions:

- **(a) GitHub** — if `gh auth status` and `gh repo view <target_repo>` succeed:
  ensure the three labels exist idempotently (skip on a permission error —
  external reporters cannot create labels; file anyway), then `gh issue create`
  (or comment + `gh api` reaction on a dedup match) and print the issue URL.
  Without `gh`: build a prefilled new-issue URL —
  `https://github.com/<target_repo>/issues/new?template=<route>.yml&labels=<route>&title=<signature>`
  plus one query param per template field id — folding `signal`,
  `corroborators`, and `counts` into the `description` value, since the
  templates have no fields for them — values URL-encoded
  (`python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "<value>"`).
  Print the URL for the SA to open, review once more on GitHub, and submit.
- **(b) Anonymous** — POST the draft to the Google Form: `curl -sS -o /dev/null
  -w '%{http_code}' <form_url>` with `--data-urlencode` per `form_fields` entry —
  `route` gets the route, `payload` gets the ENTIRE sanitized draft as one JSON
  string. Expect `200`; a missing required field returns `400`. Tell the SA it
  lands in a private, manually reviewed queue — not a public issue.
- **(c) Anonymous + email** — same POST as (b), plus the `contact_email` entry
  filled ONLY with an address the SA typed themselves in their reply (if they
  chose (c) without one, ask for it — the only extra stop). NEVER reuse an email
  seen in the session, config, git, or environment. The email goes only to the
  private queue, never into any GitHub artifact.
- **Any failure** (non-200, no network) — copy the rendered draft to the
  clipboard (`pbcopy` / `xclip`) and print `form_view_url` (and `mailto` if set)
  so the SA can paste manually.

Never file into the repo the SA is `cd`'d into — always `target_repo`.

## Hard rules
- ONE confirmation stop, never zero: the SA sees the full sanitized draft and
  picks the channel in one reply; nothing is sent before that reply.
- Metadata-first sanitization is mandatory; the SA is the final gate.
- No auto-filing, no auto-PR, no committed reference-pack edits.
