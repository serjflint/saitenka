# Sharpen Loop — process spec

> New here? Read [`GUIDE.md`](GUIDE.md) first — a self-contained explainer (mutation + property testing
> background, worked example, rationale, references) for a reader who knows only plain `pytest`. This
> file is the terse process spec the loop executes against.

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
- control flow (`if`/`for`/`while`) in a test body → cohesion smell (**advisory, not a bounce**) —
  **specced, deliberately not built** (noisy on a classicist suite; the 8 shipped rules exclude it).
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

Never point the loop at a module with an **open feature branch touching it** — sharpen at rest, don't
fight in-flight work (see the commit-around-parallel-work discipline). This exclusion is **fail-closed**:
if it cannot run (no `gh` auth / offline `--no-network`), the loop must **not open a PR** — it downgrades
to a dry-run rather than risk sharpening a module under active work.

## The cycle (one module per run)

1. **Select** a module via triage; skip if ledger-sharpened and source-hash unchanged (see *Ledger*), or if
   its real fix is already an open Grow issue (`grow-filed`, see *Grow hand-off*).
2. **Measure** all four axes → the *before* snapshot, against a **known-green baseline**: any in-scope
   test red or flaky *before* the edit is quarantined and recorded, never folded into an axis number — a
   poisoned before/after bounces good work or passes a regression (the Brittleness N×-seed distrust, extended
   to Efficacy and Redundancy). Read each instrument's **structured output** (cosmic-ray session DB,
   `test-lint --json`), never its console summary — scraped stdout silently diverges from reality.
3. **Propose** — the author agent emits a **named, deduplicated proposal list**, each item
   `{target test · axis · change · rationale}` (tighten to observable behaviour, fix level/marker, merge
   redundant tests, harden an existing test to kill a non-equivalent survivor). It gets the **minimum
   decisive context** — the surviving mutant, the failing witness, the target test file, the relevant
   rubric — not a whole-module/repo dump (broad context ~doubles cost for no measurable gain; the
   witness + prior attempt carried into a retry is the one high-value context). Sharpen-retries are **capped
   at ~3** (the first attempt succeeds ~95 % of the time it ever will; past ~3 returns collapse and cost
   blows up); an un-killable / un-sharpenable proposal is a **terminal outcome** recorded to `left-undone`
   / `blocked-on-bug`, never a spin. The gates below run **per proposal**, so a single controversial item
   can be dropped without losing the rest.
4. **Objective gate** (deterministic, no agent) — re-measure on the step-2 known-green baseline; **bounce
   the proposal** if any regressed: Efficacy dropped · a *new* Conformance violation appeared · a
   contract-preserving variant now kills a test it didn't before (brittleness up). **And bounce these
   anti-cheat checks even when the whole suite stays green** — a green suite proves nothing about a
   *quality* edit, which is the loop's entire reason to exist:
   - an **assertion removed, weakened, or made trivially true** (`assert True`);
   - a *new* assertion introduced **solely to pass the gate**;
   - an **asserted value changed to match observed output**;
   - an **expected value derived from the code under test** (a literal equal to a constant read from the
     module under test — a change-detector in disguise; also a Conformance-lint candidate).
5. **Subjective gate** (see *Review architecture*) — is the benefit real and plainly explainable, or
   over-fitting in disguise?
6. **Source-bug branch** (see *Source bugs*) if a sharpened test went red on a real defect.
7. **PR** — only if the surviving proposals clear a **"worth a human's attention" bar** (a real bug
   caught, coupling removed, a non-equivalent survivor killed); bundle or suppress lone cosmetic
   conformance nits rather than one PR per nit — the single-maintainer gate has finite throughput. One
   module, evidence-carrying body (see *PR body*). Human merges.
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

**Classify the finding's disposition** — exactly one, stated up front, so the human knows whether any
runtime behaviour is at stake. The discriminator is *pass-on-pristine*: assertions green on unmutated
code ⇒ the behaviour was already right, so this is coverage; red ⇒ a real bug to file or fix.
- **coverage-only** — the added/sharpened assertions pin *already-correct* behaviour; **no production
  change**. The default Sharpen outcome (mirrors ledger `state: sharpened`/`in-progress`).
