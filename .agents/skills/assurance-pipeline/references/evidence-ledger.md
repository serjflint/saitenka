# Evidence ledger

Copy this template into the run's scratch area (`vibe/`, git-ignored). Keep durable policy in this skill;
keep run-specific hashes, counts, commands, and dead hypotheses in the ledger.

## Boundary

- Base commit:
- Worktree / branch:
- Supported scenario:
- Product invariant:
- Affected owners:
- Scope guard:
- Outward mode: local-only / ready PR
- Entry state: fresh inquiry / accepted dossier
- Accepted dossier / human decision:
- Final change type:
- Contribution mode: prepare-only / publish-only / not applicable
- Pre-implementation full status (`--untracked-files=all`) and index digest:
- Candidate path manifest (path + absent-before or exact digest):
- Explicit scratch-ledger exclusions:

## Whole-system model

| Claim | Declared | Enforced | Observed | Falsifier |
| --- | --- | --- | --- | --- |
| | | | | |

## Hypothesis log

| Hypothesis | Deterministic discriminator | Base result | Disposition |
| --- | --- | --- | --- |
| | | | confirmed / falsified / unresolved |

Preserve falsified hypotheses: they are evidence against repeating an attractive but wrong path.

## Routing

| Finding | Route | Eligibility / freshness evidence | Why this is the smallest primitive | Invocation / artifact |
| --- | --- | --- | --- | --- |
| | Grow / Sharpen / adequacy / contribute / no change | Grow/Sharpen receipt is coordinate-only; package proof is fresh | | |

## Mechanism proofs

| Mechanism / guarantee | Baseline/branch obligation | Liveness | Evidence subtraction | Preservation / integration | Restore digest |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

Select the obligation from `contract.md` before filling the table. For every blank or skipped cell, record
why the proof is not applicable to that change type and mechanism. Record canonical instrument replay
separately for mutation survivors, fuzz crashers, and symbolic counterexamples.

## Re-enfolding checkpoints

| Trigger | What changed in the whole-system model | Scope decision | Next discriminator |
| --- | --- | --- | --- |
| initial inquiry | | | |
| candidate result | | | |
| reviewer finding | | | |

## Deterministic gates

| Final commit | Gate | Result | Relevant count / artifact |
| --- | --- | --- | --- |
| | affected | | |
| | full deterministic | | |
| | free-threaded | | |

## Frozen review packet

Copy the following into a separate immutable scratch file and hash it before launching reviewers:

- base, head, and diff digest;
- supported scenario and product invariant;
- accepted dossier and human design decision;
- affected owners, discriminator, and scope guard;
- final scenario trace and mechanism-proof matrix;
- validation evidence, residual uncertainty, and follow-ups.

Canonical diff command: `git diff --no-ext-diff --no-textconv --binary BASE...HEAD`
SHA-256 tool/version:

Frozen reviewed-packet path and digest:

## Review returns

Review generation:

| Reviewer invocation | Model / isolation | Pre/post revision, digest, tree, index | Verdict | Findings / resolution / human P2 owner |
| --- | --- | --- | --- | --- |
| | read-only / disposable worktree | | | P0-P3 |

List every launched reviewer, including timeouts or failures. If any frozen packet field changes, mark
every earlier row invalid and launch fresh review. Appending returned verdicts to this separate git-ignored
table does not change the packet. Only the human owner may accept a P2; record that decision.

If an invocation cannot return, record its host-confirmed terminal status, invalidate the whole generation,
and launch two new reviewers. A failed/canceled reviewer does not count.

Publication-worktree verification after all reviewers (HEAD + packet/diff digests + tracked tree + index):

## Final re-enfolding

- Final scenario trace:
- Applicable counterexample classes and proofs:
- Falsified hypotheses retained:
- Residual uncertainty:
- Follow-ups outside scope:
- Evidence-saturation rationale:
- No-change restoration / full-status / index result:
- Child dry-run ledger paths and disposition:
- Final disposition: no change / local proof / ready PR / blocked
- Completion receipt path and `scripts/verify_receipt.py` result:
