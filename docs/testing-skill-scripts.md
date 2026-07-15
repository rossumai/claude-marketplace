# Testing bar for skill scripts

How much test suite a script bundled with a skill deserves, and — just as
important — when to stop adding to it. This exists because adversarial review
rounds only ever ADD tests; without a stopping rule a migration script ends up
with a ratchet-grown suite pinning log wording and request dicts. The
`coupa-bulk-replication` suite (post-pruning) is the worked example.

## Scale the suite to blast radius

- **Scripts that WRITE to customer systems** (Data Storage loads, hook
  patches, deletes): pin three things exhaustively —
  1. **data-integrity invariants** (no duplicates, no lost records, never
     delete anything the run didn't create),
  2. **exit codes** (a wrapper like `smoke && full-run` must be able to trust
     them),
  3. **CLI contracts** (refusal guards for unsafe flag combinations, config
     validation that prevents a bad run from starting).
- **Scripts that only read** (counters, reporters, inspectors): smoke coverage
  is enough — one happy path per mode plus any pure-function algorithm with
  real failure potential. A miscounted report is an annoyance, not an
  incident.

## Pin behavior, not implementation

- **No assertions on log wording** — `[WARN]`/`[NOTE]` text, print formatting,
  argparse hint phrasing. If a warning has a semantic consequence (an exit
  code, a refusal, a state change), assert the consequence, not the string.
  Error messages in refusal guards may be checked for the *names of the
  conflicting flags* — that is the contract — never for full phrasing.
- **No assertions on request dict shapes** (query/pipeline/projection bodies)
  when a stateful fake proves the behavior. Prefer the `FakeDS` pattern (a
  stub that *stores documents* and answers reads from that store) over
  call-recording stubs: `assert store.value_counts() == {…: 1}` survives
  refactors that `assert calls[0][1]["pipeline"] == […]` breaks on, and it
  catches real double-inserts that a call recorder never sees.
- **One representative per outcome** for classification helpers — not the
  combinatorial input matrix.
- Exception: a request detail that is itself a live-verified API contract
  (e.g. DS delete endpoints take `filter`, not `query`) may be pinned once,
  with a comment saying why.

## Rules for review ratchets

- **Every pin request must name the invariant it protects.** "Add a test for
  X" is not actionable until X is stated as an invariant ("records persisted
  before a 401 must not double-insert on the healed retry"). Wording and
  shape concerns get *fixed*, not pinned.
- **Prune in the same PR that grows the suite past the bar.** If a review
  round adds pins, the same round removes tests the new ones subsume. A suite
  that only ever grows is a process smell.
- **Deletion criteria:** a test may be deleted iff (a) a kept behavioral test
  covers the same invariant, or (b) it protects only wording or internal
  shape. If a test is the ONLY thing pinning a real invariant, it stays —
  regardless of how stylistically unfashionable it is. List deletions in the
  commit message, grouped by reason.
- **Don't test the test helpers.** Stub/fixture scaffolding earns no tests of
  its own; if a helper is complex enough to need them, simplify the helper.

## Worked example

`plugins/rossum-sa/skills/coupa-bulk-replication/tests/` — a writer script
held to the high bar, after pruning ~50 ratchet tests (commit
`test(coupa-bulk): prune review-ratchet ceremony`):

- data integrity: end-to-end `FakeDS` pins for resume-overlap dedup,
  401-heal-through-checked-path, smoke-leftover absorption,
  concurrent-writer record survival, retry/recovered accounting;
- exit codes: smoke failure paths, supervisor 0/1/130;
- CLI contracts: one parameterized refusal-guard test
  (`test_bulk_cli_guards.py`) covering every unsafe flag combination;
- config guards: duplicate-collection rejection, credential stripping,
  misconfigured-id_key fail-fast, unique-index abort + override.
