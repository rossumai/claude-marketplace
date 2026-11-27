# Test report template

Used by Phase 4e of `test-behavioral-equivalence`. Copy it into `TEST-*.md` and fill every bracket.

```markdown
# Test Report: [Customer/Project Name]

**Test run:** [timestamp]
**Scope:** [one sentence describing what was tested]
**Export architecture:** [Pattern 1 / Pattern 2 / Hybrid — from Phase 0d]
**Verdict:** **Pass** / **Pass-with-warnings** / **Fail**

## Summary

- Corpus size: N annotations (A automated, M manually reviewed, spread across K queues)
- Pass / soft-fail / hard-fail: X / Y / Z
- Automation rate: before A% → after B% (Δ)
- Out-of-scope changes (documented, not diffed): [list]

## Static Validation

| Check | Result | Details |
|---|---|---|

## Equivalence Results

| Annotation ID | Queue | Verdict | Field diffs | Message diffs | Blocker diffs | Export signal | Link |
|---|---|---|---|---|---|---|---|

## Grouped Failures

### P0 — Formula crashes (if any)

| Annotation | Schema ID | Message |
|---|---|---|

### Group 1: [schema_id or pattern]
- Fan-out: N/K corpus annotations
- Affected annotations: [list]
- Example diffs: [2-3 before/after pairs]
- Likely cause: [cross-ref to change manifest; include migration-trace ID if available]
- Suggested fix: [...]

(Repeat per group)

## Coverage

| Change | Exercised by corpus? | Severity if uncovered |
|---|---|---|

Uncovered changes (risk-ranked): [list, elevate to P0 any gap where observed regressions suggest the uncovered path is likely broken]

## Automation Parity

| Metric | Before | After | Delta |
|---|---|---|---|

## Manual Sign-off Items

- [ ] ...
```
