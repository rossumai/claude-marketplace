---
name: mdh-reference
description: Rossum Master Data Hub (MDH) API reference and matching query design guide. Covers dataset CRUD, fuzzy search, hook configuration model (MatchConfig, mapping, result actions, query cascades), query design rules, score normalization, and real-world matching examples. Use when building or debugging MDH matching configurations, designing query cascades, or working with the MDH API.
user-invocable: false
---

# Master Data Hub (MDH) Reference

This skill provides comprehensive reference documentation for Rossum's Master Data Hub — the matching engine that connects extracted document data to master data records. It covers the MDH management API, the hook configuration model, and query design patterns.

**Reference files:**
- [mdh-api-reference.md](mdh-api-reference.md) — MDH API endpoints (dataset CRUD, fuzzy search, operation status, **Search Indexes V2**) and hook configuration schema (MatchConfig, DMDatasetSource, Mapping, ResultActions, query cascade model)
- [mdh-matching-queries.md](mdh-matching-queries.md) — Query design rules (DO/DON'T), score normalization patterns, unique-result filters, GL coding dropdown pre-selection, real-world examples (supplier matching, PO line items, delivery address resolution), and Atlas Search index recommendations

Use this knowledge when:
- Building MDH matching configurations (hook JSON with source, mapping, result_actions)
- Designing query cascades (exact → fuzzy → fallback)
- Using score normalization or `$setWindowFields` unique-result patterns
- Configuring memorization via `$unionWith` patterns
- Managing MDH datasets via the API (upload, replace, update, delete)
- Enabling fuzzy search on datasets
- Declaring or debugging Atlas Search indexes (Search Indexes V2 — a dataset subresource, not a Data Storage call)
- Debugging matching hook configurations

> **Mandatory pre-flight for any `$search` query.** Before shipping or debugging a `$search` stage, list the search indexes on the dataset (`data_storage_list_search_indexes`) and confirm the named index is **declared**, is `queryable: true`, and maps every field you reference. A misnamed index, or one missing a mapping, returns zero results silently with `status: completed` — the hardest MDH failure mode to diagnose. Full recipe in [mdh-matching-queries.md → Atlas Search Index Pre-flight](mdh-matching-queries.md#atlas-search-index-pre-flight-run-before-shipping-any-search-query).
>
> Declared indexes are **durable** (MDH reconciles the registry onto the collection), so do not write index-repair loops and do not treat a missing index as drift — it is a missing *declaration*. Manage them through the [Search Indexes V2 API](mdh-api-reference.md#endpoints-search-indexes-v2); **btree** indexes are a separate, non-self-healing concern.

When writing or debugging queries, if the dataset structure is not already known from context, try to discover it using the `rossum-api` MCP tools: `data_storage_list_collections` to find available collections and `data_storage_aggregate` with `[{"$sample": {"size": 1}}]` to retrieve a sample record. Fall back to asking the user only if the MCP tools are not available.
