# Grow Loop — process spec

> New here? Read [`GUIDE.md`](GUIDE.md) first — a self-contained explainer (scenario/specification
> adequacy, the four-arm gate, worked example, references) for a reader who knows only plain `pytest`.
> This file is the terse process spec and durable design record the loop executes against; do not
> re-describe specifics that live in the deterministic tools or their tests.

A deliberate, idle-time loop that **writes the missing tests** — reasons over *covered* code to close
under-specified scenarios, combinations, configurations, and invariants, and files a product issue when
the gap is a real defect rather than a coverage hole. It runs at rest, proposes one gap per PR with
evidence, and never merges — a human is the final gate.

This is the **Grow** loop. Its sibling **Sharpen** loop (fix *existing* tests) is out of scope here;
the two share one handshake (see *Sharpen hand-off*). The line between them is **direction**, not the
presence of mutation: a new test that ADDS power → Grow; an edit that must not DROP power → Sharpen.

## Why a loop and not a checklist

Missing *coverage* ("this line never ran") is only a lower bound on inadequacy. The real target is the
**converse**: code that is covered — even mutation-clean — but never exercised for its scenarios,
combinations, or configurations. Both motivating regressions were in covered code (green at scale 1.0
where the hi-dpi crisp path no-ops; a navigated view never combined with a key-gated feature). A checklist
of known gaps would Goodhart instantly; the loop instead ranges over the whole covered core, and the known
gaps are only **seed + ROI evidence + a held-out backtest corpus**, never the target list.

## Core principles (settled with the maintainer — do not relitigate)

- **General, not a gap checklist.** Triage ranges over the covered pure core; the invariant catalog is
  OPEN (seed families extended per target).
- **Mutation is one welcome signal, not the driver.** It is arm 1 of the gate, not the loop's purpose.
- **Four outcome classes** — Grow discovers *behaviour*; a missing test is the most common, not the only,
  output:
  1. **coverage-only** → a new test pinning already-correct behaviour (the default; ledger `closed`).
  2. **latent bug** (red on pristine) → file/fix a product issue (mirrors Sharpen's source-bug policy,
     reversed) — a grown test must be GREEN on pristine code, so a red one is a defect, not a grow.
  3. **robustness / spec gap** → a robustness fix + its guard.
  4. **design / observability gap** (no public seam to assert X) → a refactor recommendation.
- **Assert oracles, not pixels** (platform-independent). **GPL/LGPL is fine as out-of-process tooling,
  never in the shipped `saitenka` dependency graph** (`blanket` sits in the opt-in `grow` dep group).

## The deterministic teeth-gate — four arms (`tools/grow_gate.py`)

The mirror of Sharpen's anti-lobotomization gate, reversed: it proves an ADDITION adds power. A green new
test proves nothing. An ordinary scenario/config gap clears arm 2 plus either arm 1 or arm 3; arm 4
replaces that composition for a concurrency gap. No LLM decides the disposition. Each arm is a pure
function over an injected primitive, unit-tested in `tool_tests/test_grow_gate.py`.

| # | Arm | Proves | Mechanism |
|---|-----|--------|-----------|
| 1 | **property-mutant** | load-bearing + genuine growth | a scenario-encoding mutant must be KILLED by the grown test AND have SURVIVED the existing suite (survives-old ⇒ not redundant). `growth_gate` uses `sharpen_gate` cosmic-ray replay (the 4 `poe mutate` targets); `growth_adhoc_gate` generalises it to ANY module via an author-supplied one-line text mutation — so arm-1 runs off the allowlist. **The STRONG growth proof for a `scenario` gap** (arm-1 OR arm-3 — see the honest-scope note). |
| 2 | **oracle-liveness** | falsifiable, not vacuous | negate the grown test's OWN asserts one at a time → each must flip it red; a static trivial-check rejects `assert True` / `x == x`. A `pytest.raises`/`warns` block counts as a live oracle (not `no_asserts`). Catches swallowed / unreachable / tautological asserts a static count can't. |
| 3 | **context-delta** | newly-exercised | the grown test lights a `coverage.py` line the existing suite never ran (the dead-config detector). The OLD baseline MUST `--deselect` the grown test, or an extend-before-add edit collapses the delta to ∅ (false bounce). Alternative to arm 1 and insufficient without arm 2 (reached ≠ checked). |
| 4 | **concurrency** | race reproduces unguarded, prevented guarded | a PAIR of PASSING tests: a regression (the guard prevents the bug under the forced schedule) + a self-certifying negative control that unguards a throwaway instance and asserts the bug REPRODUCES (`blanket` scripts the interleaving; GIL-agnostic). Both pass; the teeth are the control's own falsifiable assertion, confirmed by running arm-2 liveness on the control. |

