# Hook ordering

Ordering rules that hold across recipes. **Ordering errors are silent**: a hook that runs before the
field it reads is populated reads empty and writes nothing — no error, no log line, just a blank
field and a rule that never fires.

## Derive the order, don't assert it

An edge exists wherever a downstream mechanism reads a field an upstream one writes. Measured on one
implementation: 29 fields flow matching → formula, 64 flow formula → rule (**48% of all rule-read
fields**), and 11 traverse all three. That graph *is* the hook chain.

Two consequences:

- **A rule that gates on a derived value must run after the derivation**, which must run after the
  matching that produced its inputs.
- **A formula cannot be the mid-chain carrier between two hooks.** Formulas evaluate only *after* a
  hook completes, so a value another hook needs during the same chain has to be written by a hook, or
  carried as a `property`. This is also why an export chain with a mid-chain formula dependency
  cannot collapse into a single request-processor hook.

## The spine

```
ingest → route → normalise → match (core) → derive → match (dependent) → derive → flag
       → validate → surface → export
```

- **Normalise before matching.** Matching consumes normalised identifiers; running normalisation
  after it reads empty.
- **Dependent matching after its inputs.** Coding segments frequently chain — one segment's match
  feeds the next segment's lookup. Gate failures here are silent and leave every downstream segment
  empty.
- **Flags last among content hooks.** Diagnostics read everything above them.
- **Surface after gate.** Operator-facing show/hide reads flags the gates also read.

## Ordering is not always expressed as edges

Two mechanisms carry order, and a tree can use both:

| mechanism | looks like | how to read it |
|---|---|---|
| `run_after` edges | an explicit chain | the graph is the order |
| queue scoping + event | many roots, one pipeline per document stream | the *binding* is the order |

One implementation in the corpus has 48% of its orderable nodes as roots with chains up to 8 deep —
both mechanisms load-bearing at once. Judge with two signals (root fraction **and** max chain depth);
a single definite label needs both to agree, otherwise the honest answer is `mixed`.

## Events

- `initialize` runs once on import. A part registered only there **cannot be verified by a soft
  re-fire** — budget a re-upload.
- A pure recalc must include `started`; `user_update` alone fires only off changed datapoints and is
  a silent no-op otherwise.
- The export trigger is **not always `annotation_content.export`** — one implementation exports on
  `confirm`. Read the trigger, don't assume it.
