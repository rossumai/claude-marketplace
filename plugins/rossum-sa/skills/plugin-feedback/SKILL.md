---
name: plugin-feedback
description: Report friction with the rossum-sa plugin as a sanitized, human-confirmed GitHub issue (or via the fallback channel). Use when the SA hits a capability gap, a tool bug, or a wrong/ missing Rossum fact, or when the friction detector offers to log it. Triggers on "log plugin feedback", "report this bug", "the plugin got this wrong", "/rossum-sa:plugin-feedback".
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
section for knowledge-gap). On a confident match, propose commenting + 👍
instead of a new issue.

## 4. File it — pick the transport, confirm first
Read `feedback-config.json`. Then, after the SA confirms the draft:
- **`gh` available** (`gh auth status` succeeds and `gh repo view <target_repo>`
  works) → ensure the three labels exist idempotently, then `gh issue create`
  (or comment + `gh api` reaction on dedup). Print the issue URL.
- **No `gh`, `form_url` set** → POST the sanitized payload to the form (Plan 1).
- **No `gh`, no form** → open a prefilled `mailto:` (if `mailto` set) or copy the
  sanitized draft to the clipboard and tell the SA where to paste it.
Never file into the repo the SA is `cd`'d into — always `target_repo`.

## Hard rules
- Human-in-the-loop always: classify → draft → SA reviews → only then file.
- Metadata-first sanitization is mandatory; the SA is the final gate.
- No auto-filing, no auto-PR, no committed reference-pack edits.
