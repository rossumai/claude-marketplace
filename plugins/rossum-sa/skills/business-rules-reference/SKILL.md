---
name: business-rules-reference
description: 'Rossum business-rules and validation reference — the two ways to validate extracted data and block automation: native Rossum Rules (the /v1/rules entity: trigger_condition + actions[], FIRE-vs-PASS polarity, lifecycle, rule events with the action×event matrix for send_email, change_queue, delete, labels and exclude_from_training) and the legacy Business Rules Validation Store extension (checks[] config, error/warning/info results, its own curly-brace expression engine). Trigger-condition syntax lives in txscript-reference. Use whenever the user works on business rules, validation rules, blocking automation on invalid data, rule actions or events (email on export success/failure, quarantine failed imports), the checks[] config, or porting legacy checks to native Rules — even when they just say "add a rule" or "validate this field".'
user-invocable: false
---

# Rossum Business Rules & Validation Reference

Rossum validates extracted data and blocks automation in two ways. This pack covers the **feature** end-to-end for both:

1. **Native Rossum Rules** (`POST /v1/rules`) — the modern, platform-native path: a boolean `trigger_condition` plus an `actions[]` array. Each action names the `event` it runs on: `validation` for messages, blockers and show/hide, or a lifecycle event (import, confirm, export, and the four import/export success/failure outcome events) for `send_email`, `change_queue`, `change_status`, labels and `exclude_from_training`. The condition is **TxScript** (`field.X` access) — for that expression syntax see the `txscript-reference` skill. This pack owns the entity, the JSON shape, `actions[]` types, FIRE-vs-PASS polarity, and action pairings.
2. **Legacy Business Rules Validation extension** — a Store hook configured with a `checks[]` array, running at the end of the extension chain. It has its **own curly-brace expression engine** (`has_value({field})`, `{value, default=0}`, `similarity()`), documented in full here because nothing else uses it.

See [reference.md](reference.md) for the full reference. Consult it when authoring or debugging either kind of rule, choosing between them, or reading a `checks[]` config.

It also covers **porting expressions between engines** — legacy `checks[]` to native Rules, legacy calculation hooks to formulas — as a defect taxonomy plus a verification method built on real field values and predicted message counts rather than on translating expression text.

Cross-references:

- `txscript-reference` — the TxScript expression language for native Rule `trigger_condition`s (and hooks/formulas). Native Rules use `field.X`; the BRV extension uses a different `{field}`-brace engine documented here.
- `rossum-reference` — the `/v1/rules` and `/v1/triggers` endpoint basics and the platform overview.
- `upgrade` — the skill that drives deprecated-extension migrations; the porting method here is the value-level verification step for the rules and formulas it produces.
- `test-behavioral-equivalence` — corpus-wide before/after replay across two environments, the heavier companion to the in-place count check.