- **issue-filed** — a real defect, or a non-equivalent survivor no test edit can kill, was found but
  **not** fixed here: link the issue, give severity, and say whether the assertion is withheld
  (green-trunk) or landed `xfail` (mirrors `grow-filed` / `state: blocked-on-bug`).
- **fix-included** — a source fix rode along, called out **separately** from the test per *Source
  bugs* (mirrors the source-bug branch).

## Review architecture

Two tiers — objective is cheap and deterministic; the debate is spent only where judgment is
irreducible.

- **Objective gate:** the deterministic tool re-measurement from the cycle's *Objective gate* step. No LLM. Deterministic bounce.
- **Subjective gate:** the **author** is *not* an independent reviewer — it wrote the edit — so shipping
  requires **two independent UPHOLDs**, not one. A **skeptic** agent (Opus, *different context* — sees
  only the factual *what* (target/axis/change) + the *diff*, never the author's *why*/rationale) reviews
  first; a **skeptic REFUTED drops immediately** (default-drop; the one independent voice found a problem
  and expected yield is low). On a skeptic UPHELD, a **second independent reviewer** — a **Sonnet judge**
  in its own isolated context, given the same *what*/*diff* and **not** the first skeptic's grounds —
  reviews adversarially; ship only if it **also** UPHOLDs. Either REFUTE → DROP. (Verification is easier
  than generation, so a Sonnet second reviewer is sound and cheap; iterative debate-to-consensus stays a
  trap — there is no back-and-forth, just two independent votes and default-drop.)

Seed the skeptic's veto list with the auto-test defects prior art hit repeatedly (each applies to a
*sharpen*, not just a generated test):
- **passes but adds nothing** — the edit neither kills a non-equivalent survivor nor tightens an
  observable behaviour (the anti-Goodhart core: a green, zero-value edit);
- **over-production** — several near-duplicate tests where one parametrised test is clearer;
- **change-detector-in-disguise** — the sharpened assertion pins an implementation detail or derives its
  expected value from the code under test (also a deterministic Objective-gate bounce, above);
- **implementation leakage in the test** — internal names, absolute line numbers, or tooling-provenance
  markers in the test body; prefer a semantic, line-number-free coverage note.

The veto criteria are not otherwise assumed complete — extend them as the loop's self-reflection or the
human surfaces new failure modes.

### Fidelity (enforced, not trusted)

The review only works if it is **actually adversarial** — one context cannot refute itself. So this is a
hard rule, structurally enforced, not a discipline:

- **Isolated subagents are mandatory.** Author, skeptic, and the second-reviewer judge (on the ship path)
  are **separate agent invocations with independent context**. Both reviewers get only the factual *what*
  (target/axis/change) and the *diff* — never the author's *why*/rationale (forwarding the persuasive
  rationale is the SycEval trap below; the harness strips it), and the judge is **not** told the first
  skeptic's grounds, so the two UPHOLD votes are genuinely independent. The loop runs as a **Workflow** where these are distinct `agent()` calls, so
  the harness enforces isolation; a human "playing all roles" in one context is **not a valid review**.
  The harness is [`harness.js`](harness.js) (Claude Code Workflow: `sharpen-loop`) — one module per run,
  `args.openPr` false ⇒ dry-run (ledger only), true ⇒ the worth-it PR (never merges).
- **Evidence over trust.** Every iteration records a `review` provenance block in the ledger: the author
  and skeptic agent ids (which must differ), the judge id or `consensus`, and the verdict. No block, or
  `author == skeptic`, means the review didn't happen.
- **No fidelity ⇒ no ship.** An iteration without a valid `review` block is a **`dry-run`**: it MUST NOT
  open a PR and MUST record `state: dry-run` (never `sharpened`/`in-progress`). The PR step checks for the
  block first. A dry-run is fine for *exploration*; it just can't reach the human gate as if reviewed.

**Why isolation, and its limit (grounded — SycEval, FAccT 2025, arXiv:2502.08177).** LLMs are sycophantic
in ~58% of challenged answers and the bias **persists** (78.5%) across a chain — so an independent skeptic
is justified and iterative debate-to-consensus is a trap (default-drop is right). But **isolation alone is
not the fix and can backfire**: SycEval found *preemptive* framing (a claim presented without the model's
own prior context — which is exactly our isolated skeptic) produced *higher* and more *regressive*
agreement (61.75% vs 56.52%; regressive 8.13% vs 3.54% on math). Consequences, now rules:
- **Frame the skeptic as adversarial refutation grounded in the artifact, not adjudication of the
  author's rationale.** Give it the diff + a task ("construct a bug this misses; default REFUTED on
  doubt") and make it reason from the *code* — this is what caught the author's mis-citation in the first
  real run.
- **Rationales cite evidence, never authority.** SycEval: *citation*-backed rebuttals maximised
  *regressive* sycophancy (flipping to wrong) — so a PR rationale leaning on citations/authorities makes
  the skeptic *more* likely to wave a bad change through. Cite mutants/tests, not sources.
- **Residual risk default-drop can't catch:** if author and skeptic *both* sycophantically agree on a bad
  change, it ships to the human. The merge gate is the only backstop for mutual sycophancy — which is why
  it stays.

## Grow hand-off — filing an uncovered risk

**Grow is the majority output, not the exception.** Most "test problems" are missing behaviour or a
missing public observation seam (this suite's inner-audit: 83/92 hits) — fixable only by a new test or a
code refactor, never by editing an existing test. A filed Grow issue is a **primary result** of a run,
not a failure to sharpen. Once filed, the gap is **filed-and-skip** (parallel to `source_sha`): triage must
not re-select a module whose real fix is an open Grow issue — record the id in the ledger's `grow-filed`.

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

## Ledger — `.ledger.sharpen.jsonl` (repo top level, committed)

Durable across cron runs; a PR cites it ("module X sharpened 3 weeks ago, source unchanged"). Header
record carries a global `toolset_version`. One record per module audit:

```jsonc
{ "module": "app/scoring.py",
  "audited": "<iso>", "source_sha": "<hash of module + its tests>",
  "toolset_version": 3,
  "axes": { "survival": 0.0, "conformance": 0, "brittleness": 0, "redundancy": "advisory:2" },
  "state": "sharpened | in-progress | blocked-on-bug | dry-run",
  "review": { "author": "<agent-id>", "skeptic": "<agent-id, ≠author>", "judge": "consensus", "verdict": "UPHELD" },
  "decisions": ["tightened test_mine to assert the note payload, not _cache"],
  "left-undone": ["3 equivalent survivors (str|None under __future__) — unkillable"],
  "grow-filed": ["#43"],
  "axes-not-applied": ["crosshair — z3 env not built this run; TODO"] }
```

`grow-filed` lists the Grow issue ids a run handed off; triage skips a module while its gap is open
(filed-and-skip, parallel to `source_sha` — see *Grow hand-off*). Terminal un-killable / un-sharpenable
outcomes land in `left-undone`; a real defect that blocked a sharpen sets `state: blocked-on-bug`.

**Sharpened** = clean on the three gating axes: **Efficacy** (no non-equivalent survivors, or all filed as
Grow issues) · **Conformance** (zero violations) · **Brittleness** (no witnesses). **Redundancy** is
advisory — recorded, never a sharpen-blocker. Any shippable state (`sharpened`/`in-progress`) additionally
requires a valid `review` block (see *Fidelity*); without one the state is `dry-run`. A sharpened module is
skipped until:
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
  **No axis, instrument, or context source is added on plausibility** — only after a **frozen-baseline
  A/B** (pin a revision, run with and without, keep it only if it moves a signal). The `toolset_version`
  bump follows a *proven* change, never a hoped-for one.

## Cadence & cost

Background cron / idle time only — **one module per run**, bounded. Never runs against a module under
active feature work. Slow, exhaustive, deliberate: this loop is allowed the long feedback the main
coding loop can't afford. `poe all` stays the fast local gate for the coding loop; this loop's
slow instruments never join it.

**Running the harness (operational).** Launch it from a **dedicated git worktree** (EnterWorktree →
Workflow → ExitWorktree) so an executor edit can never touch the maintainer's live tree — all executors
operate on paths relative to that worktree. The **Efficacy axis consumes a pre-built mutation campaign**
(`.mutation-cache/`, gitignored, reused across runs); it never launches one inline — a campaign outlives
a step budget, so `poe mutate <module>` is an out-of-band pre-req and the harness *defers* Efficacy
(Conformance-driven run) when the DB is absent. The audited-module allowlist is `poe mutate --list`
(canonical `TARGETS` in `tools/mutate/run.py`); the harness runs the Efficacy axis only for a listed
module and treats the rest as Conformance-only.

## Human gate

The maintainer approves every merge. The loop's entire job is to arrive at that gate with a small,
single-module, evidence-backed proposal that survived an adversarial internal review — so approval is
a *reading*, not an investigation.

## Deferred instruments — plan

Deferred for a stated reason, not forgotten; a later run or the outer reflection picks them up. Build
order follows yield-on-this-suite. Detailed step plans go to `vibe/` when a build actually starts.

1. **Conformance: private-monkeypatch-target** (ast-grep, ~½ day). Flag `monkeypatch.setattr` whose
   target is a **private** production symbol — the 2-arg string form (`"pkg.mod._priv"`) and the 3-arg
   form (`(obj, "_priv", …)`). This is the real coupling signal hiding among the ~666 *legitimate*
   monkeypatch seams (raw count is fine; a private target is the smell). Needs string-arg regex + the
   3-arg shape + planted rule-tests. **Highest-yield next rule.**
2. **Conformance: mis-levelled test** (ast-grep, ~½ day). Flag a real socket / subprocess /
   uncontrolled-fs / wall-clock call in a test carrying **no** `integration`/`live`/`e2e` marker.
   Deferred because it needs careful marker-absence matching (decorator-aware `not inside`) — a naive
   rule flags legitimately-marked tests. Scope by **marker**, not a `tests/unit/` dir (this suite has none).
3. **Brittleness probe** (Axis 3, ~1 week — the R1-deep recipe in the research note). Build with the
   **`rename-private-attribute`** operator, **not** `rename-local` (verified ~0 yield here — this suite
   has no `locals`/frame observation; its coupling is in private *attributes*). LibCST transformer in a
   pinned-3.13 env; TBE → Hypothesis-diff → CrossHair oracle; pytest full-failure capture; N×-seed
   flimsiness intersection; classifier → the R2 cause ids. **Build trigger:** the loop hits a Conformance
   finding it can't adjudicate by inspection (i.e. *hidden* coupling) — not the explicit private-attr
   coupling the lint already nails (the audit showed the probe is redundant there).
4. **Redundancy** (Axis 4, ~1 day, advisory only). cosmic-ray records no kill-matrix, so approximate with
   a **coverage-overlap** pass (`coverage.py` per-test line sets → identical/subset clusters) as a
   *flag-only* candidate list — **never auto-prune** (equivalent-mutant blind spots; tests double as
   regression / documentation guards).
5. **Conformance: expected-value-from-code-under-test** (ast-grep). Flag a test literal equal to a
   constant read from the module under test — asserting the code against itself, a change-detector in
   disguise. Pairs with the Objective-gate anti-cheat bounce of the same name.

---

## Research

The research (5 papers read at file level + a file-grounded GitHub survey, 2026-07) is distilled here; the
durable conclusions are already folded into this spec — the load-bearing ones:

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
- **Prior art & recalibrated novelty (grounded).** The closest existing artifact is `Jott2121/crucible`
  (fetched firsthand) — it assembles the *Efficacy + adversarial-critic + anti-cheat + receipts +
  frozen-baseline-A/B* core, **independently validating those parts of this spec**; study its
  `skills/`/`docs/` when building Efficacy, the PR-body receipts, and the outer-reflection A/B. But it is
  **Grow-flavoured** (writes new tests to kill mutants) with **no** Conformance/Brittleness/Redundancy
  axes, no ledger, no Sharpen focus. Two categories are **confirmed unbuilt anywhere** (papers + 3 GitHub
  hunts + direct search): the **behaviour-preserving-transform brittleness probe** (Axis 3) and a
  **source-hash-idempotent ledger bot**. So this loop's defensible novelty is *not* "mutation + critic"
  (that exists) — it is **Sharpen-refactoring of existing tests across four axes, with the brittleness
  probe and the ledger, integrated.** Reuse map: DSpot (`PitMutantScoreSelector` — mutation-selection),
  UTRefactor (DSL sharpen-recipes), citypaul/trailofbits configs ("test behaviour not implementation, verify
  with mutation") for the AGENTS-level rules.
