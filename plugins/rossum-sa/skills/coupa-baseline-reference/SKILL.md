---
name: coupa-baseline-reference
description: "Coupa Integration Baseline (CIB) — Complete Reference & Working Guide. Pre-configured Rossum-to-Coupa AP invoice processing solution, covering both baselines in production — CIB 1.x (one workspace, two taxation queues) and CIB 2.0 (six workspaces, seven queues — the two baseline queues, an E-invoicing Inbox and the BE/PL/FR/DE country queues). Covers schema structure, data imports, MDH matching, native business rules, the export pipeline, e-invoice ingestion and routing, Coupa status sync, and how to scope a 2.0 deployment down by removing unused country queues or e-invoicing entirely. Use when building, adjusting, scoping, troubleshooting, or explaining any aspect of a Coupa integration built on the CIB baseline."
user-invocable: false
---

# Coupa Integration Baseline (CIB) Reference

This skill contains the complete knowledge of the Coupa Integration Baseline (CIB) — the pre-configured Rossum solution for Coupa AP invoice processing. For complete details, see [reference.md](reference.md).

**Two baselines are in production and an organisation runs one or the other** — CIB 2.0 is a fresh install only, with no in-place upgrade path from 1.x. **Establish which one you are looking at before answering anything about a live org**; section 0 of the reference gives the detection table. Content tagged `[1.x]` or `[2.0]` applies to that baseline only; untagged content — the schema, the MDH cascades, the formula logic — applies to both.

When working on a CIB implementation, always reference the actual project files in the working directory. The CIB codebase is the source of truth — this skill documents the standard baseline patterns. Customer implementations may have customizations on top.

Use this knowledge when:
- Building a new Coupa integration from the CIB baseline
- Scoping a CIB 2.0 deployment to what the customer actually uses — removing unused country queues, or e-invoicing altogether (section 2)
- Adjusting or extending an existing CIB implementation
- Troubleshooting matching, export, e-invoice routing, or validation issues
- Explaining CIB architecture, data flow, or field logic
- Reviewing CIB configuration for correctness
