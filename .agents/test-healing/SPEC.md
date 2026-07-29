# Test-Healing Loop — process spec

A deliberate, idle-time loop that **sharpens the existing test suite** — finds and fixes bugs *in the
tests themselves* (wrong architecture, scope, abstraction level, cohesion; over/under-assertion) and
files genuinely-uncovered behaviour as issues for a separate Grow effort. It runs at rest, proposes
one module per PR with evidence, and never merges — a human is the final gate.

This is the **Sharpen** loop. Its sibling **Grow** loop (write missing tests) is out of scope here;
Sharpen *hands work to it* (see *Grow hand-off*), never does it.

## Why a loop and not a checklist

The judgment for "is this a good test" is already written down (`AGENTS.md` → `## Testing`,
`## Mutation auditing`, `## Fuzzing & symbolic checks`; the `write-test` skill). What was missing is
something that **runs the rubric continuously, points itself at the right module, and proves an edit is
an improvement**. Signals here *localise* work; they are never scores to maximise (Goodhart) — the loop
stops at the Pareto knee, not at 100 %.

## The four instruments

Efficacy and architecture are **orthogonal**: a test can kill every mutant and still assert a private
field. So adequacy is measured on four axes, all built from tooling already in the stack.

| # | Axis | Question | Instrument |
|---|------|----------|-----------|
| 1 | **Efficacy** | does the test catch a real change? | `poe mutate` (cosmic-ray) — a **localiser**, non-equivalent survivor = a coordinate to harden |
| 2 | **Conformance** | is it built per the invariants? | `poe test-lint` (ast-grep on `tests/`, same engine as `poe invariants`) — a per-file violation count |
| 3 | **Brittleness / over-fit** | does it survive a behaviour-neutral refactor? | `poe brittleness` — apply a **certified** behaviour-preserving source transform; any test that goes red is coupled to internals |
| 4 | **Redundancy** | is this test a waste? | **advisory only** — cosmic-ray records no per-test kill-matrix, so this can't be computed cheaply; a coverage-overlap heuristic *flags* candidates, never auto-prunes |

