---
name: assurance-pipeline
description: >-
  Compose Saitenka's architecture inquiry and evidence from Grow, Sharpen, test-adequacy, and contribution
  work into one package-level assurance case. Use for "run the assurance pipeline", "gather package-level
  proofs", "turn this test finding into a module/package improvement", "combine Grow and Sharpen", or when a supported
  scenario crosses several owners and one-test ratchets are too local. Routes each falsifiable finding
  to the smallest proof-producing primitive, re-enfolds counterexamples, and requires exact-head review.
  NOT for one missing test (grow-loop), one weak assertion (sharpen-loop), one pure-core campaign
  (test-adequacy), one bounded PR (contribute), routine pre-push evidence (dev-gate), or an architecture
  decision without implementation (architecture-inquiry).
metadata:
  project: saitenka
---

# assurance-pipeline

Build one assurance case around a supported product scenario. The pipeline composes existing loop evidence;
it does not weaken, duplicate, or replace child gates. Read
[`references/contract.md`](references/contract.md) and start an
[`evidence ledger`](references/evidence-ledger.md) before changing an artifact.

## 0. Freeze the whole and entry state

Record the base commit, supported scenario, product invariant, affected owners, and a scope guard. State
what observation would falsify the current architecture hypothesis. Choose one entry state:

- **fresh inquiry** — research and discriminate read-only, then stop for the human's design choice;
- **accepted dossier** — record the accepted architecture-inquiry dossier and human decision, then resume
  from its invariant and discriminator without re-running the inquiry.

Line coverage, test count, module size, and a tidy dependency graph are signals only; none is the invariant.

Work in a clean dedicated worktree. Default to local-only evidence. Outward action requires the user's
explicit request; this skill never merges.

## 1. Inquire before routing

In **fresh inquiry**, use `architecture-inquiry` to trace the scenario whole-to-parts-to-whole: declared,
enforced, and true behavior; writers and readers; authority reachability; supplied and absent guarantees.
When external mechanisms are genuinely missing, use `research` and verify primary sources. Stop research
at evidence saturation, then construct the smallest deterministic discriminator.

Run the discriminator against the base. Record falsified hypotheses instead of manufacturing a test or
fix after the premise fails. Present the re-enfolded dossier and stop for a human choice before any source
or package implementation. In **accepted dossier**, verify the recorded base and discriminator still hold;
do not recursively re-enter architecture inquiry.

## 2. Route the finding

Choose by the evidence gap and current phase, not by a preferred artifact:

| Phase and finding | Proof-producing primitive |
| --- | --- |
| Discovery; supported scenario is absent | consume a Grow coordinate, or eligible `grow-loop` dry-run |
| Discovery; existing oracle is weak | consume a Sharpen coordinate, or eligible `sharpen-loop` dry-run |
| Discovery; pure-core behavior is under-specified | consume a complete existing adequacy coordinate only |
| Implementation; additive test must join the package tree | `write-test` inside the contribution worktree |
| Implementation; existing assertion is weak | additive package oracle now; separate Sharpen follow-up when idle |
| Implementation; accepted pure-core coordinate | `test-adequacy` hardening and replay |
| Implementation; production contradicts the accepted invariant | `contribute` prepare-only mode |
| Architecture hypothesis is false | ledger the no-change result and re-enfold |

Grow and Sharpen remain idle-time loops: invoke them only before implementation, only when their cadence
and exclusion rules say the target is idle, and only in their existing dry-run mode. Their candidate edits
revert. A receipt supplies a coordinate and historical evidence, not current package proof. Child receipts
do not retain canonical candidate bytes, so never replay them. After human acceptance, build a fresh
additive package oracle through `write-test` and prove it on the current tree. The pipeline never consumes
a child working tree.
After a design is accepted or feature work begins, materialize only additive package tests through
`write-test`; never replay a reverted Sharpen edit or invent an embedded Grow/Sharpen mode. A Sharpen
receipt can justify an additive package oracle; changing the old assertion remains a later standalone
Sharpen run when the module is idle.

Every child retains its own baseline, isolation, liveness, restoration, review, and scope rules. A passing
child candidate is an input to the assurance case, not terminal package success.

## 3. Escalate only when the invariant crosses owners

Move from a test-sized candidate to a module/package change only when one supported scenario requires
multiple owners to agree, or the discriminator exposes mixed identities, generations, ordering, or
policy across those owners. Widen only along that same invariant and production path. Unrelated defects,
cleanup, and attractive refactors become follow-ups.

