# Recipe Library

Vetted, parameterized, composable building blocks for Rossum implementations —
one layer above the reference packs. A reference pack explains *how* something
works (the grammar); a recipe is a *drop-in* you adapt (a known-good assembly).

## Layout

```
recipes/<axis>/<recipe-name>/
  recipe.json    # metadata contract (see below)
  fragment.*     # the config body: schema-field JSON | hook .py | rule JSON | pipeline-stage JSON
  README.md      # what it does, gotchas, how to adapt
```

Axes: `capture`, `matching`, `validation`, `export`, `formula`.

## `recipe.json` contract

| key | required | meaning |
|-----|----------|---------|
| `name` | yes | unique, kebab-case, equals the folder name |
| `axis` | yes | one of the five axes |
| `summary` | yes | one line, shown in the index |
| `maturity` | yes | `candidate` \| `reviewed` \| `standard` (only `standard` is safe to auto-compose) |
| `params` | yes | object: `{ "<name>": { "required": true } \| { "default": <v> }, "description": "..." }` |
| `produces` | yes | array of schema field ids this recipe adds (may be empty) |
| `consumes` | yes | array of schema field ids it expects to already exist (may be empty) |
| `provenance` | yes | where it was lifted from |
| `reference` | yes | `"<pack-name>#<anchor>"` — links down to the grammar |
| `notes` | no | soft gotchas not mechanically checkable |

## Placeholder convention

Every `param` appears in the fragment as `«param»` (guillemets). An unfilled
seam is therefore greppable, and the CI guard asserts params and placeholders
match exactly. Use `«param»` — never `{{ }}`, `${ }`, or `<...>`.

## Maturity & promotion

v1 recipes are `standard` by provenance (lifted from expert-written packs).
Future candidates: `candidate → reviewed` requires passing `analyze` +
`coding-best-practices` + `evaluate-namings` with complete metadata;
`reviewed → standard` requires positive production-automation evidence plus a
named senior-SA sign-off recorded in `provenance`. Prevalence is only a
*candidate* signal — frequency ≠ quality.
