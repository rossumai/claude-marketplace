---
name: rossum-reference
description: Complete Rossum.ai platform reference including API, architecture, concepts, schemas, extensions, and workflows. Use when answering questions about Rossum, writing Rossum integrations, configuring queues/schemas/hooks, or working with the Rossum API.
user-invocable: false
---

# Rossum.ai Platform Reference

This skill provides comprehensive reference documentation for the Rossum.ai document automation platform. For complete details, see [reference.md](reference.md).

Which Rossum API operations the `rossum-api` MCP server wraps is tracked in [api-coverage.md](api-coverage.md) (auto-generated). Read (GET) operations without a dedicated tool are still reachable through the generic `rossum_get` tool (shown as ✅ via rossum_get); ⬜ pending = an uncovered write with no MCP tool yet.

## Verification Rule (read first)

**Do not make assumptions. All technical decisions must be grounded in verified facts, and when uncertain you must ask before acting.** API endpoints, field names, hook events, and `operations` payloads must be verified against the official docs at <https://rossum.app/api/docs/openapi/guides/getting-started/#introduction> or a live read-only probe before being written into code or documentation. If you must guess, label the claim *Unverified — confirm before relying on this*. Full rationale in [`../__shared/verification-rules.md`](../__shared/verification-rules.md).

## Safety Rules

**Do not call write/mutating API tools without explicit user approval.** This applies to all `rossum_create_*`, `rossum_patch_*`, `rossum_delete_*` tools, all `data_storage` write tools (insert, update, delete, replace, bulk_write, drop), and `prd2 push`/`prd2 deploy` commands. Read-only tools (list, get, find, aggregate, whoami) are fine without confirmation. When in doubt, describe what you intend to do and ask first.

**Edit local `.py` files, not JSON.** When modifying hook code or formula logic in a prd project, only edit the `.py` file. Never edit the `code` field in hook JSON or the `formula` property in `schema.json` — `prd2 push` syncs `.py` files into JSON automatically. Do not use `rossum_patch_hook` or `rossum_patch_schema` to push code changes that should go through `prd2 push` instead.

## Customer-facing terminology

In SOWs, proposals, emails, and any other customer-facing material, write "Master Data Hub" in full — never "MDH" — and do not mention "Data Storage" (it is implementation; the product is Master Data Hub). Reference content and internal developer guidance can use "MDH" freely.

## When to use

Use this knowledge when:
- Answering questions about Rossum concepts, architecture, or features
- Writing code that integrates with the Rossum API
- Configuring schemas, queues, hooks, or connectors
- Debugging Rossum extension or webhook issues
- Explaining annotation lifecycles or document processing flows