Classify the final change and apply the proof matrix in `references/contract.md`. Bug fixes and behavioral
features require fail-base/pass-head evidence; behavior-preserving refactors require the same acceptance
suite on both plus their boundary/retirement proof. Liveness and evidence subtraction apply to claimed
behavioral or test mechanisms, with any non-applicability justified. Coverage is only a locator.

## 4. Re-enfold after every counterexample

Reconstruct the whole scenario after each candidate or reviewer finding. Update the hypothesis, affected
owners, missing guarantees, and scope guard. A counterexample may reroute the next step; it does not grant
permission to broaden the product invariant.

Stop when the evidence saturates: the supported scenario is coherent across its owners, every applicable
counterexample class has a proof, the applicable deterministic checks are green, and the residual uncertainty is
explicit. Do not stop because coverage, mutation score, or test count reached a pleasing number.

If re-enfolding leaves no justified tracked change, complete a local no-change result: preserve the
discriminator, falsified hypotheses, final whole, and residual uncertainty in the scratch ledger, then
use the pre-implementation candidate manifest to restore modified paths and remove only paths recorded as
absent before the run. Require the full status, including untracked files, and index to match the baseline
except explicitly enumerated scratch ledgers. Child dry-run ledgers remain in their dedicated worktrees
under their own contracts; record their paths/disposition. Do not create an empty commit, run change-only
gates/reviews, or open a PR.

## 5. Complete one exact tree when an artifact exists

Run affected checks during editing, then the full deterministic and free-threaded gates required by the
repository. Commit the final artifact before review.

Freeze a review packet containing the base/head/diff, supported scenario, invariant, accepted dossier and
human decision, affected owners, discriminator, scope guard, final scenario trace, mechanism-proof matrix,
validation, residual uncertainty, and follow-ups. Compute the diff bytes with `git diff --no-ext-diff
--no-textconv --binary BASE...HEAD`, record the SHA-256 tool, then hash the packet. Give two isolated
adversarial reviewers that
packet and withhold author rationale. Also include `.agents/rules/searching.md`, the shell-search ban,
permitted navigation surfaces, and `uv run` for Python—the safety envelope is not design rationale.
Reviewers are read-only: forbid file edits and `checkout`/`switch`/`stash`/`reset`/`commit`. Prefer a
separate disposable worktree per reviewer. Before and after each review, verify the current HEAD, packet
and diff digests, tracked worktree, and index; after all reviews, repeat those checks in the publication
worktree.

Record P0-P3 findings and the reviewed-packet digest. **Every launched reviewer must finish.** Fix every
P0/P1; resolve each P2 or obtain an explicit acceptance from the human owner; record P3. Any change to
tracked reviewed bytes or a frozen packet field invalidates all earlier approvals. Updating only the
separate git-ignored review-return table does not alter the reviewed packet.

If a reviewer cannot return, terminate it through the host and record the terminal failure; invalidate that
whole review generation and launch two fresh reviewers. A canceled/failed reviewer never counts, and a
still-running invocation never permits publication.

During implementation, invoke `contribute` prepare-only: diagnose, implement, and gate, but do not review,
push, or open a PR. The pipeline's two exact-packet reviews satisfy its review phase. On an outward path,
resume `contribute` publish-only to verify the unchanged head/packet/gates/reviews and perform PR packaging;
it makes no artifact edit and launches no new review unless that validation fails. Preserve residual risks
and follow-ups without smuggling them into the current scope.

Materialize the frozen packet and completion receipt from
[`references/packet.example.json`](references/packet.example.json) and
[`references/completion.example.json`](references/completion.example.json), then run
`uv run python .agents/skills/assurance-pipeline/scripts/verify_receipt.py <receipt.json>`. The pipeline is not
complete unless this validator exits zero. It fail-closes stage order, the human checkpoint,
prepare-only/publish-only separation, both completed read-only reviewers, and their pre/post binding to
the same head, diff, packet, tree, and index. It parses the packet contract and recomputes the current HEAD,
tree, index, canonical diff, frozen-packet digest, and full status including untracked files. The
`--self-test` mode accepts no caller receipt and validates only the checked-in smoke fixture. Local receipt
validation is tamper-evidence, not an external attestation that a human or command existed; host-issued
identity and execution evidence remain host responsibilities.

## Verify

`bash scripts/smoke.sh` (grep-free). Validate each real completion receipt with `scripts/verify_receipt.py`.
