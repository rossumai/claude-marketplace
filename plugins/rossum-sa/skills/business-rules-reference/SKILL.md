---
name: business-rules-reference
description: Rossum business-rules & validation reference — the two ways to validate extracted data and block automation: native Rossum Rules (the /v1/rules entity — trigger_condition + actions[], FIRE-vs-PASS polarity, lifecycle; the trigger_condition is written in TxScript, so for that syntax see txscript-reference) and the legacy Business Rules Validation Store extension (checks[] config, error/warning/info result types, automation blocking, queue scoping, one-rule-one-table limit, plus its own curly-brace expression engine — has_value({field}), aggregations, casting, similarity/substring/regexp — documented in full in this pack because it is specific to this extension). Use whenever the user works on business rules, validation rules, data validation, native Rules vs. the business-rules extension, blocking automation on invalid data, rule actions, the checks[] config, or gating on field values — even when they just say "add a rule" or "validate this field".
user-invocable: false
---

# Rossum Business Rules & Validation Reference

Rossum validates extracted data and blocks automation in two ways. This pack covers the **feature** end-to-end for both:

1. **Native Rossum Rules** (`POST /v1/rules`) — the modern, platform-native path: a boolean `trigger_condition` plus an `actions[]` array, evaluated at validation time. The condition is **TxScript** (`field.X` access) — for that expression syntax see the `txscript-reference` skill. This pack owns the entity, the JSON shape, `actions[]` types, FIRE-vs-PASS polarity, and action pairings.
2. **Legacy Business Rules Validation extension** — a Store hook configured with a `checks[]` array, running at the end of the extension chain. It has its **own curly-brace expression engine** (`has_value({field})`, `{value, default=0}`, `similarity()`), documented in full here because nothing else uses it.

See [reference.md](reference.md) for the full reference. Consult it when authoring or debugging either kind of rule, choosing between them, or reading a `checks[]` config.

Cross-references:

- `txscript-reference` — the TxScript expression language for native Rule `trigger_condition`s (and hooks/formulas). Native Rules use `field.X`; the BRV extension uses a different `{field}`-brace engine documented here.
- `rossum-reference` — the `/v1/rules` and `/v1/triggers` endpoint basics and the platform overview.
