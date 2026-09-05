---
name: sharpen-loop
description: >-
  Run one bounded Saitenka Sharpen audit: select one concrete weakness in an existing test, improve it
  without losing detection power, and obtain isolated review for a PR-ready artifact. Use for "run
  Sharpen", test-quality audits, or mutation-survivor healing. Missing tests route to grow-loop.
metadata:
  project: saitenka
---

# Sharpen Loop

Read `.agents/sharpen/SPEC.md` completely, then run one existing-test audit.

## Procedure

1. Default to exploration with no outward action. Open a PR only when the user authorizes it.
2. Use a clean worktree and select one idle module with either a complete mutation survivor, a
   target-grounded actionable lint finding, or a concrete brittleness/redundancy witness.
3. Confirm the mapped tests are green. Edit only the selected existing test; missing behavior routes to
   Grow and production/tool changes route to `contribute`.
4. Run focused affected tests and `sharpen_gate.py anticheat` for every assertion edit, then the relevant
   objective proof:
   - mutation: kill the named survivor and retain relevant prior kills;
   - off-allowlist assertion change: old and new tests both kill a focused preservation witness;
   - marker/structure fix: targeted lint plus unchanged behavior.
5. For a PR-ready result, obtain two isolated read-only adversarial reviews of the exact final diff.
   Reviewers receive the coordinate, claimed observable behavior, diff, and validation only.
6. Offer a genuine improvement as a PR. Suppress cosmetic or metric-only churn.
7. Use `sharpen_ledger.py append` only for durable duplicate-prevention: PR/landed, Grow handoff, product
   issue, or unsharpenable. It derives the mapped tests and content hash. Do not write receipts for
   no-candidate, bounce, or clean exploration.
8. Follow the repository contribution gates for a PR. Never merge.

Use context-free subagents for reviewers when available. Deterministic commands stay with the root agent.

## Reflection

Record deliberate residue in the result. Run a process retrospective separately when requested or after
repeated failures; it never gates completion.

## Verify

Run `bash .agents/skills/sharpen-loop/scripts/smoke.sh`.
