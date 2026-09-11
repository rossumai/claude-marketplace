# Recipes

A **recipe** is an ordered, checkable plan for building one shape of implementation. Parts (`parts/`)
are ingredients; a recipe is the assembly — what runs in which order, what an SA must decide, and how
each phase is proven.

A recipe is **not** config. It carries intents, ordering, decisions and verification, and references
parts by name. It never carries a customer's values.

## Files

| path | what |
|---|---|
| `<name>/recipe.json` | the machine-readable recipe |
| `<name>/README.md` | the narrative an SA reads |
| `hook-ordering.md` | ordering rules that hold across recipes |
| `tools/shape_extract.py` | read-only extractor: one pulled `prd2` tree → a shape record |

## The contract

**Intents, not artifacts.** The unit is a statement of what must be true (*"a PO-backed invoice must
resolve to an open PO line"*), not the mechanism. Mechanism appears as `realized_by` (what was
observed), `alternatives` (what else works), and **`constraint`** (what forces or forbids a choice).
The constraint field is what makes a recipe buildable — without it, "pick a mechanism per best
practice" produces designs that cannot work.

**Ordering is derived, not asserted.** `hook_chain` follows from `field_flow`: an edge exists wherever
a downstream mechanism reads a field an upstream one writes. Ordering errors are *silent* — a hook
that runs before the field it reads is populated reads empty and writes nothing.

**Every phase has a `verify`.** A phase without a passing verify is not done; that is the difference
between "built" and "proven".

**`status`**

- `preferred` — what new work should reach for.
- `supported` — widely deployed and fully valid, carries `preferred_alternative`.

`supported` is **not** deprecation. Promotion from `supported` to `preferred` is explicit human
judgement, never a function of how often a shape was observed: prevalence is evidence of history, not
desirability.

**`provenance` is a count, never a list.** `"mined from 7 implementations"`, never which. Deduplicate
before counting: second pulls of one customer, and copies of a baseline, are one source.

**n≥5 before publishing.** A recipe built from one or two implementations is a description of that
customer's system — recognisable to them, and to a competitor, even with every name stripped. Below
five, it stays a candidate outside this directory.

## What must never appear here

Org names or ids, queue/hook/schema ids, customer field values, document content, SOW prose,
collection *contents*, endpoint hostnames, credential references — and no **worked recipe** (a recipe
with its seams filled for one customer). Instantiating a recipe means putting customer specifics into
it, so a worked recipe is customer data by construction and belongs in that customer's `prd2` project.

`tests/test_recipes.py` enforces the mechanical half of this.

## Using one

1. Read the phases in order. Each has a `goal`, a `decide[]` and a `verify`.
2. Resolve every `decide[]` **before** building the phase — these are the questions that otherwise
   surface at hour two.
3. Run the phase's `verify` before moving on.
4. Take ordering from `hook_chain`, not intuition.
5. Read `platform_contract` before writing anything. Every entry cost a failed API call to learn.

## Adding one

Run `tools/shape_extract.py --summary ~/Projects` to cluster what you have, then
`--env <tree> --json out.json` per tree. Shape records name customers, so they stay **private tier** —
local or a private repo, never here. Only the generalized recipe is publishable.

## Delivering in chunks

A recipe is built one phase per working session, not in one pass. Each phase is a deliverable chunk
with its own `verify`, which doubles as the handoff gate between sessions.

**Delivery order is not runtime order.** The observed working sequence is:

1. **Digest the target API** — read the endpoints, the payload shape, what it rejects. Everything
   downstream is shaped by this and it is cheap to get wrong late.
2. **Master-data import** — collections exist and are fresh, so matching has something to resolve.
3. **Export** — pin the payload early: it determines what matching and derivation must *produce*.
4. **Matching** — resolve supplier, PO, coding against the collections from step 2.
5. **Validation** — gate on everything the previous steps populate.

Steps 3 and 4 are deliberately inverted relative to `hook_chain`, which is the order things *run*.
Pinning the export contract first stops matching from resolving fields the target never wanted.
