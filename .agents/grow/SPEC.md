# Grow Loop

Grow is a bounded idle-time audit that adds one missing behavioral test. It does not chase line
coverage, refactor production code, or merge. A human decides whether a proved finding deserves a PR.

## Boundary

Grow is additive: append a case, example, assertion, or test without weakening existing assertions.
Changing an existing test's meaning belongs to Sharpen. A red-on-pristine proposed test is a product
finding, not a test-growth success.

## One run

1. Select one module that is not under active feature work. Triage is advisory; stale or missing optional
   mutation/coverage evidence is ignored or regenerated, never treated as authoritative health.
2. Build a scenario map from the production seam and its mapped tests. Pick one observable orphan, then
   query `grow_ledger.py status` for its semantic coordinate. Skip `closed-current`, `filed`, and
   `unclosable`.
3. Apply the `write-test` decision tree and add the smallest test at the existing extension point.
4. Run the mapped baseline and the focused changed test.
5. Prove the test has teeth:
   - always preserve existing assertions and demonstrate a live oracle;
   - for a scenario, either show a focused mutant survives the old suite and is killed by the new test,
     or show a genuinely new coverage path;
   - for concurrency, use a deterministic guarded regression plus an unguarded negative control.
6. Give the final diff to two isolated adversarial reviewers only when preparing a PR. Either concrete
   refutation drops the candidate.
7. Record a durable result only when it prevents repeated work: landed/PR-ready, issue-filed, or explicitly
   unclosable. Exploratory, bounced, and no-candidate runs need no receipt.
8. For a PR, follow the repository contribution gates. Never merge.

## Outcomes

- **coverage-only:** behavior was already correct; the test pins it.
- **product bug:** the proposed oracle fails on pristine code; route test plus fix to a normal contribution.
- **robustness/design gap:** file or propose the smallest product-level follow-up.
- **unclosable:** retain the coordinate and reason only when that prevents immediate rediscovery.

## Durable memory

`.ledger.grow.jsonl` is duplicate-prevention, not proof that commands or agents existed. Identity is
semantic: source, target symbol, and scenario dimension, with a target hash that reopens the coordinate
when its implementation changes. Historical records remain readable; new records should carry only the
coordinate, disposition, decisive evidence summary, and PR/issue reference.

## Reflection

Reflection is a periodic human-triggered retrospective over several runs, or an explicit response to a
repeated false pass/bounce. It produces a short proposal in normal planning/issue workflow. It is advisory,
does not gate a run, does not write a receipt, and never modifies the loop automatically.
The retained lessons and output bound live in `../quality-loop-retrospective.md`.

## Human gate

The useful output is a small test or a concrete product finding. Genuine findings should be offered as a
PR or issue; keeping a worktree clean is not a reason to discard them.