Conformance lint rules (`poe test-lint`, initial set — grows as smells surface):
- assert on a `_private` attribute → observable-behaviour violation (a **test bug**)
- `.assert_called*` / `.call_count` / `.mock_calls` → interaction assertion (this repo is classicist)
- `monkeypatch` of a **private** production symbol (`_name`) → coupling to internals (the *raw*
  monkeypatch count is fine — it's the sanctioned seam; only a private target is a smell)
- a default-tier test (no `integration`/`live`/`e2e` marker) that opens a socket, spawns a subprocess,
  mutates `os.environ`, reads the wall clock, or draws ambient `random` → **mis-levelled / non-deterministic**
- a `session`/`module`-scoped fixture returning a literal mutable (`{}`/`[]`/`set()`) → cross-test state
- control flow (`if`/`for`/`while`) in a test body → cohesion smell (**advisory, not a bounce**).
  Assertion-Roulette and Eager-Test are deliberately **not** gated — empirically noisy on a classicist
  suite (pytest's assertion rewriting already localises multi-assert failures).

None of the four is a `poe all` gate. They are the loop's instruments. (A clean subset of the
Conformance lint *may* graduate to the gate later — a separate decision.)

### Reading the Conformance lint — metric vs bug list

Some rules are **actionable** (each hit is a candidate fix); others are **metrics** (a per-file count
that ranks coupling — *not* a per-hit to-do list). `test-assert-private-attr` is a **metric**: on
white-box / god-object code it fires heavily *by design*, because the real output (e.g. OSD pixels) has
no cheap public handle, so reaching into `_state` is the pragmatic seam, not an over-fit bug. (Audit of
`test_controller.py`: 92 hits → 83 load-bearing white-box, only ~9 genuinely redundant.) A **high count
is an architecture signal** — a missing public observation seam → **Grow/refactor**, not test-by-test
sharpening. The actionable subset lives in a separate rule, `test-assert-private-compound` (a private
check *bundled* into a compound assert: drop the redundant half, or split the multi-act). So: rank
modules by the metric, act on the actionable rules, and **triage each metric hit against "is there a
public seam?" before ever rewriting** — the loop's judgment step, not a static verdict.

### Axis 3 — the brittleness probe (contract-preserving variants)

*Not* "reuse cosmic-ray's equivalent mutants": those **survived**, so they killed nothing and carry no
brittleness signal — and cosmic-ray records no per-test kill anyway. Instead **generate** a certified
behaviour-preserving variant of the production code and run the impacted tests: a test that goes **red**
under a change that provably preserves observable behaviour is coupled to implementation detail.

- **v1 operators (safe):** `rename-local`, `inline-pure-temp`. `extract-pure-temp` (needs a stronger
  purity oracle) and `rename-private-helper` (name-mangling / polymorphism make its failures often
  *not* brittleness) are deferred.
- **Certify before running tests** via a three-tier oracle built from tools already present:
  **Trivial Bytecode Equivalence** (compare normalised `code` objects — omit line tables / `co_name` /
  `co_firstlineno`; a sound under-approximation, peephole/const-fold means it proves fewer pairs) →
  **Hypothesis differential** (same inputs ⇒ same return + exception type/message + warnings, reusing
  existing strategies) → **CrossHair** symbolic. Anything uncertified is discarded, not applied.
- **Env:** the transformer (LibCST) runs in a **pinned-3.13** env — source-rewrite only; the tests run
  under the target 3.14t. Same quarantine pattern as `fuzz`/`crosshair`/`invariants-taint`.
- **Flimsiness:** re-run the impacted tests N× with different `pytest-randomly` seeds and take the
  failing-node **intersection** — a witness must be stable (mutation can expose flakiness).

### Axis 2 ↔ Axis 3 — one vocabulary

The two share one cause vocabulary — private-access, interaction-assert, `repr`/`__name__` equality,
traceback-assert, `locals`/frame — but they are **not equally useful for every smell**. For **explicit**
coupling (`assert x._priv`), the lint already has 100 % precision and a rename-`_priv` witness is
*definitional* — it adds nothing. The probe's unique value is **hidden** coupling: a clean-looking test
that breaks under a neutral refactor via traceback / `repr` / call-order / `locals`. So build/run the
probe only where hidden coupling is plausible — and note this suite has **zero** `locals`/frame/source
observation (verified), so its probe yield is near-nil today. The cross-reference "flagged **and**
witnessed" matters for the non-explicit smells; for explicit private-attr the lint alone is the signal,
and adjudication is the *public-seam judgment* above, not a witness.

## Triage — where the loop points first

Per candidate module compute `[Efficacy: non-equiv survival rate, Conformance: violation count,
Brittleness: witness count, repowise centrality/risk]`, rank a composite, take the top (Redundancy is
advisory, not a ranking input). Churn is ~free (single
maintainer + agents), so recency is a *selection* signal, not a cost: **start from modules recently
changed and not yet covered by the ledger** (see *Ledger*), then descend the composite. The largest
multi-purpose test files score worst on cohesion and surface first.

Never point the loop at a module with an **open feature branch touching it** — heal at rest, don't
fight in-flight work (see the commit-around-parallel-work discipline).

## The cycle (one module per run)

1. **Select** a module via triage; skip if ledger-healed and source-hash unchanged (see *Ledger*).
2. **Measure** all four axes → the *before* snapshot.
3. **Propose** — the author agent emits a **named, deduplicated proposal list**, each item
   `{target test · axis · change · rationale}` (tighten to observable behaviour, fix level/marker, merge
   redundant tests, harden an existing test to kill a non-equivalent survivor). The gates below run
   **per proposal**, so a single controversial item can be dropped without losing the rest.
4. **Objective gate** (deterministic, no agent) — re-measure; **bounce the proposal** if any regressed:
   Efficacy dropped · a *new* Conformance violation appeared · a contract-preserving variant now kills a
   test that it didn't before (brittleness up — the classic false-improvement).
5. **Subjective gate** (see *Review architecture*) — is the benefit real and plainly explainable, or
   over-fitting in disguise?
6. **Source-bug branch** (see *Source bugs*) if a sharpened test went red on a real defect.
7. **PR** — one module, the surviving proposals, evidence-carrying body (see *PR body*). Human merges.
8. **Record** the outcome in the ledger, including *what was deliberately left undone and why*.

### Source bugs (green-trunk policy)

A sharpened test that fails on a real defect can't land red. So: **always file the bug as an issue**
(tracking), hand the fix to a dedicated subagent, and land test-fix **together** so every sharpened
test kills the bug it found. **But** if a *confidently-correct* fix can't be produced (reviewer
unconvinced / risk high), **don't land the sharpened assertion** — file the bug high-severity and
record "sharpening blocked on unfixed bug." Trunk stays green either way.

### PR body — evidence, in project terms

Not "N mutants died." The body states **what** (diff) and **why a human should care**:
> This test claimed to cover mining but asserted a private field, so a real breakage of the written
> note slid past. It now asserts the note payload; the mutant at `anki.py:LN` (change X→Y) proves it.

Plus the four-axis **before/after** table and, if a code fix rode along, that fix called out
**separately** so the merge decision is clean.

## Review architecture

Two tiers — objective is cheap and deterministic; the debate is spent only where judgment is
irreducible.

