---
name: grow-loop
description: >-
  Run one bounded Saitenka Grow audit: find one missing scenario, add one focused test, prove its oracle
  has new detection power, obtain isolated review for a PR-ready artifact, and offer genuine findings.
  Use for "run Grow", "grow tests", or missing scenario coverage. Not for changing existing assertions
  (sharpen-loop), adequacy campaigns (test-adequacy), or production fixes (contribute).
metadata:
  project: saitenka
---

# Grow Loop

Read `.agents/grow/SPEC.md` completely, then run one additive audit.

## Procedure

1. Default to exploration with no outward action. Open a PR only when the user authorizes it.
2. Use a clean worktree and select one idle module. Treat triage and optional mutation data as hints;
   verify the chosen scenario against source and mapped tests.
3. Before authoring, query `grow_ledger.py status` for the chosen semantic coordinate. Skip
   `closed-current`, `filed`, and `unclosable`.
4. Use the `write-test` skill to choose the tier, seam, extension point, and oracle. Add one test or
   extend one existing case without altering prior assertions.
5. Run the mapped baseline and focused changed test. A red-on-pristine oracle is a product finding and
   routes to `contribute`.
6. Run `grow_gate.py additive` and `liveness`. For a scenario, require either a focused old-survives /
   new-kills mutant or a real new coverage path. For concurrency, require the guarded test and unguarded
   negative control.
7. For a PR-ready result, obtain two isolated read-only adversarial reviews of the exact final diff.
   Reviewers receive the target, claimed observable behavior, diff, and validation only.
8. Offer a genuine finding as a PR or issue. Do not revert it merely to keep the main worktree clean.
9. Append a ledger record only for durable duplicate-prevention: PR/landed, issue-filed, or unclosable.
   Do not write receipts for no-candidate, bounce, or clean exploration.
10. Follow the repository contribution gates for a PR. Never merge.

Use context-free subagents for reviewers when available. Deterministic commands stay with the root agent.

## Reflection

Run a separate retrospective only when requested or after repeated false passes/bounces. It is advisory
planning work, never a gate or ledger receipt.

## Verify

Run `bash .agents/skills/grow-loop/scripts/smoke.sh`.
