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

| Finding | Route | Why this is the smallest primitive | Invocation / artifact |
| --- | --- | --- | --- |
| | Grow / Sharpen / adequacy / contribute / no change | | |

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

| Reviewer invocation | Model / isolation | Reviewed commit | Verdict | Findings / resolution |
| --- | --- | --- | --- | --- |
| | | | | P0-P3 |

List every launched reviewer, including timeouts or failures. If tracked reviewed bytes change, mark every
earlier row invalid and launch fresh review on the new commit. Appending verdicts to this git-ignored
scratch ledger does not change the reviewed artifact.

## Final re-enfolding

- Final scenario trace:
- Applicable counterexample classes and proofs:
- Falsified hypotheses retained:
- Residual uncertainty:
- Follow-ups outside scope:
- Evidence-saturation rationale:
- Final disposition: no change / local proof / ready PR / blocked
