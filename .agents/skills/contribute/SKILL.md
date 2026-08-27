---
name: contribute
description: >-
  Drive a change big enough to warrant its own PR — from diagnosis to a review-ready pull
  request — the house way: the ordered loop this repo and its upstream forks (repowise) use:
  restore context, diagnose to root cause offline, PoC to prove tractability, fresh
  adversarial review as a gate, then a lean ready PR by default. Use for a multi-step
  change: fix a bug, implement or prototype a feature, open a PR, contribute upstream, file
  an issue and PR, or "do a proper PR/review loop". Sequences what the gate and packaging
  skills don't: offline proof, optional issue handoff, isolated adversarial review, and which
  gate applies (this repo vs external). Defers to
  CONTRIBUTING.md for the pass/fail gate and pr-ticket-describe for the issue/PR text. NOT for
  a one-line edit or a read-only what/why question (just answer, or use repowise/LSP); NOT for
  writing one test (use write-test), running the gate (dev-gate / poe all), or sharpening
  existing tests (sharpen-loop).
metadata:
  project: saitenka
---

# contribute

The *order and the moves* for landing a change. The pass/fail **criteria** live in the
gate (`CONTRIBUTING.md`); the issue/PR **text** lives in `pr-ticket-describe`. This skill is
the connective procedure neither of those encodes. Walk it in order, but **interleave** — a
PoC feeds the review, a review finding sends you back to the design.

## Which gate applies

- **A change in this repo** → the repo's [`CONTRIBUTING.md`](../../../CONTRIBUTING.md) (the
  saitenka gate: `Fake*` seams, `poe all`, `repowise risk`, the Sharpen loop, the saitenka
  failure-pattern table) + `AGENTS.md`.
- **A change in an external repo / upstream fork** (e.g. repowise) → that repo's own
  contributing guide first, then [`references/review-gate.md`](references/review-gate.md) —
  the generalized gate with the full evidence record and the **LLM/RAG adversarial patterns**
  (prompt-trust, grounding-alias, provenance-inflation) the saitenka-local one doesn't carry.
  It is a vendored copy for self-containment; the SSOT is `~/workspace/CONTRIBUTING.md`.

## The loop

1. **Restore context first.** Memory, Basic Memory, the relevant issues/PRs. Don't diagnose
   from a blank slate — half the answer is usually already recorded.
2. **Diagnose to root cause — grounded and offline.** The signature move: reconstruct the
   real production input and run the pure function/seam offline until the cause is
   deterministic. **Name the class before proposing a fix** — selection vs grounding, symptom
   vs cause, correctness vs perf — because the fix differs by class. A cause you can't
   reproduce offline you haven't found yet; keep going. **Before naming a cause, open the
   artifact that would falsify it** — the signature, the AST classification, the API field. A
   clean correlation is more suspicious, not less. **State the discriminator in the finding**, so a
   later reader can check the claim without redoing the work.
3. **Check the design against `ARCHITECTURE.md` before writing it.** Its "Composition and
   extension seams" section says where a feature is *supposed* to plug in — which layer, which
   registration, which gate will refuse you — and `docs/contributing/runtime.md` says how. A design
   that needs a new host member or a `SessionController` parameter is not blocked by a gate being fussy; it has
   not found its layer. If the code genuinely wants a shape the document forbids, that is a finding
   about the document — say so and update it in the same PR, rather than routing around it.
4. **Smallest coherent design, then a PoC that proves tractability.** Prefer an existing seam
   over a new dispatcher condition. The PoC's offline verification — reconstruct the input,
   assert the module's **documented invariants**, and **measure the churn / blast-radius** —
   *is* the regression test (fails on base, passes on the branch). What the PoC prints is what
   the reviewer reads next.
   For an ownership change, use the canonical authority-reachability lens linked from the repository gate.
   Protect the semantic seam without freezing private layout or adding ceremonial indirection.
5. **Fresh adversarial review is a gate, not a formality.** Spawn a subagent that did **not**
   author the change; give it only the base, the diff, and the claimed validation — **no
   rationale**. Have it verify independently and classify P0–P3. Fix every P0/P1; resolve or
   explicitly accept each P2; **any code change invalidates the pass** → re-review the exact new
   tree/diff, or a pinned commit when review happens after commit. Full criteria and pattern tables are in
   the applicable gate (§5 + the adversarial tables). **Prefer cross-family when available:** route the
   reviewer to a different model family
   when the host roster supports it, because correlated blind spots are less useful than independent
   disagreement. Do not pretend this is always available or make it a validity condition: record the
   limitation and retain two genuinely isolated reviews. Deterministic gates such as `poe all` remain
   mandatory and family-immune. The canonical policy is `CONTRIBUTING.md` §4.
6. **Package a lean, ready PR by default.** In this single-maintainer repo, a clean gated change is
   ready to merge; draft status adds no value by itself. Use a draft only for genuine WIP, early CI,
   active collaboration, or an explicit request. File a separate issue only when the problem needs
   durable tracking beyond this PR; when used, make it self-contained and let the PR point to it rather
   than restating it. Carry only why / design / trade / test-plan. Text mechanics → `pr-ticket-describe`.
   **Humans talk; agents build** — the human posts the issue/PR discussion and makes the
   hold-vs-post and design calls; you produce the artifacts and the review, not the voice.

## Anti-patterns

- Proposing a fix before the cause reproduces offline.
- Reporting a mechanism you inferred from a matching pair of totals or a shared call shape —
  that is a correlation, and the artifact that would falsify it was one command away.
- "Verifying" a pure-function change by eyeballing output instead of asserting its invariants
  and measuring churn.
- Letting the reviewer see your rationale — it must judge the artifact, not your story.
- A permanent draft used as a generic safety ritual after the change is already review-ready.
- A PR body that re-describes an issue, or an issue that needs the reader to know the repo
  internals.
- Posting the issue/PR discussion yourself, or auto-deciding a maintainer's design call.

## Verify

`scripts/smoke.sh` — asserts the skill's own structure and that the gate/skills it points at
still exist.