- **Objective gate:** the deterministic tool re-measurement from the cycle's *Objective gate* step. No LLM. Deterministic bounce.
- **Subjective gate:** an **author** agent (Opus) and a **skeptic** agent (Opus, *different context* —
  sees only what/why/diff, never the author's reasoning) argue whether the change genuinely improves
  quality. Agree-good → pass. Disagree → a **Sonnet judge** decides; **default on genuine controversy
  is DROP** and move to the next module. (Verification is easier than generation, so a Sonnet judge on
  a well-framed disagreement is sound and cheap.)

The veto criteria are not assumed complete — extend them as the loop's self-reflection or the human
surfaces new failure modes.

## Grow hand-off — filing an uncovered risk

When a **non-equivalent** mutant survives that *no edit to existing tests can kill*, that's a genuine
coverage gap — Grow's job. Sharpen **files an issue and stays in scope**, written the way a senior QA
writes a defect:
- **Risk as title** — the behaviour that can silently break ("subtitle timestamps past 24h aren't
  normalised — regression uncaught").
- **Repro that breaks it** — the survivor made concrete ("change `<=`→`<` at `sub_index.py:88`, suite
  stays green"), ideally with a 3-line failing test attached.
- **Consequence to the user** — wrong cue highlighted, wrong field mined — the impact, not the line.
- **Level + kind proposed** — "property test, boundary oracle."
- **Severity = user-impact × likelihood**, so the backlog self-orders.

## Ledger — `.ledger.healing.jsonl` (repo top level, committed)

Durable across cron runs; a PR cites it ("module X healed 3 weeks ago, source unchanged"). Header
record carries a global `toolset_version`. One record per module audit:

```jsonc
{ "module": "app/scoring.py",
  "audited": "<iso>", "source_sha": "<hash of module + its tests>",
  "toolset_version": 3,
  "axes": { "survival": 0.0, "conformance": 0, "brittleness": 0, "redundancy": "advisory:2" },
  "state": "healed | in-progress | blocked-on-bug",
  "decisions": ["tightened test_mine to assert the note payload, not _cache"],
  "left-undone": ["3 equivalent survivors (str|None under __future__) — unkillable"],
  "axes-not-applied": ["crosshair — z3 env not built this run; TODO"] }
```

**Healed** = clean on the three gating axes: **Efficacy** (no non-equivalent survivors, or all filed as
Grow issues) · **Conformance** (zero violations) · **Brittleness** (no witnesses). **Redundancy** is
advisory — recorded, never a heal-blocker. A healed module is skipped until:
- its **`source_sha` changes** — a SHA-256 (hex) over the module's bytes concatenated with its mapped
  test file(s)' bytes; content-hash, not mtime, so it survives clones/CI (mtime was fine for the
  in-process Anki cache but isn't portable for a committed ledger), **or**
- the **`toolset_version` bumps** (see *Self-reflection*) → whole ledger re-audits.

The ledger audits itself: an entry whose `source_sha` no longer matches the tree, or references a
module that moved, is stale → re-audit. A corrupt/untrustworthy ledger may be wiped and rebuilt from
recently-changed modules first.

## Self-reflection

- **Inner** (every audit): record `axes-not-applied` with the reason. This is the guard against the
  real failure it's named for — *the loop never ran crosshair/fuzz/arch until explicitly asked.* If an
  axis was skippable, the ledger says so out loud.
- **Outer** (periodic "grill the loop"): re-derive the technique list from scratch against current
  tooling and the `poe` stack. A **meaningful** extension **bumps `toolset_version`** and invalidates
  the whole ledger — this includes **adding or removing an axis** (a new measurement dimension is a
  technique change) as well as a new *kind* of test or a new tool. Rewording a rule does **not**.

## Cadence & cost

Background cron / idle time only — **one module per run**, bounded. Never runs against a module under
active feature work. Slow, exhaustive, deliberate: this loop is allowed the long feedback the main
coding loop can't afford. `poe all` stays the fast local gate for the coding loop; this loop's
slow instruments never join it.

## Human gate

The maintainer approves every merge. The loop's entire job is to arrive at that gate with a small,
single-module, evidence-backed proposal that survived an adversarial internal review — so approval is
a *reading*, not an investigation.

---

## Research

The prompts that shaped the four instruments and the full results live in the Saitenka-Vault note
`_source/test-healing-research.md` (Gemini deep research, 2026-07). The durable conclusions are already
folded into this spec; the load-bearing ones:

- **Brittleness (Axis 3) is genuinely under-tooled** — no turn-key Python tool exists; you build the
  contract-preserving-variant probe above. The equivalence oracle (TBE → Hypothesis-diff → CrossHair)
  reuses the stack. LibCST is MIT/permissive; run it pinned-3.13 (source-rewrite only).
- **Conformance (Axis 2): hand-rolled ast-grep beats every off-the-shelf detector.** Ruff `PT` is
  pytest-idiom hygiene only; `pylint-pytest` is an FP-suppressor; PyNose/pytest-smell are stale. Simple
  smell thresholds over-report on *human-written* suites → keep Assertion-Roulette / Eager-Test out of
  the gate.
- **Redundancy (Axis 4) is a negative result** — cosmic-ray stores no per-test kill-matrix (killed/
  survived only, `pytest -x` first-killer), so it's advisory, and *flag never auto-prune* regardless
  (equivalent mutants make zero-kill tests look redundant when they're regression/documentation guards).
