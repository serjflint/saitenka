# The Sharpen Loop — a guide

**Status:** Informational · **Audience:** anyone comfortable with plain `pytest` unit tests · **Scope:**
what the Sharpen loop is, why it exists, how the pieces work, and the reasoning behind each choice.

This is the *reader's guide*. The terse, agent-facing process spec is [`SPEC.md`](SPEC.md); the audit
trail is `.ledger.sharpen.jsonl`; the research this rests on is summarized in **References** below. If you only have five minutes, read the **Abstract**
and
**Example** sections.


## Abstract

Most test suites are judged by two numbers: *do the tests pass* and *what percentage of lines they
cover*. Both can be high while the tests are quietly worthless — they can pass without asserting
anything meaningful, and they can "cover" a line without noticing when that line breaks. The **Sharpen
loop** is a background process that measures a *different* thing — whether each test actually **catches
bugs** and is **built well** — and then improves the tests themselves, one module at a time, arriving at
a human with a small, evidence-backed pull request. It never merges on its own.

Two ideas do the heavy lifting:

1. **A green suite proves nothing about a quality edit.** So every proposed change to a test is checked
   by a *deterministic gate* that asks "did this edit keep (or improve) the suite's ability to catch a
   real change to the code?" — not "do the tests still pass?"
2. **The judgment is the product, not the LLM.** The instruments (mutation testing, a test-smell linter,
   a git-content ledger) are cheap and boring; the human merge gate is final. The model only proposes.


## Motivation — why "passing" and "coverage" are not enough

Consider a real test:

```python
def test_score_returns_expected():
    assert score("hello") == 3
```

It passes. It covers `score`. Now suppose a colleague "refactors" it:

```python
def test_score_returns_expected():
    result = score("hello")
    assert result is not None       # still green, still 100% coverage — and now catches almost nothing
```

The suite is still green. Coverage is unchanged. But the test has been **lobotomized**: `score` could
now return `3`, `4`, `-1`, or `"banana"` and the test would not complain. Neither "pass/fail" nor
"coverage" noticed. This is the exact failure the Sharpen loop is built to detect and prevent.

The other half of the problem is *how* a test is built. A test that reaches into a private field —
`assert reader._paused is True` — passes and covers, but it's welded to the implementation: rename the
field and the test breaks though nothing a user sees has changed. That's a bug **in the test**, and no
amount of green tells you it's there.

Sharpen exists to run a real quality rubric continuously, point itself at the module that most needs it,
prove an edit is genuinely an improvement, and stop bothering you once a module is clean.


## Background (for the pytest-only reader)

You need three concepts. Each is small.

### Coverage vs. adequacy

**Coverage** answers "was this line *executed* during the tests?" It says nothing about whether a test
would *notice* if the line were wrong. **Adequacy** is the stronger question: "would the suite *fail* if
the code changed?" A suite can have 100% coverage and near-zero adequacy (the lobotomy above). Sharpen
measures adequacy, not coverage.

### Mutation testing (the adequacy microscope)

Mutation testing measures adequacy directly. A tool makes a tiny, deliberate change to your production
code — a **mutant** — and re-runs the tests:

```python
# original
if start <= t < end:
# a mutant: <= became <
if start < t < end:
```

If some test now **fails**, the mutant is *killed* — your suite noticed the change. If every test still
passes, the mutant **survived** — a real behaviour changed and nobody complained. A surviving mutant is
a precise, addressable coordinate: "a test that pinned *this* is missing or too weak."

Two caveats define how we use it:

