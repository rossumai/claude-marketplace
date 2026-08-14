---
name: plugin-feedback
description: Report friction with the rossum-sa plugin as a sanitized, human-confirmed GitHub issue or anonymous maintainer-reviewed report. Use when the SA hits a capability gap, a tool bug, or a wrong/missing Rossum fact, or when the friction detector offers to log it. Triggers on "log plugin feedback", "report this bug", "the plugin got this wrong", "/rossum-sa:plugin-feedback".
argument-hint: [optional free-text description]
allowed-tools: Read, Bash, Grep, Glob
---

# Report plugin feedback (sanitized, human-confirmed)

You help a Rossum SA turn friction with THIS plugin into a sanitized,
deduplicated, human-confirmed GitHub artifact. You NEVER file anything without
the SA's explicit confirmation, and you NEVER include customer data.

## 0. Read the friction state (if any)
The detector hook may have written a state file for this session:
`~/.cache/rossum-sa/friction/<session_id>.json` (honor `$XDG_CACHE_HOME`).
Read it if present for deterministic signal metadata. If absent, proceed from
the SA's description and the current conversation.

## 1. Classify into exactly ONE route
- `tool-request` — a Rossum capability the generalist tools could not do cleanly
  (a write/action gap, or an awkward read). NOT clean `rossum_get` usage.
- `agent-bug` — a tool returned/did the wrong thing.
- `knowledge-gap` — a Rossum fact/behavior the plugin got wrong or didn't know.
Confirm the classification with the SA.

## 2. Build the sanitized draft — metadata ONLY
Use ONLY the fields in `reference/payload-contract.md`. Never include raw
payloads, field values, document content, annotation/org IDs, emails, or file
contents. Scrub the SA's free text for anything resembling customer data and
redact it. Show the rendered draft; the SA reviewing it is the FINAL gate.

## 3. Dedup
Search open issues in `target_repo` (from `feedback-config.json`) by signature
(`METHOD /endpoint` for tool-request; tool + error class for agent-bug; pack +
section for knowledge-gap). With `gh`, use `gh search issues`; without it,
`curl -sG --data-urlencode "q=repo:<target_repo> state:open <signature>"
https://api.github.com/search/issues`.
If the search fails, skip dedup and continue — never block filing on it. On a
confident match, propose commenting + 👍 instead of a new issue.

## 4. File it — the reporter chooses the channel
Read `feedback-config.json`. If `form_url` is empty (e.g. a fork, or the form
not yet provisioned), do not offer (b)/(c) — say why, and offer (a) + the
clipboard fallback only. Otherwise, after the SA confirms the draft, ask ONE
question:

> How do you want to send this?
> (a) GitHub issue — public and trackable
> (b) anonymous — a private queue the maintainers review manually
> (c) anonymous + contact email — same queue, but we can follow up

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
  filled ONLY with an address the SA types now, in answer to this question.
  NEVER reuse an email seen in the session, config, git, or environment. The
  email goes only to the private queue, never into any GitHub artifact.
- **Any failure** (non-200, no network) — copy the rendered draft to the
  clipboard (`pbcopy` / `xclip`) and print `form_view_url` (and `mailto` if set)
  so the SA can paste manually.

Never file into the repo the SA is `cd`'d into — always `target_repo`.

## Hard rules
- Human-in-the-loop always: classify → draft → SA reviews → only then file.
- Metadata-first sanitization is mandatory; the SA is the final gate.
- No auto-filing, no auto-PR, no committed reference-pack edits.
