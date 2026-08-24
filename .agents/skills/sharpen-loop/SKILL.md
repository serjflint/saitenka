---
name: sharpen-loop
description: 'Run or resume one Saitenka Sharpen audit: triage a module, measure its existing tests, propose a test-only improvement, apply the deterministic anti-lobotomization gate, obtain two isolated adversarial reviews, and record a dry-run or PR-ready result. Trigger on "run Sharpen", "sharpen the tests", "audit test quality", "mutation survivor heal", or requests to operate `.agents/sharpen`. Default to a ledger-only dry-run. Not for writing missing coverage or new feature tests; hand those to Grow or the `write-test` skill.'
metadata:
  project: saitenka
---

# Sharpen Loop

Run one bounded audit while preserving the loop's deterministic gates and reviewer isolation.

## Load the contract

Before taking action, read these files completely:

1. `.agents/sharpen/SPEC.md` — behavior and safety policy.
2. `.agents/sharpen/ADAPTERS.md` — host operations, response contracts, and provenance.
3. `.agents/sharpen/PROMPTS.md` — canonical author and reviewer payloads.

Read `.agents/sharpen/GUIDE.md` only when explaining the design or adjudicating an ambiguity.

## Choose the mode

- Treat an unspecified run as `openPr=false`: ledger-only dry-run, no push, PR, issue, or merge.
- Set `openPr=true` only when the user explicitly asks to open a PR.
- Use one module per run. Respect a user-pinned module only if triage says it is live.
- Consume an existing complete mutation DB. Never start a mutation campaign inside the run.

## Execute with Codex

1. Work in a clean, dedicated git worktree. Keep every command relative to that worktree.
2. Run selection, baseline checks, lint, gates, hashing, and ledger operations directly with shell tools from the repository root. Do not delegate deterministic commands to an agent.
3. Stop before authoring when triage has neither a target-grounded actionable finding nor a complete
   campaign survivor. Ledger a `dry-run` when the baseline is red/flaky, the open-PR exclusion is
   unavailable, or reviewer fidelity cannot be proven.
4. Invoke the author in a fresh isolated context. Give it only the selected module, mapped test file, applicable axis evidence, rubric, scope guard, and prior gate bounce on retry. Capture the host-returned invocation id.
5. Validate the author's response against `.agents/sharpen/contracts.json`, then run the objective gate
   directly. Off the mutation allowlist, an assertion replacement/removal must pass the exact-text
   preservation witness. Retry at most three times, reverting only the patch created by the failed attempt.
6. Invoke the skeptic in a second fresh isolated context with only factual WHAT plus DIFF. Do not include the author's rationale or claimed kills. Capture its invocation id.
7. If the skeptic says `UPHELD`, invoke the judge in a third fresh isolated context with the same WHAT plus DIFF. Do not include the first review or its grounds. Capture its invocation id.
8. Ship only when both independent reviewers say `UPHELD`. Prefer cross-family reviewers when the host
   roster offers them, per `CONTRIBUTING.md` §4; never weaken isolation when unavailable.
   Either `REFUTED` means drop the candidate.
9. On a refutation, inspect only that reviewer's evidence and `better_fix`. If it proves the objective is
   valid but the candidate is too local, record and report the smallest evidence-backed alternative.
   Never apply it in the same run; route `outside-sharpen` work for separate maintainer authorization.
10. When `openPr=true`, run `uv run poe all` after the reviews and before opening the ready PR. A failure
    forces a dry-run/no-PR result.
11. Record actual invocation ids, both individual verdicts, and the final verdict. If the host exposes no invocation identity or cannot create fresh contexts, record `state: dry-run` and no valid review block.
12. In dry-run mode, revert the test edit after capturing its diff; leave only the ledger append.

Use the host's equivalent of a context-free subagent invocation. In Codex environments that expose `spawn_agent`, use `fork_turns="none"`; run author, skeptic, and judge sequentially because they share the worktree.
Optional LSP navigation may locate symbols and callers; never invoke or depend on the infrastructure-only
`pyrefly-lsp` skill.

## Handoffs

- Missing mutation data is a `test-adequacy` prerequisite outside the run. A survivor/crasher/
  counterexample returns as a coordinate for a property plus pinned `@example` and an adequacy rerun.
- Source, tooling, config, dependency, or product changes route to `contribute` as a separate PR. An
  ordinary test-only Sharpen PR remains in this loop.
- Repeated reflection evidence that needs external prior art routes to `research`, never into a candidate run.

## Preserve scope

- Edit only the selected existing test file. Do not change source, tools, configuration, mutation targets, or dependencies.
- Return a blocker verbatim instead of working around it outside the target test.
- Use the target file's saved pre-attempt diff when reverting; do not discard unrelated work.
- Never merge. The maintainer remains the final gate.

## Verify the run

Before reporting success, confirm:

- the mapped baseline tests were green before the edit;
- the anti-cheat arm passed, plus efficacy replay or the required preservation witness;
- every temporary mutation restored its pre-run bytes exactly;
- author, skeptic, and judge identities are distinct on an upheld path;
- the ledger's `review.verdict` is the final two-reviewer verdict;
- a refutation-derived alternative is recorded as a hand-off, never as an UPHOLD or an automatic retry;
- every skipped axis is listed with its reason;
- a dry-run produced no outward action and left no test edit behind.
- `poe all` passed after review on every ship path.