- **Equivalent mutants.** Some mutants don't actually change behaviour (e.g. flipping an operator inside
  a type annotation that is never evaluated). They can never be killed, so **100% is unreachable** — a
  survivor is a *lead to investigate*, never a score to max out (chasing the score is
  [Goodhart's law](https://en.wikipedia.org/wiki/Goodhart%27s_law): the metric stops being useful the
  moment it becomes the target).
- **Cost.** Every mutant reruns the suite, so mutation is minutes-per-module — an idle-time instrument,
  not something you run on every commit.

We use [`cosmic-ray`](https://cosmic-ray.readthedocs.io/) via `uv run poe mutate <module>`. See
`AGENTS.md` → *Mutation auditing*.

### Property-based testing (killing a *class* of survivors)

When mutation finds a survivor, the fix is usually not one more example — it's a **property** that holds
for *all* inputs. Instead of:

```python
def test_add():
    assert add(2, 3) == 5           # one example; a mutant that only breaks on 7+11 survives
```

you assert an invariant with [Hypothesis](https://hypothesis.readthedocs.io/) generating the inputs:

```python
from hypothesis import given, strategies as st

@given(a=st.integers(), b=st.integers())
def test_add_is_commutative(a, b):
    assert add(a, b) == add(b, a)    # kills a whole class of arithmetic mutants at once
```

Hypothesis *shrinks* a failure to its smallest form; you pin that shrunk case as an `@example` so the
kill is deterministic on every rerun. This is how the pure core in this repo is hardened (see
`tests/test_sub_index_properties.py`).

### Test smells (the "built badly" axis)

A **test smell** is a recognised anti-pattern in test *code*: asserting a private attribute, asserting
that a mock *was called* rather than what it produced, a real socket in a "unit" test, a `time.sleep`,
control flow in the test body. These are orthogonal to adequacy — a test can kill every mutant and still
assert a private field. The literature (van Deursen 2001; DSpot; PyNose) catalogues them; we detect a
curated subset with a linter.


## Approach — Sharpen vs. Grow, and the four axes

The loop has a sibling it deliberately does **not** do. **Grow** *writes missing tests* for uncovered
behaviour. **Sharpen** *fixes the tests that already exist* — over/under-assertion, wrong abstraction
level, coupling, redundancy. When Sharpen finds a gap only a new test could fill, it **files an issue for
Grow and moves on** (a filed issue is a primary result, not a failure).

Adequacy and architecture are independent, so Sharpen measures four **axes**, each with its own
instrument, all built from tooling already in the stack:

| # | Axis | The question it answers | Instrument |
|---|------|-------------------------|------------|
| 1 | **Efficacy** | Does the test catch a real change? | `poe mutate` (cosmic-ray) |
| 2 | **Conformance** | Is the test built per the house rules? | `poe test-lint` (ast-grep on `tests/`) |
| 3 | **Brittleness** | Does it survive a behaviour-neutral refactor? | *probe — deferred, see Limits* |
| 4 | **Redundancy** | Is this test a duplicate? | advisory heuristic only |

Efficacy is the **differentiator** (see *Rationale*); Conformance is the **workhorse**;
Brittleness/Redundancy are depth added only on demonstrated need.


## The cycle (one module per run)

1. **Select** a module via *triage* (below); skip it if the ledger says it's already sharpened and
   unchanged, or if its real fix is an open Grow issue.
2. **Measure** all applicable axes against a **known-green baseline** — any test already red or flaky
   *before* the edit is quarantined, never folded into a number.
3. **Propose** a small, named list of edits (tighten this assertion; fix this level; kill this survivor),
   each with the minimum decisive context (the surviving mutant, the target test, the rubric).
4. **Objective gate** — the deterministic anti-lobotomization check (below). No LLM. A proposal that
   drops adequacy or fakes a kill is bounced automatically.
5. **Subjective gate** — an *author* and an independent *skeptic* argue whether the change is a real
   improvement; on genuine disagreement a judge decides and the default is **drop**.
6. **PR or Grow issue** — a surviving, worth-a-human's-time proposal becomes a one-module PR; a genuine
   coverage gap becomes a filed issue. A human merges. Always.
7. **Record** the outcome (including what was deliberately left undone) in the ledger.


## Tooling

### Axis 1 — `poe mutate` and the anti-lobotomization gate (`tools/sharpen_gate.py`)

Running `uv run poe mutate sub_index` writes a cosmic-ray **session database** listing every mutant and
whether it was KILLED or SURVIVED. We read that database directly (never the console summary — scraped
stdout silently diverges from reality).

The gate is the heart of the loop. It answers "is this test edit a genuine improvement, or a lobotomy in
disguise?" with **two complementary arms** — deterministic given the session DB, no LLM:

- **Arm A — Efficacy replay (`sharpen_gate.py efficacy`).** For each mutant the edit *claims* to kill, it
  re-applies that exact mutant (`cosmic-ray apply`) and confirms the edited suite now fails on it (an
  *earned* kill). Critically, it also re-checks the **complete set of mutants the campaign already
  killed** in the touched function — the *control set*. If any of those now survives, the edit weakened
  the suite while staying green: a **lobotomy → BOUNCE**.
- **Arm B — anti-cheat (`sharpen_gate.py anticheat`).** A static (AST) diff of the test file that bounces
  the cheap fakes: an assertion **removed** or **weakened** (e.g. `== 5` downgraded to `is not None` — a
  *specificity* ladder catches this even when the assertion count is unchanged), a **trivially-true**
  assertion (`assert True`, `assert x == x`), or an **expected value read from the code under test**
  (`assert x == module.CONST`, or a bare constant imported from the module — a change-detector that moves
  in lockstep with the code and so catches nothing).

They are complementary by design. A `assert True` is caught by Arm B. A subtler edit that *narrows* an
assertion within the same tier (`assert whole == expected` → `assert whole['k'] == expected['k']`) slips
past Arm B — static "is X weaker than Y?" (subsumption) is undecidable in general — but Arm A catches it,
because the narrowed assertion lets a previously-killed mutant survive. Neither arm replaces the human
gate; together they stop the lobotomies a green run hides.

### Axis 2 — `poe test-lint` (ast-grep rules under `sgconfig/test-rules/`)

Eight hand-written [ast-grep](https://ast-grep.github.io/) rules scan `tests/` for smells:
private-attribute asserts (and the compound-assert variant), mock-interaction asserts, private-symbol
`monkeypatch`, mis-levelled real I/O (a real subprocess/socket/`time.sleep` in an unmarked default-tier
test), ambient non-determinism, `os.environ` mutation, and session-scoped mutable fixtures. Each rule
ships with planted `valid`/`invalid` examples (`poe test-lint-test`) so it can't silently rot into a
no-op. (A control-flow/cohesion rule is specced but deliberately not built — advisory-only, noisy on a
classicist suite.)

A key distinction the loop makes: a rule is either a **metric** or **actionable**.
`test-assert-private-attr` is a *metric* — on white-box code it fires heavily *by design* (the real
output, e.g. rendered pixels, has no cheap public handle), so a high count is an **architecture signal**
(a missing public seam → Grow/refactor), not a list of 100 test-by-test fixes. `test-mislevelled-realio`
is *actionable* — each hit is a concrete "add the `integration` marker or use a fake." The triage ranks
on the actionable count and treats the metric count as a separate signal.

### Triage — `tools/sharpen_triage.py`

Points the loop at the right module. It prints a transparent, per-signal table (never just one number —
signals *localise* work, they are not a score to maximise) and ranks a composite:

```
score = 0.4·survival(Efficacy) + 0.4·actionable-conformance + 0.2·churn(recency) + freshness-bonus
```

It **hard-excludes** what the loop must not touch: a module already sharpened and unchanged, a module
whose gap is an open Grow issue (*filed-and-skip*), and any module a currently-open PR is editing (heal
at rest, don't fight in-flight work). `churn` is git activity — a *recency* proxy, honestly **not** a
stand-in for centrality/risk (a repowise integration is a documented, still-unwired input).

### The ledger — `tools/sharpen_ledger.py` + `.ledger.sharpen.jsonl`

The loop's durable memory: one JSONL record per module audit — what was measured, what was decided, what
was left undone, and a `source_sha`. That hash is a **SHA-256 over the module's bytes concatenated with
its mapped test files' bytes** — *content*, not mtime, so a "sharpened" verdict survives clones and CI.
A module is skipped until its `source_sha` changes (someone edited it) or the toolset version bumps (the
instruments themselves changed → the whole ledger re-audits). This is what lets the loop run forever
without redoing settled work — and what makes *filed-and-skip* possible.


## Example — the `sub_index` heal (a full iteration)

`SubIndex._disambiguate_text_matches` decides *which* cue wins when several subtitles share identical
text: the last-jump hint first, then the cue whose `[start, end)` window contains the timestamp, then the
first match. A mutation campaign on `sub_index.py` left **24 non-equivalent survivors** clustered in the
timing/fallback tiers — because the only existing test covered the *hint* tier and left the other two
**unasserted**. User-visible consequence: on a repeated line, navigation would jump to the *wrong copy*.

The heal was one additive test asserting all three tiers on the public return value:

```python
def test_locate_by_text_disambiguates_duplicate_lines_by_timing_then_first():
    idx = SubIndex([SubCue(1.0, 2.0, "同じ"), SubCue(3.0, 4.0, "ちがう"), SubCue(5.0, 6.0, "同じ")])
    dup = "同じ"  # matches cues 0 and 2
    assert idx.locate(text=dup, preferred=0, sub_start=5.5) == 0   # tier 1: the hint wins
    assert idx.locate(text=dup, preferred=-1, sub_start=5.5) == 2  # tier 2: window containing sub_start
    assert idx.locate(text=dup, preferred=-1, sub_start=6.0) == 0  # tier 3: no window → first match
```

Result: **all 24 survivors killed; module survival 38.98% → 35.67%; zero regressions** (confirmed by a
full re-campaign). The gate's Arm A verified the 24 earned kills and that nothing previously-killed
regressed; an isolated skeptic subagent independently re-derived every assertion and upheld it. The
ledger recorded it `in-progress` (other functions in the module remain to audit).

**The gate catching a lobotomy.** Take that healed test and weaken it — `== 2` → `in (0, 2)`. The suite
still passes. Arm B may not see it (still an equality-ish shape); Arm A does: replaying the mutants the
test used to kill, most now survive → **score dropped → BOUNCE**. That is the whole point in one move.


## Rationale — why each choice, grounded in prior work

- **Mutation, not coverage, is the adequacy signal.** Coverage never moved off ~97% while real bugs slid
  through; mutation is what actually measures fault-detection. (Independently reached by
  `Jott2121/crucible`.)
- **The gate is the differentiator.** The closest published system, **UTRefactor** (arXiv:2409.16739),
  refactors test smells but its only checks are smell-gone + compile + pass + an LLM judge — it *never
  verifies the test still catches bugs*. That is precisely the lobotomization blind spot; our
  mutation-stability gate (Arm A) is what it lacks.
- **Expect low yield; the gates carry the weight, not the model.** **TAM-Eval** (arXiv:2601.18241) found
  SOTA LLMs are weak at realistic test maintenance; Meta's **TestGen-LLM** (arXiv:2402.09171) shipped a
  73% acceptance rate that came *entirely* from heavy deterministic filters, not model cleverness. So we
  invest in the deterministic gates and keep the human as the final filter.
- **Killing a mutant ≠ a change a developer values.** **DSpot** (EMSE 2019,
  doi:10.1007/s10664-019-09692-y) had developers reject mutation-killing assertions as
  implementation-detail noise. So the gate proves *correctness-preservation*; only the human proves
  *worth*, and the loop refuses to over-automate (this is also why the static `expected-value-from-CUT`
  lint was assessed and **not** shipped — it would flag legitimate symbolic-constant assertions).
- **An isolated skeptic, framed to refute.** **SycEval** (Stanford, FAccT 2025, arXiv:2502.08177) found
  LLMs sycophantic ~58% of the time and that *preemptive* framing makes it worse — so the skeptic gets
  only the diff and a task ("construct a bug this misses; default to REFUTED"), reasons from the code,
  and cites mutants, not authorities. Debate-to-consensus is a trap; the default on controversy is drop.
- **Hand-rolled ast-grep beats off-the-shelf smell detectors.** Ruff's `PT` rules are pytest-idiom
  hygiene; PyNose/pytest-smell are stale or noisy on a classicist suite. Simple thresholds over-report on
  human-written tests, so Assertion-Roulette / Eager-Test are deliberately *not* gated.


## Prior art and novelty

A deliberate GitHub + literature survey (recorded in our internal research notes) asked: does this already
exist? Findings:

- **Closest artifact — `Jott2121/crucible`** (MIT, weekend repo): mutation-not-coverage + adversarial
  critic + anti-cheat + honest `dry` terminal state + receipts + a frozen-baseline A/B. It independently
  validates that core — but it is **Grow-flavoured** (writes *new* tests), with no Conformance /
  Brittleness / Redundancy axes, no ledger, no Sharpen focus.
- **Closest published system — UTRefactor** (above): the smell-refactoring half, without the efficacy
  gate.
- **Reused ideas:** DSpot's mutant-selection predicate; UTRefactor's DSL heal-recipes; adversarial-review
  configs (`dang-w/sceptic`, `trailofbits/claude-code-config`).
- **Confirmed unbuilt anywhere:** (a) a behaviour-preserving-transform **brittleness probe** run against
  *existing* tests, and (b) a **source-hash-idempotent, propose-only ledger bot**.

So the novelty is not "mutation + a critic" (that exists) — it is **Sharpen-refactoring of existing tests
across four axes, integrated with the brittleness probe and the content-hash ledger**, arriving at a
human gate with worth-it, one-module, evidence-carrying proposals.


## Limits and open work (stated honestly)

- **Arm B cannot catch narrowing within a tier** (subsumption is undecidable statically). This is
  backstopped by Arm A's full control set and the human gate — not hidden.
- **Efficacy is minutes-per-module** — an idle-time instrument, never part of the fast `poe all` gate.
- **Brittleness (Axis 3)** — the certified behaviour-preserving-transform probe is designed but not built
  (near-zero yield on this suite today); it's trigger-gated on a hidden-coupling case appearing.
- **Redundancy (Axis 4)** — advisory only; cosmic-ray records no per-test kill-matrix, so it can only
  *flag* candidates, never auto-prune (a "redundant" test is often a regression/documentation guard).
- **The autonomous adapters** ([`ADAPTERS.md`](ADAPTERS.md) — author / skeptic / judge as isolated
  invocations; [`harness.js`](harness.js) for Claude Workflow, `.agents/skills/sharpen-loop/` for Codex)
  are built; the Claude adapter is proven on dry-runs and the Codex adapter is structurally validated
  but has not spent its first live audit. The remaining gaps are that receipt and an idle-cron trigger.
  A manual run without a valid isolated review
  is still a **`dry-run`** — fine for exploration, but it may not open a PR as if reviewed.
- **repowise centrality/risk** is not yet a triage input (churn stands in as recency only).


## References

Peer-reviewed and first-party sources this loop rests on (from an internal survey of the literature
+ a GitHub artifact hunt):

- **UTRefactor** — LLM + tsDetect + DSL refactoring of test smells (Java). arXiv:2409.16739 (FSE 2025).
  *The closest existing system; lacks the efficacy gate.*
- **DSpot** — test amplification. *Empirical Software Engineering* 2019, doi:10.1007/s10664-019-09692-y.
  *"Mutation-killing ≠ developer-valued."*
- **Meta TestGen-LLM** — "Automated Unit Test Improvement." arXiv:2402.09171. *Acceptance came from
  deterministic filters, not the model.*
- **TAM-Eval** — LLMs are weak at realistic test maintenance. arXiv:2601.18241 (2026).
- **SycEval** — sycophancy in LLM evaluation. Fanous et al., Stanford, FAccT 2025, arXiv:2502.08177.
  *Frame the skeptic to refute; default-drop.*
- **STING** — behaviour-preserving transforms to filter *newly generated* tests (near-miss; not applied
  to existing tests). arXiv:2604.01518.
- **Agent-as-a-Judge** — multi-agent evaluation precedent. arXiv:2410.10934.
- **PyNose** — Python test-smell detector. arXiv:2108.04639.
- **van Deursen et al. (2001)** — the original test-smell taxonomy.
- **Harman (2004)** — testability transformation (the behaviour-preserving-transform lineage).
- **`Jott2121/crucible`** — closest GitHub artifact (mutation + critic + anti-cheat + receipts + A/B).

Tools: [cosmic-ray](https://cosmic-ray.readthedocs.io/) · [Hypothesis](https://hypothesis.readthedocs.io/)
· [ast-grep](https://ast-grep.github.io/). Local conventions: `AGENTS.md` (*Testing*, *Mutation
auditing*, *Fuzzing & symbolic checks*) and the `write-test` / `dev-gate` skills.
