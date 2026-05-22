---
name: txscript-reference
description: Rossum field-expression reference for TxScript serverless hooks, native Rossum Rule entities, and schema-field formulas. Covers (a) TxScript Python 3.12 API, (b) Rule trigger_condition language and Rule.actions shape including the FIRE-vs-PASS polarity convention and defensive is_empty guards, and (c) schema-field formula expressions (default_to, is_set, is_empty, line-item .all_values, helpers, multi-line formulas, the "absorb" refactoring pattern). Use when writing or debugging any Rossum field-expression code — a hook .py file, a Rule trigger, or a schema formula.
user-invocable: false
---

# Rossum Field-Expression Reference: TxScript, Rules, Formulas

This skill is the practical reference for every Rossum surface that runs a Python-flavored expression on annotation data:

1. **TxScript serverless hooks** (`.py` files invoked by webhook events) — full Python 3.12, with the `TxScript` helper class.
2. **Native Rossum Rules** (`POST /v1/rules` — `trigger_condition` expression + `actions` array).
3. **Schema-field formulas** (`formulas/<field_id>.py` next to `schema.json`) — multi-line Python expressions where the last expression is the field's value.

All three share the same Python expression sublanguage (operators, comparisons, helpers like `default_to` / `is_empty`) but the *evaluation context* differs (event-driven hook vs. validation-time rule vs. derived-field formula). See [reference.md](reference.md) for the full reference; the rest of this SKILL.md indexes when to consult what.

**IMPORTANT — editing rule:**
- For hook code: always edit the `.py` file next to the hook JSON. Never edit the `code` field inside the hook's `.json`. `prd2` extracts hook code into `.py` files on pull and merges it back on push.
- For formulas: always edit `formulas/<field_id>.py` next to `schema.json`. Never edit the `formula` property inside `schema.json` directly. `prd2` handles the round-trip.
- For Rules: edit the rule JSON directly (`trigger_condition` is a string field, not extracted into a `.py`).

Use this knowledge when:
- Writing or debugging a TxScript serverless hook (the original audience)
- Authoring a native Rossum Rule (`trigger_condition` + `actions`)
- Authoring a schema-field formula (single-expression or multi-line)
- Looking up common schema field IDs and their conventions

Cross-references:
- `rossum-reference` covers the platform overall (the Business Rules Validation hook syntax cheat-sheet lives there).
- `prd-reference` covers the local file layout and `prd2` push/pull workflow.