**Genuine-growth proof = arm-1 OR arm-3 (they are ALTERNATIVES, not both-required).** The reframe is
*covered-but-under-specified*. Arm-1 (a killed scenario-mutant that survived the old suite) is the STRONG
proof and works for the flagship covered-but-under-specified class. Arm-3 (a newly-lit line) is the
ALTERNATIVE for a dead-config / uncovered-line gap that has no clean one-line mutant. A `scenario` gap
passes on EITHER; requiring both is wrong — **arm-3 is LINE-level, so a covered-but-under-specified BRANCH
of an already-covered line (e.g. an untested arm of a ternary) lights no new line and bounces arm-3, though
arm-1 proves it** (found live: `panel/rows.py::header_add_rect(speak_button=False)` — arm-1 PASS, arm-3 BOUNCE).
So the gate is: additive AND liveness AND (arm-1 OR arm-3). A gap with NEITHER has no growth proof → bounce.
(Future refinement: make arm-3 branch/arc-aware so it corroborates branch gaps too.)

A separate **Grow↔Sharpen boundary check** (`additive_gate`) enforces adds-only assert nodes — see
*Extend-vs-add*. It is NOT `sharpen_gate.anticheat_diff` (which only flags a specificity drop).

Discovery-tier (feeds arm 4, not a gate): TSan `--with-thread-sanitizer` + `pytest-run-parallel`
(stochastic, needs a no-GIL build). Deferred/optional: checked-coverage (pyChecco, abandoned; the
C-extension boundary defeats it here).

## Triage — value × under-specification (`tools/grow_triage.py`)

Where Sharpen SUMS its signals, Grow ranks the **product** of two axes — a target is worth growing only if
it is BOTH valuable AND under-specified; either axis at zero zeroes the score (a fully-specified hot module
and an under-tested dead leaf are both skipped). Every component is a printed column, never just a scalar.

- **value/risk:** ruff-analyze fan-in · churn (recency proxy — NOT centrality; repowise `get_risk` is the
  documented, still-unwired centrality input).
- **under-specification:** the **coverage-context signal** (uncovered + weakly-covered lines — those run by
  ≤1 test — produced by `grow_contexts.py` → `--contexts-json`). When supplied it DOMINATES the axis; the
  private-attr seam proxy drops to a tiebreak (it scales with test VOLUME, not adequacy — review C5).
  The v2 context artifact carries executing test nodeids; **TESTLESS** requires neither a static
  attribution nor an executed context. Also un-killed survivors (`--survivors-json`). An untested module is a candidate at
  under-spec 1.0. With no real signal supplied, the seam proxy is used dampened and a low-confidence warning
  prints — never a silent zero.

