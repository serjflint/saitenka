# The Grow Loop — a guide

**Status:** Informational · **Audience:** anyone comfortable with plain `pytest` · **Scope:** what the
Grow loop is, why it exists, how the four-arm gate works, and the reasoning behind each choice.

This is the *reader's guide*. The terse, agent-facing process spec is [`SPEC.md`](SPEC.md); the design
SSOT is `vibe/grow-loop-plan.md`; the audit trail is `.ledger.grow.jsonl`. If you have five minutes, read
the **Abstract** and **Example** sections.

## Abstract

Its sibling, the **Sharpen loop**, *fixes* the tests you already have. The **Grow loop** writes the ones
you're **missing** — but not by chasing line coverage. It targets a subtler inadequacy: code that is
*covered*, and even mutation-clean, yet was never exercised for the **scenarios, combinations, and
configurations** that actually occur. It runs at rest, one gap per run, and arrives at a human with a
small, evidence-backed pull request. It never merges on its own.

Two ideas do the heavy lifting:

1. **A new test that is green proves nothing.** So every grown test clears a *deterministic gate* that
   asks "does this test actually add power — is it load-bearing, falsifiable, and newly-exercising — or is
   it vacuous / redundant?" — not "does it pass?"
2. **The judgment is the product, not the LLM.** The instruments (mutation replay, an AST oracle-liveness
   check, coverage-context deltas, a deterministic thread-schedule replayer) are cheap and boring; the
   human merge gate is final. The model only proposes.

## Motivation — why "coverage" is only a lower bound

Coverage answers "did this line ever run?" That's necessary but weak. Consider a tooltip that renders at a
`scale` factor:

```python
def render(text, scale):
    if scale > 1.0:
        return crisp(text, scale)   # the hi-dpi path
    return soft(text)               # the 1080p path
```

A suite that only ever calls `render(text, 1.0)` has **100% line coverage of the branch it takes** and can
be **mutation-clean on that branch** — yet the entire `crisp` path is untested. Line coverage of the
*taken* branch says nothing about the *untaken configuration*. This is exactly how a real regression
shipped here: everything was green at scale 1.0, where the crisp path no-ops. Grow's target is that
converse — *covered but under-specified* — which neither pass/fail nor coverage percentage detects.

The second class is **combination**: feature A works, feature B works, but A-after-B was never tried. A
navigated tooltip view worked; a key-gated feature worked; the two combined broke. No single-feature test
saw it. Grow reasons about scenarios and their *sequences*, not lines.

## Example — the worked output

The canonical Grow artifact in this repo is `overlay/tests/test_tooltip_statemachine.py`. A single-action
test (`test_scale_boundary.py`) already proves the render↔hit-test **agreement oracle** for one action:
every drawn element's displayed centre round-trips back to that element through the real hit path. Grow
made it **stateful** — a `RuleBasedStateMachine` drives the real controller through arbitrary
`hover / scroll / navigate / back / open_nested / resize` sequences and asserts, after *every* step, that
the oracle still holds and the model matches the impl. That catches the interaction regressions a
single-action test can't. It ships with a permanent negative control (`test_the_agreement_oracle_has_teeth`)
that proves a deliberately-drifted transform *does* mis-hit — so the invariant is falsifiable, not vacuous.

The other worked artifact is `overlay/tests/test_cache_race.py`: a `blanket`-scripted deterministic
regression for a cache eviction race, with a negative control that fails against the unguarded variant.

## Background — the four arms, for the pytest-only reader

A grown test must clear every *applicable* arm of the deterministic gate (`overlay/tools/grow_gate.py`).
Each arm is a pure function over an injected primitive, so the gate logic is unit-tested without a real
subprocess (`tools/test_grow_gate.py`).

- **Arm 1 — property-mutant (load-bearing + genuine growth).** We plant a small mutation that *encodes the
  scenario's violation* (e.g. "the crisp path silently degrades to soft"). The grown test must KILL it —
  proving teeth and relevance — AND the mutant must have SURVIVED the pre-existing suite — proving the
  behaviour was genuinely uncaught, not a redundant restatement. Mutation here is one signal, not the
  loop's purpose.
