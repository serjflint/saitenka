---
name: assurance-pipeline
description: >-
  Compose Saitenka's architecture inquiry, Grow, Sharpen, test-adequacy, and contribution loops into
  one package-level assurance case. Use for "run the assurance pipeline", "gather proofs", "turn this
  test finding into a module/package improvement", "combine Grow and Sharpen", or when a supported
  scenario crosses several owners and one-test ratchets are too local. Routes each falsifiable finding
  to the smallest proof-producing primitive, re-enfolds counterexamples, and requires exact-head review.
  NOT for one missing test (grow-loop), one weak assertion (sharpen-loop), one pure-core campaign
  (test-adequacy), one bounded PR (contribute), or an architecture decision without implementation
  (architecture-inquiry).
metadata:
  project: saitenka
---

# assurance-pipeline

Build one assurance case around a supported product scenario. The pipeline composes existing loops; it
does not weaken, duplicate, or replace their gates. Read
[`references/contract.md`](references/contract.md) and start an
[`evidence ledger`](references/evidence-ledger.md) before changing an artifact.

## 0. Freeze the whole

Record the base commit, supported scenario, product invariant, affected owners, and a scope guard. State
what observation would falsify the current architecture hypothesis. Line coverage, test count, module
size, and a tidy dependency graph are signals only; none is the invariant.

Work in a clean dedicated worktree. Default to local-only evidence. Outward action requires the user's
explicit request; this skill never merges.

## 1. Inquire before routing

Use `architecture-inquiry` to trace the scenario whole-to-parts-to-whole: declared, enforced, and true
behavior; writers and readers; authority reachability; supplied and absent guarantees. When external
mechanisms are genuinely missing, use `research` and verify primary sources. Stop research at evidence
saturation, then construct the smallest deterministic discriminator.

Run the discriminator against the base. Record falsified hypotheses instead of manufacturing a test or
fix after the premise fails.

## 2. Route the finding

Choose by the evidence gap, not by a preferred artifact:

| Finding | Proof-producing primitive |
| --- | --- |
| Supported scenario is absent | `grow-loop` |
| Existing oracle is weak or non-live | `sharpen-loop` |
| Pure-core behavior is under-specified | `test-adequacy` |
| Production code contradicts the invariant | `contribute` |
| Architecture hypothesis is false | ledger the no-change result and re-enfold |

Each invoked loop retains its own baseline, isolation, liveness, restoration, review, and scope rules.
Return its evidence to this pipeline; a passing candidate is an input, not terminal success.

## 3. Escalate only when the invariant crosses owners

Move from a test-sized candidate to a module/package change only when one supported scenario requires
multiple owners to agree, or the discriminator exposes mixed identities, generations, ordering, or
policy across those owners. Widen only along that same invariant and production path. Unrelated defects,
cleanup, and attractive refactors become follow-ups.

For every independent mechanism in the final change, require an exact base/branch behavioral
discriminator, an oracle-liveness witness, and semantic deletion or an equivalent evidence-subtraction
check. Coverage is a locator; the proof is that the claimed guarantee fails when its mechanism is removed.

## 4. Re-enfold after every counterexample

Reconstruct the whole scenario after each candidate or reviewer finding. Update the hypothesis, affected
owners, missing guarantees, and scope guard. A counterexample may reroute the next step; it does not grant
permission to broaden the product invariant.

Stop when the evidence saturates: the supported scenario is coherent across its owners, every applicable
counterexample class has a proof, the deterministic gates are green, and the residual uncertainty is
explicit. Do not stop because coverage, mutation score, or test count reached a pleasing number.

## 5. Complete one exact tree

Run affected checks during editing, then the full deterministic and free-threaded gates required by the
repository. Commit the final artifact before review.

Give isolated adversarial reviewers only the base, exact head, diff, claimed invariant, and validation;
withhold author rationale. Record P0-P3 findings and the reviewed commit. **Every launched reviewer must
finish.** Any artifact change invalidates all earlier approvals, so rerun the relevant proof and review
the new exact head.

On an outward path, use `contribute` and `pr-ticket-describe` to open a lean ready PR after the evidence
ledger is complete. Preserve residual risks and follow-ups without smuggling them into the current scope.

## Verify

`bash scripts/smoke.sh` (grep-free).
