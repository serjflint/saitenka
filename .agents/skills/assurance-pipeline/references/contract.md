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
| `grow-loop` | idle-time dry-run receipt for one missing scenario and live additive oracle | a retained edit in the package worktree |
| `sharpen-loop` | idle-time dry-run receipt for one stronger oracle with preservation evidence | a retained edit in the package worktree |
| `test-adequacy` | survivor, crasher, or counterexample plus instrument replay after acceptance | a discovery-only reverting mode |
| `write-test` | a test materialized in the active contribution worktree | package coherence by itself |
| `contribute` | coherent source/tooling change, repository gates, PR packaging | the outer assurance claim unless its evidence is returned |
| `assurance-pipeline` | routing, composition, re-enfolding, scope, exact-tree completion | permission to weaken a child contract |

The pipeline may consume a Grow/Sharpen coordinate or invoke a dry-run only while no feature work
is active and the target passes the child loop's idle/exclusion rules. Those candidate edits revert. Once
implementation starts, `write-test` materializes additive package tests. A reverted Sharpen edit is never
replayed: its coordinate may motivate a new additive package oracle, while changing the old assertion stays
a later standalone Sharpen run. Discovery may consume a complete existing adequacy coordinate, but both
hardening and instrument replay wait until the design is accepted. The pipeline records which
evidence was consumed and never treats an agent verdict as a substitute for deterministic proof.

A Grow/Sharpen receipt is historical evidence, never current package proof by itself. To count it as proof,
recover the exact candidate bytes and replay every applicable child arm against a current green baseline;
bind the surrounding source, mapped tests, candidate diff, current loop/toolset/contract version, restoration,
and new review results. Otherwise it contributes only an untrusted coordinate or falsified hypothesis.

## Entry and decision checkpoint

A fresh run creates a read-only architecture dossier. It may gather dry-run test evidence, but it stops
before source/package implementation until the human records an accepted arrangement. A resumed run names
that dossier, decision, base, invariant, and discriminator. It validates them, then proceeds without
recursively invoking architecture inquiry. An outward-action request is authorization to publish completed
work, not evidence that an architecture choice was accepted.

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

## Proof obligations by change type

Classify each final artifact before selecting obligations:

| Change type | Baseline/branch obligation | Additional evidence |
| --- | --- | --- |
| Bug fix or behavioral feature | supported observation fails on base and passes on head | live oracle; evidence subtraction for each claimed behavioral mechanism; integration where owners meet |
| Behavior-preserving refactor | same acceptance suite passes on base and head | boundary/reachability or retirement proof; preservation evidence; semantic deletion only for a claimed retired policy/mechanism |
| Test-only growth/hardening | child loop's exact liveness, restoration, preservation, and review contract | final suite and exact-head review |
| Tooling or documentation | source-backed tool/reader failure on base and contract/smoke proof on head | path rot-guard and reader check as applicable |

Every claimed mechanism gets a row in the ledger. Applicable evidence is cumulative:

- **Exact baseline/branch comparison:** use the change-type obligation above; A/B does not always mean
  fail-base/pass-head.
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

Not every row needs every proof kind. Select the obligation before authoring, and give every skipped proof
a reason tied to the change type and mechanism. “Tests pass” and “coverage increased” are never sufficient.

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

A **no-change result** completes locally when the discriminator is preserved, the falsified hypothesis is
re-enfolded into the final whole, residual uncertainty is explicit, and no tracked change is justified. It
also restores every pipeline-created tracked byte to its pre-implementation digest and leaves the package
worktree/index clean. Child-loop ledger-only results stay in their dedicated worktrees under the child
contract, with their location/disposition recorded. No-change does not create an empty commit, run
artifact-only gates/reviews, or open a PR.

An **artifact result** is complete only when:

- the final scenario trace has no contradictory generation, identity, policy, or ordering among its owners;
- every mechanism claimed necessary has direct proof or a recorded non-applicability reason;
- all temporary perturbations restored exact bytes;
- repository-required deterministic and free-threaded gates passed on the final head;
- two isolated exact-head reviewers returned; P0/P1 are fixed, P2 resolved or accepted by the human owner, and P3 recorded;
- reviewers remained read-only and their pre/post HEAD, packet/diff digests, tracked-tree, and index checks match;
- every approval names the same reviewed-packet digest, and the worktree is clean.

The reviewed packet binds the base, head, diff, supported scenario, invariant, accepted dossier and human
decision, affected owners, discriminator, scope guard, final scenario trace, mechanism-proof matrix,
validation evidence, residual uncertainty, and follow-ups. Any change to one of those fields invalidates
all approvals. Appending returned verdicts to a separate git-ignored review table does not change the
packet. A reviewer timeout or abandoned invocation is unfinished, not an implicit pass. Only the human
owner may accept a P2. Prefer a disposable worktree per reviewer; regardless, repeat the same HEAD, digest,
tracked-tree, and index checks in the publication worktree after every reviewer has returned.

Canonical diff bytes are `git diff --no-ext-diff --no-textconv --binary BASE...HEAD`; record the SHA-256
tool used. If a reviewer cannot return, terminate it through the host, record the terminal failure, invalidate
the entire review generation, and start a new two-review generation. Failed/canceled reviewers never count.

## Contribution handoff modes

- **prepare-only:** `contribute` performs diagnosis, design, implementation, and repository gates, then
  returns the unreviewed/unpublished exact tree. It does not push or open a PR.
- The pipeline freezes the complete packet and its two reviews also satisfy `contribute`'s review phase.
- **publish-only:** after both approvals, `contribute` verifies the unchanged head, packet, gates, and
  reviews, then performs PR packaging only. It neither edits artifacts nor launches another review; failed
  validation returns to prepare-only and invalidates the packet.
