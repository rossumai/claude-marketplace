# Parts Library

Vetted, parameterized, composable building blocks for Rossum implementations —
one layer above the reference packs. A reference pack explains *how* something
works (the grammar); a part is a *drop-in* you adapt (a known-good assembly).

**Part vs. fragment:** a **part** is the whole unit — `parts/<axis>/<name>/`,
made up of `part.json` + `fragment.*` + `README.md`. The **fragment** is only
the config payload inside it (the JSON/`.py` body with the `«param»` seams).
Never call a part a fragment or vice versa — a fragment has no metadata, no
README, and cannot stand alone; a part is not itself a file you paste in.

## Layout

Installed, this directory is `${CLAUDE_PLUGIN_ROOT}/parts/`. Read fragments through
that variable — a repo-relative `plugins/rossum-sa/parts/…` path only resolves inside
this marketplace repo, and resolves to nothing from the customer project you are normally
`cd`'d into.

```
parts/<axis>/<part-name>/
  part.json      # metadata contract (see below)
  fragment.*     # the config body: schema-field JSON | hook .py | rule JSON | pipeline-stage JSON
  README.md      # what it does, gotchas, how to adapt
```

Axes: `capture`, `matching`, `validation`, `export`, `formula`.

## Category-qualified reference

A part is referenced as `<axis>/<name>` — e.g. `capture/capture-page-text-to-field`
— never by bare name. The recipe layer will use this form so a reference is
self-describing and resolving it is a direct path join (`parts/<axis>/<name>/`)
with no glob and no ambiguity.

## `part.json` contract

| key | required | meaning |
|-----|----------|---------|
| `name` | yes | unique, kebab-case, equals the folder name |
| `axis` | yes | one of the five axes |
| `summary` | yes | one line, shown in the index |
| `maturity` | yes | `candidate` \| `reviewed` \| `standard` (only `standard` is safe to auto-compose) |
| `params` | yes | object: `{ "<name>": { "type": "string" \| "number", "required": true \| "default": <v>, "description": "..." } }`. `type` is mandatory and drives the seam: a `number` param must appear **bare** in a JSON fragment (`"$gte": «threshold»`), a `string` param **quoted** (`"«dataset»"`). Quote a number and the filled config compares a string against a numeric value — zero rows, no error. CI rejects both mismatches. |
| `produces` | yes | array of **post-fill schema field ids** the fragment *writes* (result actions, datapoint updates, table rows it creates). Each entry is either a literal id that appears in the fragment text, or a `«param»` seam that is a declared param of this part (so filling the seam resolves it to a real id). Empty is legitimate only when the fragment genuinely writes no schema field (e.g. an OAuth token cache) — non-schema outputs (files, tokens, HTTP calls) never belong here. No duplicates. |
| `consumes` | yes | array of **post-fill schema field ids** the fragment *reads* (an input, a gate/condition, a query input, a mapping source) — same literal-or-declared-seam grounding rule as `produces`. Empty is legitimate only when the fragment genuinely reads no schema field. No duplicates. |
| `provenance` | yes | where it was lifted from |
| `reference` | yes | `"<pack-name>#<anchor>"` — links down to the grammar |
| `notes` | no | soft gotchas not mechanically checkable |

## Placeholder convention

Every `param` appears in the fragment as `«param»` (guillemets). An unfilled
seam is therefore greppable, and the CI guard asserts params and placeholders
match exactly. Use `«param»` — never `{{ }}`, `${ }`, or `<...>`.

Because the guillemets are deliberately invalid in every target language, a
`fragment.py` is **not** runnable Python as-shipped. `parts/**` is therefore
excluded from `ruff` (see `pyproject.toml`) — the `test_parts.py` guard reads
fragments as text, not code. Don't expect a `.py` fragment to import or compile
until its seams are filled.

## When is a per-ERP part legitimate?

Export parts are mechanism-level; a target system is a *composition*, never one
monolithic per-ERP part. A part may still be ERP-specific — when all three hold:

1. **ERP-ness is in the grammar, not the values.** The fragment speaks a dialect only
   that ERP uses (e.g. a connector's mapping DSL). If seams alone could de-ERP it, ship
   the generic part + params instead — this is why there is no
   `workday-oauth-token-cache`: `export-oauth-token-cache` + params covers it.
2. **It's a part, not the whole.** The target integration stays a composition of several
   independently-swappable parts. A single folder bundling match + mapping +
   attachments would resurrect CIB inside the library.
3. **Tenant-level facts stay seams.** WIDs, hosts, tenant ids, worktag sets, order-type
   ids — parameterized, never baked.

ERP-specific parts carry the ERP token in the name, so the per-ERP subset stays a
greppable audit. They are also provisional: their grammar lifts into a reference pack
when one exists, at which point they become thin drop-ins linking down to it.

## Maturity & promotion

v1 parts are `standard` by provenance (lifted from expert-written packs).
Future candidates: `candidate → reviewed` requires passing `analyze` +
`coding-best-practices` + `evaluate-namings` with complete metadata;
`reviewed → standard` requires positive production-automation evidence plus a
named senior-SA sign-off recorded in `provenance`. Prevalence is only a
*candidate* signal — frequency ≠ quality.