- **Arm 2 — oracle-liveness (falsifiable, not vacuous).** We negate the test's *own* assertions, one at a
  time, and re-run it: a live assert flips the test red. An assert that stays green when negated is
  swallowed (a `try/except` ate it), unreachable (after an early return), or a tautology (`assert x == x`)
  — it asserts nothing. A static count of `assert` statements can't see this; running the negation can.
- **Arm 3 — context-delta (newly-exercised).** The grown test must light a `coverage.py` line the existing
  suite never ran — the dead-configuration detector. Necessary but not sufficient alone: reaching a line
  isn't checking it (that's arms 1–2).
- **Arm 4 — concurrency (race fails-on-bug, passes-on-fix).** A race has no coverage-delta and no ordinary
  mutant. Instead the grown test ships as a *pair*: a regression that passes against the guarded code, and
  a negative control that fails against the unguarded variant. `blanket` (MIT, out-of-process tooling)
  scripts the exact thread interleaving via a bytecode injector, so the race is *deterministic* — no
  stochastic stress that flakes 1-in-200. Because `blanket` controls the schedule, the logical race
  reproduces even under the GIL, so the gate needs no special build.

## Discovery — where the loop points first

Triage (`overlay/tools/grow_triage.py`) ranks the **product** of two axes: **value** (import fan-in +
churn) × **under-specification** (the missing-public-seam proxy + optional survivors + optional dead
coverage-contexts). The product matters: a valuable but fully-specified module and an under-tested dead leaf
both score zero — you want the intersection. From the top module, an AutoCover-style *scenario map* (intents
× edges × invariants, minus what the coverage baseline already exercises) yields orphan scenarios, each fed
to the four-arm gate.

## Outcomes — a missing test is the common case, not the only one

Grow discovers *behaviour*. Four dispositions, classified up front in every PR:

1. **coverage-only** — the new test pins already-correct behaviour (the default).
2. **latent bug** — the test went red on pristine code → a real defect; file/fix a product issue
   (green-trunk: land test+fix together, or withhold the assertion and file high-severity).
3. **robustness/spec gap** — a robustness fix plus its guard.
4. **design/observability gap** — there's no public seam to assert the behaviour → a refactor
   recommendation.

## Why a ledger, and why the key is semantic

The loop must *terminate*: once a gap is closed, it must stay closed under unrelated churn, and reopen only
when its own target actually changes. A naive line-number key drifts on any edit above the target and
re-opens closed gaps forever. So the gap identity is `hash(source, target_symbol, dimension)` plus a
content-hash of the **target symbol's AST source** — not the whole module. Unrelated edits leave it closed;
a real change to the target reopens it. This is proven in `vibe/proto_grow_ledger.py` and locked by
`tools/test_grow_ledger.py`.

## Anti-bloat — Grow's characteristic hazard

Where Sharpen risks *lobotomising* a test, Grow risks *flooding* the suite. Four controls: extend-before-add
(append a `PROFILES` row / a `parametrize` case rather than a new file), hide-covered-code from the author
(so it can't restate what's tested), redundancy-as-a-flag (never an auto-reject — subsumption misses oracle
strength), and a suite-cost budget (a slow grown test carries `integration`).

## Fidelity and the human gate

The author is not its own reviewer. Shipping requires two *independent* UPHOLDs — an isolated skeptic then
an isolated judge, each seeing only the factual *what* + the *diff*, never the author's reasoning (the
SycEval trap: a persuasive rationale *increases* sycophantic agreement). Every run records a review
provenance block; no valid block ⇒ `dry-run`, never a PR. The maintainer approves every merge.

## References

- Design SSOT and full research provenance: `vibe/grow-loop-plan.md`, `vibe/research/grow-loop-research-*`.
- Sibling loop: `.agents/sharpen/{GUIDE,SPEC}.md` — Grow mirrors its structure and shares the fidelity
  rationale (SycEval, FAccT 2025, arXiv:2502.08177) and the isolated-review architecture.
- Prior art (gated): Meta ACH (arXiv:2501.12862, concern-specific mutants), Uber AutoCover (ICSE-SEIP'26,
  scenario coverage), `larryhastings/blanket` (deterministic thread-schedule replay).
