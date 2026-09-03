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

| Finding | Route | Eligibility / trust evidence | Why this is the smallest primitive | Invocation / artifact |
| --- | --- | --- | --- | --- |
| | Grow / Sharpen / adequacy / contribute / no change | idle/exclusion, baseline, proof, restoration, reviews | | |

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

## Exact-head reviews

Frozen review-input digest (base + head + diff + invariant + scope + validation):

| Reviewer invocation | Model / isolation | Reviewed digest | Verdict | Findings / resolution / human P2 owner |
| --- | --- | --- | --- | --- |
| | | | | P0-P3 |

List every launched reviewer, including timeouts or failures. If any frozen review input changes, mark
every earlier row invalid and launch fresh review. Appending returned verdicts to this git-ignored review
table does not change the frozen inputs. Only the human owner may accept a P2; record that decision.

## Final re-enfolding

- Final scenario trace:
- Applicable counterexample classes and proofs:
- Falsified hypotheses retained:
- Residual uncertainty:
- Follow-ups outside scope:
- Evidence-saturation rationale:
- Final disposition: no change / local proof / ready PR / blocked
