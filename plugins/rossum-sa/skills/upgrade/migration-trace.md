# Migration-Trace Convention

A lightweight, grep-friendly way for the `upgrade` skill to leave breadcrumbs that the `test-behavioral-equivalence` skill (and humans doing triage) can follow.

## Why

When an upgrade replaces a hook-driven transformation with a formula, a rule, or a different hook, two things can happen later:

1. A value regression is detected in a test run and the triager needs to find the exact line of old logic the new artifact was supposed to replace.
2. An analyst months later wonders "what *used* to happen here?" and the old hook has been disabled but not fully explained.

A machine-readable trace inside the disabled artifact solves both. It lets `test-behavioral-equivalence` say "field X now differs in 4 annotations — it was migrated from `C07` on schema 1234567 (DE) (condition: `has_value({amount_paid})`)" instead of just "field X differs".

## The convention

### Storage

For deprecated hooks whose logic has been migrated away:

- Keep the hook's JSON in the repo with `"active": false`. **Do not delete.**
- Leave each row in its settings list (`settings.calculations`, `settings.mappings`, `settings.rules`, whatever the hook's own structure is) in place.
- Add a `"//"` sibling key to each row with the migration trace line.

`//` is already the de-facto JSON-comment idiom and both `prd2` and Rossum ignore it. The `test-behavioral-equivalence` skill picks it up with a simple recursive walk over every `//` key whose value matches the grammar below.

```json
{
  "settings": {
    "calculations": [
      {
        "//": "C07 -> F05 on schema 1234567 (DE). Cond: has_value({amount_paid})",
        "output": "amount_due",
        "value": "{amount_total} - {amount_paid}"
      }
    ]
  },
  "active": false
}
```

For migrated **formulas** and **rules** the breadcrumb goes in the opposite direction — a header comment pointing back at the legacy ID:

```python
# migration-trace: F05 <- C05,C06,C07,C08,C09 (Calculations hook 1000001)
# Outstanding amount: total minus paid, fallback to total when unpaid.
field.amount_total - field.amount_paid if field.amount_paid else field.amount_total
```

```json
{
  "name": "amount_due non-negative",
  "description": "migration-trace: R02 <- C11 (Business Rules Validation hook 1000003). Original blocker kept.",
  "condition": "{amount_due} < 0"
}
```

### Grammar

One line per trace. Two forms, both parseable by the same regex.

**Forward (from the deprecated artifact):**

```
<LEGACY_ID> -> <NEW_ID>[ (<new_target_name>)][ on schema <schema_id> (<scope_label>)][. Cond: <original_condition>][. <free-form note>]
```

**Backward (from the new artifact):**

```
migration-trace: <NEW_ID> <- <LEGACY_ID>[,<LEGACY_ID>...] (<source_artifact_name> <source_id>)[. <free-form note>]
```

### Legacy-ID prefix registry

The prefix encodes the *class* of thing being migrated. Keep this list short and extend only when a new extension type appears.

| Prefix | Source                          | Example                                           |
|--------|---------------------------------|---------------------------------------------------|
| `C`    | Calculations hook rows          | `C07 -> F05 on schema 1234567 (DE)`               |
| `D`    | Date Calculations hook rows     | `D03 -> F18 (item_date_prepaid_start_export)`    |
| `PM`   | Post-Match Calculations rows    | `PM31 -> F23. Migrated on all 40 schemas.`        |
| `CP`   | Copy & Paste hook rows          | `CP02 -> F12 (vendor_country)`                    |
| `FR`   | Find & Replace hook rows        | `FR05 -> F04 (currency). Cond: unconditional`     |
| `VM`   | Value Mapping hook rows         | `VM11 -> F31 (tax_rate_enum)`                     |
| `VO`   | Value Operations hook rows      | `VO03 -> F16 (iban)`                              |
| `R`    | Rules in a Business Rules hook  | `R02 <- C11 (Business Rules Validation 1000003)`  |
| `E`    | Events / event-filter rewrites  | `E01 -> hook:1000005 (Totals Aggregation TxS)`    |

New-ID prefix registry:

| Prefix | Target                                | Example                                          |
|--------|---------------------------------------|--------------------------------------------------|
| `F`    | Formula field on a schema             | `F05` (stable across schemas when logic matches) |
| `R`    | Native business rule                  | `R02`                                            |
| `H`    | New hook (e.g. TxScript rewrite)      | `H03` or `hook:<id>` form                        |

**Numbering is local to the source artifact.** `C07` only makes sense *inside* "Calculations hook 1000001". The test-behavioral-equivalence skill always qualifies by source when surfacing it to the user.

### Suffix tokens

Recognized, all optional, order-flexible:

- `(<name>)` — target/source field or formula name (after the arrow target, or after the source artifact).
- `on schema <id> (<label>)` — when the migration is scoped to a single schema; label is usually a country code.
- `Migrated on all N schemas.` — convenience marker when a single formula replaces rows across many schemas. Test skill uses this to decide whether a single-schema failure is local or systemic.
- `Cond: <expr>` — original condition. Invaluable for triage; formulas often roll conditions into the expression itself, so seeing the original side-by-side tells you whether a condition was lost.
- `Note: <text>` or free-form trailing sentence — anything else worth recording (e.g. "passthrough variant for some schemas", "one schema uses a custom formula").

### Non-migrations look different

Rows that stay in an **active** hook carry descriptive-only `//` comments with a colon, no arrow:

```
C00: OrderAmountLine default for item_price_export
```

The test-behavioral-equivalence skill's parser ignores any `//` value that does not contain ` -> ` or `migration-trace:`. This lets the convention coexist with documentation comments that have always been there.

### Intentional behavior changes

If an upgrade is *not* behavior-preserving (rare, but happens — e.g. a rounding rule was wrong and got fixed), emit an explicit marker so the test-behavioral-equivalence skill down-ranks that field from "failure" to "expected delta":

```
C14 -> F08 on all schemas. INTENTIONAL-CHANGE: old rule rounded half-up, new rounds banker's. Cond: unconditional
```

Parser convention: any trace line containing the token `INTENTIONAL-CHANGE` (uppercase, hyphenated) is treated as expected.

## Parser (what downstream skills do)

The `upgrade` skill does **not** emit a `migration-trace.json`. Downstream skills (`test-behavioral-equivalence`, `analyze`) parse breadcrumbs directly from the prd2 tree on each run. Pseudocode below is the reference grammar — the upgrade skill only needs to produce breadcrumbs that match it.

```python
import re, json, pathlib

TRACE_RE = re.compile(
    r'^(?P<legacy>[A-Z]{1,3}\d+)\s*->\s*(?P<target>[A-Z]\w*)'
    r'(?:\s*\((?P<target_name>[^)]+)\))?'
    r'(?:\s+on\s+schema\s+(?P<schema>\d+)(?:\s*\((?P<scope>[^)]+)\))?)?'
    r'(?:\s*\.\s*Cond:\s*(?P<cond>.+?))?'
    r'(?:\s*\.\s*(?P<note>.+?))?$'
)

BACK_RE = re.compile(
    r'^migration-trace:\s*(?P<target>[A-Z]\w*)\s*<-\s*(?P<legacy>[A-Z]{1,3}\d+(?:,[A-Z]{1,3}\d+)*)'
    r'(?:\s*\((?P<source>[^)]+)\))?'
    r'(?:\s*\.\s*(?P<note>.+?))?$'
)

def walk_slashes(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "//" and isinstance(v, str):
                yield path, v
            yield from walk_slashes(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_slashes(v, path + (i,))
```

Phase 0a builds an in-memory trace map shaped like:

```json
{
  "forward": {
    "Calculations_1000001": {
      "C07": {
        "target": "F05",
        "target_name": null,
        "schema": "1234567",
        "scope": "DE",
        "cond": "has_value({amount_paid})",
        "note": null,
        "intentional_change": false
      }
    }
  },
  "backward": {
    "F05": [{"legacy": ["C05","C06","C07","C08","C09"], "source": "Calculations hook 1000001"}]
  }
}
```

This map is never written to disk — each downstream consumer rebuilds it on demand. Phase 4 (diff & report) joins annotation-level field diffs to the map and emits:

> **amount_due** differs in 4/50 annotations. Migrated from `C07` on schema 1234567 (DE). Original condition: `has_value({amount_paid})`. **Likely cause:** condition lost or re-ordered in `F05`.

## What the `upgrade` skill needs to do

A one-line amendment to the upgrade skill's output contract:

1. For every deprecated-artifact row that has a replacement, write a forward trace into its `"//"` sibling key using the grammar above. Never delete the row.
2. For every new formula/rule/hook, write a `migration-trace:` header comment (formulas) or `description`-prefix (rules) with the backward trace.
3. Keep the deprecated hook's JSON in the repo with `"active": false`; do not delete.
4. For genuinely behavior-changing upgrades, use the `INTENTIONAL-CHANGE` token.

That's it. Two breadcrumbs per migration, one forward and one backward, both greppable with the two regexes above.
