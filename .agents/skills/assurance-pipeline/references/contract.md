# Assurance pipeline contract

This is the orchestration contract for turning local test evidence into a coherent module/package
improvement. It is shaped like an assurance case: a product claim is supported by explicit arguments and
evidence. Its iteration is counterexample-guided: failed discriminators and adversarial findings refine
the model before the next candidate. Those are useful prior-art primitives, not proof that another
system's architecture should be copied.

## Roles and non-roles

| Surface | Supplies | Does not establish |
| --- | --- | --- |
| `architecture-inquiry` | product invariant, whole-system trace, falsifiable discriminator | implementation fitness by itself |
| `grow-loop` | one genuinely missing scenario and live additive oracle | package coherence |
| `sharpen-loop` | one stronger existing oracle with preservation evidence | new scenario coverage or source correctness |
| `test-adequacy` | survivor, crasher, or counterexample plus instrument replay | relevance outside the pure core |
| `contribute` | coherent source/tooling change, repository gates, PR packaging | the outer assurance claim unless its evidence is returned |
| `assurance-pipeline` | routing, composition, re-enfolding, scope, exact-tree completion | permission to weaken a child contract |

The pipeline may consume a completed child-loop artifact or invoke a child loop. It must record which.
It never treats an agent verdict as a substitute for a deterministic proof.

## Package-escalation test

Escalation is warranted when all of these hold:

1. the scenario is supported product behavior, not unsupported direct misuse;
2. one named invariant spans two or more substantive owners or mechanisms;
3. the base discriminator demonstrates contradiction, mixed generation/identity, or a missing guarantee;
4. a one-file test change cannot make production satisfy the invariant;
5. the proposed widening follows the same scenario and invariant.

The following do not justify escalation alone: high LOC, many uncovered lines, a mutation score, a
reviewer's unrelated observation, a general desire for cleaner layers, or several tests that merely touch
the same module.

## Proof obligations

Every claimed mechanism gets a row in the ledger. Applicable evidence is cumulative:

- **Exact A/B:** the supported observation fails on the recorded base and passes on the recorded head.
- **Oracle liveness:** a deliberate relevant perturbation makes the oracle fail.
- **Evidence subtraction:** delete or neutralize the claimed mechanism; the focused proof must fail, then
  restore the exact bytes. This is semantic deletion, not line coverage.
- **Preservation:** when an existing assertion or policy changes, show that unrelated established behavior
  remains observable.
- **Instrument replay:** mutation/fuzz/symbolic coordinates are rerun through their canonical poe task.
- **Integration:** prove agreement across the real composition seam using repository fakes for external
  boundaries.
- **Whole-suite and free-threaded gates:** protect interactions outside the focused proof.
- **Exact-head adversarial review:** reviewers attack the final artifact and the stated proof, not the
  author's story.

Not every row needs every proof kind. A skipped proof needs a reason tied to the mechanism. “Tests pass”
and “coverage increased” are never sufficient reasons.

## Counterexample and widening discipline

Classify a new finding before acting:

- **same invariant, same supported path:** widen the current assurance case;
- **same subsystem, different invariant:** record a follow-up;
- **unsupported call path or misuse:** reject it as a product discriminator unless the product contract is
  deliberately expanded;
- **false premise:** preserve the falsification and rebuild the model;
- **tool/instrument defect:** repair in a separate contribution unless it prevents trustworthy evidence.

Reviewer discovery does not silently rewrite scope. Re-enfold first and state why a finding is inside or
outside the frozen invariant.

## Completion and invalidation

A pipeline result is complete only when:

- the final scenario trace has no contradictory generation, identity, policy, or ordering among its owners;
- every mechanism claimed necessary has direct proof or a recorded non-applicability reason;
- all temporary perturbations restored exact bytes;
- repository-required deterministic and free-threaded gates passed on the final head;
- every launched reviewer returned and all required findings were resolved or explicitly accepted;
- every approval names the final commit, and the worktree is clean.

Any artifact edit after review invalidates all previous approvals. A reviewer timeout or abandoned
invocation is an unfinished review, not an implicit pass. If evidence saturates with no justified change,
a well-supported no-change result is successful research.
