# Verification Rules — Read Before Acting

These rules apply to **every** rossum-sa skill. They exist because hallucinated endpoints, field names, and behaviors have repeatedly burned real iterations.

## The rule

**Do not make assumptions. All technical decisions must be grounded in verified facts, and when uncertain you must ask before acting.**

This applies especially to:

- **API endpoints** — paths, methods, query parameters, response shapes
- **Field names and types** — on schemas, hooks, workflow steps, MDH datasets, anything returned by the API
- **Hook event names** — `annotation_status.changed` vs `annotation_content.user_update` etc.
- **Operation payloads** — `operations[]` shapes for line-item add/replace/remove
- **prd2 / CLI flags** — don't invent flags from "common sense"
- **MCP tools** — only call tools that you can see in the current tool list; do not assume a `rossum_*` wrapper exists for an endpoint just because the endpoint exists

## How to verify

In priority order:

1. **Check the official Rossum API docs first:** <https://rossum.app/api/docs/openapi/guides/getting-started/#introduction>. Treat this as the source of truth for endpoints, methods, request bodies, and response fields. If a section in this skill conflicts with the docs, the docs win — flag the skill for correction.
2. **Make a read-only API probe** when allowed (`GET /endpoint`, `OPTIONS /endpoint`). One real response beats five paragraphs of speculation.
3. **Inspect a real object** of the same kind via `prd2 pull` or `rossum_get_*` MCP tools before writing code that targets it.
4. **Ask the user** when the docs are silent, the probe is ambiguous, or you would otherwise be guessing about something that affects production state. A clarifying question costs one turn; a wrong write costs much more.

## Config presence ≠ live behavior

A field, dataset, or selector appearing in a hook's `settings` does **not** make it a live dependency. Before claiming "field X is used by hook Y" — especially when assessing the impact of removing a field, mapping, or dataset — verify all three:

1. The hook has `active: true`.
2. The queue in question is listed in the hook's `queues`.
3. The reference sits in configuration that actually executes — not in `test.savedInput` or other saved debug payloads embedded in the hook JSON.

Inactive hooks routinely linger in real implementations after being superseded (e.g., a vendor-matching MDH hook replaced by PO-based matching but never deleted). Reporting their dependencies as live produces false warnings that get relayed to customers. If the config is real but dormant, say so explicitly ("referenced only by an inactive hook") instead of presenting it as current behavior.

## What this rule forbids

- Writing code that calls an endpoint you have not seen succeed in either the official docs or a live probe.
- Documenting field names that you have not seen in a real response or in the OpenAPI spec.
- Extrapolating "GET works → POST/PATCH must work the same way" — many Rossum endpoints are read-only.
- Reasoning from analogous SaaS APIs (Stripe, GitHub, etc.) — Rossum has its own conventions.
- Silent fallbacks: if a tool you expected to exist is absent, say so out loud rather than open-coding `curl` around it.

## When you must guess

If — after the verification steps above — you still must produce a best guess (e.g. user is in a hurry, docs are partial), explicitly label it:

> *Unverified — best guess based on X. Please confirm with the API docs / a probe before relying on this in production.*

That label is the contract: the user knows the claim is provisional and can challenge it without you having to walk back a confident-sounding statement later.
