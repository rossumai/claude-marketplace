---
name: verify-change
description: Inner dev loop for Rossum hook iteration. Trigger one annotation through a deployed hook change, observe hook logs and changed field values, repeat. Use when actively editing a hook and you want a tight cycle. Triggers on "verify this change", "did my hook land", "trigger and check", "iterate on this hook", "re-run this annotation", "test this on one annotation". For pre-promote regression checks across a corpus, use `test-behavioral-equivalence` instead.
argument-hint: [annotation-id-or-url] [--env=<name>] [--trigger=toggle|confirm|patch] [--patch=<schema_id>=<value>] [--timeout=<s>]
allowed-tools: Read, Grep, Glob, Bash, Agent
context: fork
---

# Verify a Rossum Hook Change

You are a Rossum.ai Solution Architect iterating on a hook change. Each invocation triggers one annotation through the current deployed state and shows you what happened: which hooks fired, what they logged, what changed on the annotation. Single annotation, no corpus, no two-environment comparison — that's `test-behavioral-equivalence`'s job.

> Path or context: $ARGUMENTS

## Safety: Remote Write Confirmation

<HARD-GATE>
Three write operations exist in this skill. Each gets an explicit confirmation prompt — never batched.

1. **Status toggle** — `PATCH /annotations/<id>` to change status. Show current → target.
2. **Confirm** — `POST /annotations/<id>/confirm`. Enumerate every hook in the chain that will fire on `confirmed`. Call out export-shaped hooks (webhook to external endpoint, SFTP, email) by name. Refuse if the queue name does not match `test|sandbox|uat|dev` unless the user explicitly overrides.
3. **Optional `prd2 push`** — only if the user asked Claude to iterate solo. Show the file list before executing.

Read-only operations (`rossum_get_annotation`, `rossum_get_annotation_content`, `rossum_list_hook_logs`, `rossum_search_annotations`, `rossum_get_queue`, `rossum_list_hooks`) run without confirmation.
</HARD-GATE>

## How to Use This Skill

(workflow narrative — written in Task 2)

## Trigger Modes

(table + per-mode state-reset notes — written in Task 2)

## Output Format

(inline report template — written in Task 3)

## State Management

(cache file behavior — written in Task 3)

## Common Errors and Gotchas

(troubleshooting list — written in Task 4)

## When to Use Something Else

(cross-references — written in Task 4)
