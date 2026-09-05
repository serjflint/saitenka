# Sharpen Loop

Sharpen is a bounded idle-time audit that improves one existing test without reducing what it detects.
It does not add missing scenarios, refactor production code, or merge. A human decides whether a proved
improvement deserves a PR.

## Boundary

Sharpen is mutative: it changes an existing test's assertion, structure, or execution tier. A missing
scenario belongs to Grow. A production defect exposed by a stronger assertion belongs to a normal
contribution with the test and fix together.

## Inputs that can justify a run

- a complete non-equivalent mutation survivor tied to an existing test;
- a target-grounded actionable test-lint finding;
- a concrete brittleness or redundancy witness.

Counts and rankings only locate work. They are not findings, and absent/deferred instruments are not
recorded as passing.

## One run

1. Select one module that is not under active feature work and one concrete coordinate.
2. Confirm its mapped tests are green before editing.
3. Improve only the selected existing test, asserting observable behavior through the nearest stable seam.
4. Run the focused affected tests.
5. Prove preservation and gain:
   - run `sharpen_gate.py anticheat` for every assertion edit;
   - mutation-backed work must kill the named survivor without losing relevant prior kills;
   - off the mutation allowlist, an assertion replacement/removal needs a focused source witness killed
     both before and after;
   - marker/structure changes need the targeted lint result and unchanged behavior.
6. Give the final diff to two isolated adversarial reviewers only when preparing a PR. Either concrete
   refutation drops the candidate.
7. Record a durable result only when it prevents repeated work: landed/PR-ready, Grow handoff, product
   issue, or explicitly unsharpenable.
8. For a PR, follow the repository contribution gates. Never merge.

## Durable memory

`.ledger.sharpen.jsonl` prevents duplicate audits. The module-plus-mapped-tests hash reopens work when
the relevant tree changes. Historical records remain readable; new records should retain only the
coordinate, disposition, decisive before/after evidence, deliberate handoffs, and PR/issue reference.
Use `sharpen_ledger.py append`; it derives the mapped tests, content hash, and toolset version.

## Reflection

Per-run reflection is the short list of unavailable evidence or deliberate residue already present in
the result. Process-level reflection is periodic and human-triggered. It may recommend changing an
instrument after demonstrated repeated failure, but it is never a completion receipt or an automatic
toolset change.
The retained lessons and output bound live in `../quality-loop-retrospective.md`.

## Human gate

The useful output is a plainly stronger or better-factored test. Cosmetic lint churn and metric-only
signals do not justify a PR; genuine improvements do.