Ranked gap list → an AutoCover-style **scenario map per top target** (LLM: intents / edges / invariants ∩
the coverage baseline → orphan scenarios) → each orphan through the four-arm gate. Module-level exclusions
are hard drops: a module any OPEN PR is editing (grow at rest, don't fight in-flight work), or an unchanged
module whose completed scenario-map audit found no orphan. Per-GAP exclusion (a `.ledger.grow.jsonl` gap
already `closed-current` / `unclosable`) applies when the picked module's scenario map is enumerated.
repowise steers SELECTION only
(grounded-summary-not-ground-truth — verify the deficit you act on). Tiered: v1 = cheap in-stack signals
(fan-in + seam + churn); fold in survivors / contexts / repowise / complexity as it matures.

## The cycle (one gap per run)

1. **Select** a module via triage; enumerate its scenario map; pick the top orphan gap. Skip a gap that is
   `closed-current` / `unclosable` in the ledger, or whose real fix is an open issue Grow already filed.
2. **Author** ONE grown test for the gap (ADDITIVE — see *Extend-vs-add*). It gets the **minimum decisive
   context**: the target symbol + the orphan scenario + the relevant invariant family + the existing test
   file (to extend, not duplicate) — never a whole-module/repo dump. Prefer emitting a Hypothesis property /
   `deal` contract over a bare example when the gap is a family, not a point.
3. **Objective gate** (deterministic executor) — first prove the edit is additive and run its proposed
   node on pristine production. Then run the applicable `grow_gate` arms. Scenario growth needs liveness
   plus either an old-survives/new-kills mutant (arm 1) **or** a newly-lit line (arm 3); at least one is
   required, but neither is mandatory. Concurrency needs the bound regression/control pair (arm 4). A pristine failure reaches
   the product-bug branch only after two isolated oracle reviews uphold it. Temporary mutants restore
   captured bytes and verify equality; Git-index access is never part of a gate.
4. **Subjective gate** (see *Review architecture*) — is the added power real and plainly explainable, or
   redundant / over-fit / a change-detector in disguise?
5. **Product-bug branch** (see *Product bugs*) if the executor and both reviewers prove a real defect.
6. **Reflect, record, act, finalize** — first obtain a durably appended isolated reflection receipt, then
   bind it into the ledger record. A missing/failed reflection makes the run incomplete and cannot suppress
   the coordinate. Append an `open` receipt before any authorized PR/issue action. Only after
   validating that receipt may the adapter act outward. Persist a non-empty PR result as `open` until its
   merge is verified; persist a created issue as `filed`. Failed/no-op outward action leaves the gap `open`.
   Dry runs and unclosable gaps record directly. A shippable change must still clear the **"worth a human's
   attention" bar**. Human merges.
8. **Complete** only after reflection is recorded. The reflection's proposals remain advisory and never
   self-modify the loop; its durable receipt is mandatory lifecycle evidence.

### Product bugs (green-trunk policy)

A grown test that fails on a real defect can't land red. Mirror Sharpen's source-bug policy, reversed:
**file the bug as an issue when outward actions are authorized**; otherwise record the explicit dry-run
filing blocker. Land test+fix
**together** so the new test guards the bug it found. If a confidently-correct fix can't be produced,
**don't land the assertion** — file the bug high-severity, record `state: filed` (`filed:[#id]`), and leave
trunk green. This is the reverse handshake to Sharpen's `grow-filed`.

### PR body — evidence, in project terms

Not "a new test was added." State **what** (the scenario/config now pinned) and **why a human should
care**: which real interaction was untested, and the gate evidence that the test is load-bearing (arm-1
mutant killed / arm-2 live assert / arm-3 newly-lit line / arm-4 control-fails). Classify the disposition
up front — exactly one of the four outcome classes — so the merge decision is clean.

## Extend-vs-add — the boundary with Sharpen [`tests/util.py` model]

**Grow = ADDITIVE**: append a `Profile` / `ENTRY_FACTORIES` row, a `parametrize` case, an `@example`, or a
new test (appending a `PROFILES` row IS extend-before-add — one row, every property inherits it). **Sharpen
= MUTATIVE**: change an existing test's assertions. Deterministically separable by `grow_gate.additive_gate`
— a real adds-only assert-node diff: every before-assert must still be present after (only additions); any
altered/removed assert node ⇒ MUTATIVE ⇒ route to Sharpen. **Not `sharpen_gate.anticheat_diff`**: that only
flags a specificity *drop*, so a same-tier value change (`== 1` → `== 2`, a change-detector) slips past it
as 'additive' (the review's C4) — `additive_gate` catches it.

## Review architecture

Two tiers — objective is cheap and deterministic; the debate is spent only where judgment is irreducible.
Identical fidelity rules to Sharpen (isolated author→skeptic→judge, two independent UPHOLDs, SycEval
framing) — see [`ADAPTERS.md`](ADAPTERS.md) and Sharpen's `SPEC.md` → *Review architecture* for the shared
rationale; not duplicated here.

- **Objective gate:** the deterministic `grow_gate` re-measurement from cycle step 3. No LLM.
- **Subjective gate:** the **author** is not an independent reviewer — shipping requires **two independent
  UPHOLDs**. A **skeptic** (different context — sees only the factual *what* + *diff*, never the author's
  *why*) reviews first; a skeptic REFUTED drops immediately. On UPHELD a **judge** (its own isolated
  context, not told the skeptic's grounds) reviews adversarially; ship only if it ALSO UPHOLDs.

Seed the reviewers' veto list with Grow's characteristic failure modes (the anti-bloat hazard, mirror of
Sharpen's lobotomy):
- **redundant** — the scenario is already pinned elsewhere (arm-1 survives-old is the deterministic guard;
  the reviewer catches subtler subsumption a coverage set misses);
- **vacuous / change-detector** — the test asserts a tautology, an implementation detail, or a value read
  from the code under test (also an arm-2 / anti-cheat bounce);
- **over-production** — several near-duplicate tests where one `parametrize` / `@given` is clearer;
- **should-have-extended** — a new file/test where appending a `PROFILES` row or a case would inherit the
  corner (extend-before-add).

## Anti-bloat governance

Grow's hazard is suite explosion, not lobotomy. Three controls, in the author prompt and the gate:
extend-before-add (above) · hide-covered-code in the author context (so it can't restate what's tested) ·
redundancy-as-flag (coverage-set subsumption, TestIQ-style — **flag, never auto-reject**: subsumption
misses oracle strength) · a suite-cost budget (a grown test that adds seconds to the default tier must earn
it, or carry `integration`).

## Sharpen hand-off — the handshake

Sharpen files an uncovered-behaviour gap it cannot fix by editing as a `grow-filed` issue and skips that
module while the issue is open. **Grow consumes those `grow-filed` issues as prime candidates.** When Grow
closes one (a landed test), Sharpen's filed-and-skip flips back on. Conversely, when Grow finds a latent
bug (outcome class 2) it files a product issue and records it (`filed`) so it does not re-surface the same
gap until the issue closes or the target symbol changes.

## Ledger — `.ledger.grow.jsonl` (repo top level, committed; `tools/grow_ledger.py`)

Durable across cron runs. The gap key is **semantic**, not positional, or line-drift from unrelated edits
would spuriously reopen a closed gap and the loop would never terminate (proven in
`tools/grow_ledger.py`, locked by `tool_tests/test_grow_ledger.py`):

```jsonc
{ "gap_id": "<hash(source, target_symbol, dimension)>",
  "examined": "<iso>",
  "source": "survivor | dead_config | invariant | filed",
  "target_symbol": "app/dictionary.py::Dictionary._entry_from_row",
  "dimension": "warm==cold@entry_cache",           // the under-specified axis
  "target_sha": "<hash of the TARGET SYMBOL's AST source, not the whole module>",
  "toolset_version": 3,
  "contract_version": 11,
  "state": "open | closed | unclosable | filed | dry-run",
  "test": "tests/test_cache_race.py::test_...",
  "outcome": "coverage-only | bug | robustness | design",
  "review": { "author": "<id>", "skeptic": "<id>", "judge": "<id>",
              "skeptic_verdict": "UPHELD", "judge_verdict": "UPHELD", "verdict": "UPHELD" },
  "filed": ["#201"],                                // product issues (outcome class 2/3/4)
  "left-unclosable": ["equivalent mutant — no test can kill it"] }
```

Status (`grow_ledger.status`): **unseen** / **open** / **closed-current** (a test landed, target unchanged
→ SKIP) / **stale-target** (the target symbol's AST changed → reopen) / **stale-toolset** (whole ledger
re-examines) / **unclosable** (recorded infeasible → SKIP). A closed gap stays closed under unrelated churn
and reopens ONLY when its own target symbol changes. `filed` records a confirmed product issue; `dry-run`
records a run with no outward action. Any shippable state additionally requires a valid `review` block
(see *Fidelity* in `ADAPTERS.md`); without one the run is a `dry-run`.

A completed scenario map that finds no orphan appends a separate module-audit record:

```jsonc
{ "audit_module": "app/subnav.py", "examined": "<iso>",
  "tests": ["tests/test_subnav_policy.py"], "audit_sha": "<module plus test tree>",
  "toolset_version": 3, "contract_version": 11, "state": "no-gap",
  "scenario_map_summary": "source replacement, policy, settle windows, failure, navigation" }
```

`audit_status` is **audit-unseen** / **audited-current** / **stale-audit** / **stale-contract** /
**stale-toolset**. Current
no-gap audits are module-level triage exclusions only when current survivor/context evidence remains zero.
Any byte change to the module or test tree, a lifecycle-contract or toolset bump, or newly positive
adequacy evidence reopens the audit. The conservative whole-test-tree hash prevents an omitted indirect
test from creating a false permanent exclusion. This is not an `unclosable` gap: no semantic gap was
claimed, so no target symbol or dimension is invented.

## Self-reflection — every run introspects the LOOP (`tools/grow_reflect.py`)

The loop's own thesis applied to itself: a green run proves nothing about whether the LOOP is any good. Both
dogfood runs found real loop-design bugs the design docs missed (run 1 → 8 flaws under adversarial review;
run 2 → the arm-1/arm-3 composition bug). So every completed outcome — bounced, dropped, or no-candidate
included, those are the richest lessons — passes a **`Reflect`** step before its Grow receipt:

- **Isolated + evidence-based.** A fresh agent (it did not run the loop) receives only the factual **run
  trace** — which arms ran / bounced / were n-a, retries, review verdicts, outcome, notes — and reasons
  from it. It **introspects** (what happened), **reflects** (what about the LOOP was wrong / inefficient /
  suboptimal — a false-bounce, a false-pass, an n-a arm that should apply, an inverted triage signal, a slow
  stage, a CLI that couldn't express what was needed, a fidelity gap), and **improves** (the smallest
  concrete change to a loop TOOL/SPEC/harness). If the run was clean it files NOTHING (anti-Goodhart — no
  manufactured findings).
- **Durable lifecycle evidence.** `tools/grow_reflect.py append-run` writes a run receipt even when there
  are no findings and returns its `reflection_id` plus `trace_sha`. Every invocation gets a monotonically
  sequenced receipt; `grow_ledger.py` refuses stale reuse except the single `open` → outward-evidence
  finalization for the same gap. A filed issue gets a fresh reflection bound to the `filed` outcome.
- **Advisory, never self-modifying.** It writes only `.reflection.grow.jsonl`; it MUST NOT edit any tool /
  spec / harness / product file. Self-modification is strictly more dangerous than the loop's test edits
  (which already never auto-merge) — a human triages every proposal. A proposal touching the reflection
  machinery itself is flagged `self_referential` for extra scrutiny.
- **Recurrence + escalation, versioned.** A finding's identity is `hash(category, subject)` — so the
  reflector must REUSE an existing finding's exact category+subject when the same weakness recurs (it reads
  the ledger first), or the id won't match and recurrence can't accumulate (a real trap the reflection's own
  test surfaced). Recurrence is counted at the current `loop_version` (manifest, mirrors Sharpen's
  `toolset_version`). A finding seen
  ≥ threshold (2) is **escalated** to the human. When a human lands a loop-improvement they bump
  `loop_version`, which resets the accumulation — a finding that outlives the fix re-escalates; one truly
  fixed goes quiet. This is the *inner* loop; the *outer* "grill the loop" reflection (re-derive the arm/
  signal set from scratch, A/B before adopting) mirrors Sharpen's and also bumps `loop_version`.

## Cadence & cost

Background cron / idle time only — **one gap per run**, bounded. Never runs against a module under active
feature work. `poe all` stays the fast local gate for the coding loop; the loop's own tools live outside it
Deterministic loop-tool tests run in `poe all` via `loop-tools-test`; adapter/harness smokes and slow live
loop or adequacy executions remain explicit. A grown test is an ordinary suite member.

## Human gate

The maintainer approves every merge. The loop's job is to arrive at that gate with a small, single-gap,
evidence-backed test that survived an adversarial internal review — so approval is a *reading*, not an
investigation.
