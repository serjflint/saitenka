---
name: grow-loop
description: >-
  Run or resume one Saitenka Grow audit: find an orphan scenario, choose the test kind and
  oracle through write-test, add one focused test, run the deterministic growth gate, obtain
  two isolated adversarial reviews, and record a dry-run or PR-ready result. Trigger on "run
  Grow", "grow the tests", "find missing test coverage", or requests to operate `.agents/grow`.
  Default to a ledger-only dry-run. Not for strengthening an existing assertion (sharpen-loop),
  launching mutation/fuzz/crosshair campaigns (test-adequacy), or product/tooling changes
  (contribute).
metadata:
  project: saitenka
---

# Grow Loop

Run one bounded additive test-growth audit while preserving objective gates and reviewer isolation.

## Load the contract

Before taking action, read completely:

1. `.agents/grow/SPEC.md` — behavior and safety policy.
2. `.agents/grow/ADAPTERS.md` — host operations, response contracts, and provenance.
3. `.agents/grow/PROMPTS.md` — canonical role payloads.
4. The `write-test` skill — choose the tier, fake/real seam, extension point, and oracle family.

Read `.agents/grow/GUIDE.md` only to explain the design or resolve ambiguity.

## Choose the mode

- Unspecified means `openPr=false`: ledger-only dry-run, no push, issue, PR, or merge.
- Set `openPr=true` only when the user explicitly requests outward action.
- Use one orphan gap per run; respect a pinned module only when deterministic triage says it is live.
- Consume complete adequacy artifacts only. Missing mutation data is a `test-adequacy` prerequisite,
  never a campaign launched inside Grow.

## Execute with Codex

1. Work in a clean dedicated worktree; keep commands relative to it.
2. Run triage, baselines, objective gates, hashing, and ledgers directly from the repository root.
3. Apply the `write-test` decision tree. Pass only the selected tier, seam, extension point, oracle,
   gap coordinate, scope guard, and prior bounce to a fresh isolated author.
4. Permit additive test edits only. Any changed/removed existing assertion routes to Sharpen.
5. Run every applicable deterministic Grow arm and verify temporary mutations restore exact bytes.
   For a scenario, liveness is mandatory and growth-adhoc/context-delta are alternative growth proofs;
   a context bounce does not override a passing scenario mutant. Recompute disposition from the
   individual results instead of trusting an agent-supplied aggregate.
6. Invoke skeptic and then judge in separate fresh contexts. Give each factual WHAT plus DIFF, never
   author rationale or the other review. Two UPHOLDs are required.
7. Prefer a different model family for reviewers when the host roster offers one. Isolation is
   mandatory; cross-family routing is recommended, not required.
8. When `openPr=true`, run `uv run poe all` after reviews and before opening a ready PR. Gate failure
   forces a dry-run/no-PR result. Never merge.
9. Record real invocation ids, verdicts, applied arms, skipped-axis reasons, and final disposition. If a
   completed live-module scenario map finds no orphan, append a no-gap module audit so unchanged evidence
   is not inspected again; a no-live selection records nothing.
10. Revert the test edit in dry-run mode so only the ledger append remains.

Use `spawn_agent` with `fork_turns="none"` when available. Run author, skeptic, and judge sequentially
because they share a worktree. Optional LSP navigation may locate symbols/callers; never invoke or depend
on the infrastructure-only `pyrefly-lsp` skill.

## Handoffs

- Survivor/crasher/counterexample → `test-adequacy`, then author a property plus pinned `@example`.
- Product bug, source fix, loop-tool change, dependency, or config work → `contribute` as a separate PR.
- Repeated reflection evidence needing external prior art → `research` outside the candidate run.
- An ordinary test-only Grow PR stays in this loop; do not nest a full contribution workflow around it.
- A completed dry-run receipt may feed `assurance-pipeline` before feature work begins. Return the scenario,
  liveness, restoration, and growth evidence; the reverted edit is a coordinate, not package-tree content.

## Verify

Before success, confirm the mapped baseline was green, the edit is additive, every applicable arm passed,
temporary bytes restored, reviewer identities are distinct, `poe all` passed on a ship path, and a dry-run
left no test edit or outward action.

Run `bash scripts/smoke.sh` from this skill directory or the repository-root equivalent.
