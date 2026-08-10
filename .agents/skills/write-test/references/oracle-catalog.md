# Oracle & invariant catalog

The **write-time menu of every oracle kind this repo actually runs** — the design contracts a feature
must satisfy *and* the platform-independent way to test it. Consult it when writing a feature
(design-time) or its test (author-time). Distilled from the Grow/Sharpen loop research so the knowledge
is at hand while building, not only in the idle loops.

**Why the menu must be complete.** An author picks from what's *listed here*; a kind that lives only in
scattered memory notes gets **re-invented per feature**. That is exactly how the *differential* kind was
rebuilt a 4th time for French (JP transforms, fsrs vectors, taffy gentest — then FR) despite three prior
instances in-tree, and how the *assembly integration* oracle was rediscovered from scratch. The families
below therefore span all three groups the repo realizes — **metamorphic/invariant**, **external-oracle**,
and **other realized kinds** — not just the metamorphic relations this file once listed.

## Two principles

- **Adequacy is not coverage.** Green + 100% line is a lower bound. The defect that ships lives in
  *covered-but-under-specified* code — a configuration, feature-combination, or scenario the line ran in
  but no oracle ever checked. Both motivating regressions were here (green at scale 1.0 where the hi-dpi
  crisp path no-ops; a navigated view combined with a key-gated feature). Ask "would a test go red if this
  behaviour broke in *this* config?", not "did the line run?".
- **Assert the oracle, not the pixels.** A raw pixel/bitmap equality is **platform-dependent** (FreeType
  AA/hinting differ across macOS/Windows/Linux) — it either flakes cross-platform or needs a per-OS golden.
  A metamorphic oracle (a *relation* between outputs) is platform-independent, so one assertion validates
  every OS for free. Reserve bitmap goldens for the few cases where geometry is the point, and then pin
  deterministic geometry (Ahem font) or an SSIM tolerance.

## Kinds inventory — one vocabulary

The `poe` *tiers* (unit / integration / live + the opt-in adequacy tools), the metamorphic *families*, and
the *external-oracle* kinds are three views that used to be described in three places. Reconciled, the kinds
actually realized in-tree are:

| Kind | Group | Detailed in | Canonical in-tree |
|---|---|---|---|
| agreement · scale-invariance · cache-equivalence · config-commutativity · feature-toggle · sub-pixel · concurrency · input-equivalence | metamorphic/invariant | *Metamorphic families* ↓ | `overlay/tests/test_scale_boundary.py` etc. |
| stolen conformance corpus (census-locked) | external-oracle | *External-oracle families* ↓ | `deinflect/tests/test_yomitan_transform_corpus.py` + `overlay/tools/corpus_check.py` |
| differential / reference-vector (from an authoritative impl) | external-oracle | *External-oracle families* ↓ | taffylite `tools/gen_taffy_fixtures.py`; `overlay/tools/gen_fsrs_vectors.py`; `deinflect/tools/gen_transform_differential.mjs` |
| assembly / pipeline integration oracle | external-oracle | *External-oracle families* ↓ | `overlay/tests/test_pipeline_oracle.py` |
| golden-image (deterministic geometry / MAE) | other | *Other realized kinds* ↓ | `overlay/tests/util.py::assert_golden` |
| property-based (Hypothesis) | other | *Other realized kinds* ↓ | `overlay/tests/test_sub_index_properties.py` |
| stateful / model-based | other | *Other realized kinds* ↓ | `overlay/tests/test_tooltip_statemachine.py` |
| humble-object boundary via `Fake*` | other | *Other realized kinds* ↓ | `overlay/tests/util.py` (`FakeMpvServer`/`FakeTransport`/`FakeAnki`) |
| perf-regression gate · crash-repro-as-artifact · install/bootstrap smoke | other | *Other realized kinds* ↓ | `overlay/tools/perf_gate.py`; `overlay/examples/subinterpreter_crash_repro.py`; `overlay/tools/install_smoke.py` |
| mutation · fuzz · symbolic | adequacy (not oracles) | `test-adequacy` skill | `tools/mutate/`, `poe fuzz`, `poe crosshair` |

The point-in-time **subsystem × kind coverage matrix** (which subsystem has which kind, and the ranked
gaps) lives beside this file in [`coverage-matrix.md`](coverage-matrix.md) — a dated audit, not a gate.

## Metamorphic / invariant families

Each is a relation that must hold; instantiate it against your seam. The canonical in-tree example is the
pattern to copy.

| Family | The invariant | Canonical example | Reach for it when |
|---|---|---|---|
| **Agreement / hit-test reciprocity** | every drawn element's displayed centre round-trips back to *that* element through the real hit path (`hit_target`) — render geometry and hit geometry can't drift | `tests/test_scale_boundary.py` (single action); `tests/test_tooltip_statemachine.py` (stateful, + the **one-panel invariant**) | you draw an interactive element and later hit-test a point against it |
| **Scale / display-invariance** | the element under a screen point is stable across display scale; geometry scales, *identity* does not | `tests/test_crisp_scale_properties.py`, `tests/test_scaled_viewport.py` | anything that renders at a `scale`/DPI factor (the shipped hi-dpi regression) |
| **Warm == cold cache-equivalence** | a cached result is byte-identical to a freshly computed one — a cache is a pure accelerator, never a behaviour change | `tests/test_cache_equivalence.py` | every memo / atlas / render / band cache you add |
| **Eviction / idempotence / monotonicity** | evict-then-recompute == recompute; a re-put is a no-op; capacity math holds under churn | `tests/test_cache_equivalence.py`, `tests/test_block_cache.py` | any bounded / LRU cache |
| **Config commutativity** | orthogonal configuration applied in any order yields the same state | the config-matrix profiles (`tests/util.py`) | independent config knobs / init-order sensitivity |
| **Sub-pixel-shift tolerance** | a 0.1px input shift moves a box ≤1px — an aggressive reflow on a tiny perturbation is a bug | `tests/test_crisp_scale_properties.py` | layout / rounding boundaries |
| **Feature-toggle consistency** (the *combination* oracle) | a feature's behaviour/geometry is invariant under toggling an **unrelated** feature; the agreement oracle must hold in *every* feature combination | `tests/test_tooltip_statemachine.py` (arbitrary `hover/scroll/navigate/back/open_nested/resize` sequences) | two features can be on together (the navigated-view × key-gated-feature regression); this IS the UI/UX-consistency check |
| **Concurrency oracles** | under a forced interleaving: concurrent warm==cold · first-writer-wins identity stable · no-lost-update (evicted count == single-threaded math) · no-corruption (backing structure intact at teardown) | `tests/test_cache_race.py` (`blanket`-scripted, deterministic) | **every shared cache/registry** touched by >1 thread — the `942ca3c` TOCTOU class, fixed reactively 4× (panel/crisp/entry/band). Design a locked or first-writer-wins seam AND ship the race gate from day one |
| **Input-equivalence** (metamorphic, no answer key) | a *meaning-preserving* input transform leaves the output identical — CRLF↔LF, trailing blank lines, redundant whitespace | `tests/test_subtitle_metamorphic.py` (parse invariance) | a parser / tokenizer / normalizer where a golden pins only finite fixed inputs; complements the stolen-corpus golden by catching the formatting bugs it misses |

**Pattern menus to instantiate from** (no importable graphics/UI catalog exists — author against your seam):
Dwyer specification patterns (Absence / Universality / Existence / Precedence / Response × scopes) → cache
monotonicity, eviction-invariance, back-stack ordering; Segura et al. metamorphic-relation patterns
(input-/output-equivalence, combinatorial, permutative) → the config matrix and cache key/value relations.

## External-oracle families

A metamorphic oracle needs no answer key. These do: they pin behaviour against an **authoritative external
source** (an upstream conformance suite, a reference implementation, or the real end-to-end assembly). Use
one whenever "correct" is defined by something outside our own reading of the spec — a ported algorithm, a
copied formula, a multi-component wiring. All three vendor the answer key as committed data and read it
hermetically (no network / Node / live process in `poe all`); a *drift guard* (`poe corpus-drift`,
regenerate-and-diff, off the default gate) catches a stale key.

| Family | The oracle | Canonical example | Reach for it when |
|---|---|---|---|
| **Stolen conformance corpus** (census-locked) | our port reproduces an upstream suite's own vectors; the corpus's case census (count + key-hash) is bound to a manifest so a re-vendor can't silently shrink it | `deinflect/tests/test_yomitan_transform_corpus.py` (transcribes Yomitan's `japanese-transforms.test.js`); locked by `overlay/tools/corpus_check.py` (`poe corpus-lock`, in `all`) | you port an algorithm from a project that **ships its own test vectors** (UAX #14, Yomitan JP transforms, subtitle) — steal them |
| **Differential / reference-vector** (generated from the authoritative impl) | run the *real* upstream implementation over inputs, record `(input → output)`, assert our port reproduces each. Agreement is expected **by construction**, so a diff is a pure port/transcription-bug detector | **taffylite `tools/gen_taffy_fixtures.py` → `taffy_gentest_flex.json`** (most mature); `overlay/tools/gen_fsrs_vectors.py` → `test_fsrs_reference_vectors.py`; `deinflect/tools/gen_transform_differential.mjs` → `test_transform_differential_corpus.py` (French) | you port/copy an algorithm whose upstream has **no stealable suite** — generate the vectors instead. The **reusable mechanism** for a transform grammar is `gen_transform_differential.mjs` (parametrized by language) — add a `LANGS` row, don't rebuild it |
| **Assembly / pipeline integration oracle** | drive the **real** multi-component assembly end-to-end (`config → build deps → …→ output`) and assert structural invariants — never pixels. Catches wiring bugs that live *between* green-unit components | `overlay/tests/test_pipeline_oracle.py` (`(cue, hover pos) → Entry` over the real run path; every 2026-08-10 French bug lived here) | a defect can hide in the **wiring** of components that each pass their own unit tests (the `language="jp"` mis-thread) |

## Other realized kinds

Not metamorphic relations and not external oracles, but part of the menu — reach for the right one instead
of defaulting to a bespoke assert.

| Kind | What it is | Canonical example | Reach for it when |
|---|---|---|---|
| **Golden-image** | a committed reference bitmap/trace, diffed with **deterministic geometry** (Ahem) or an MAE/SSIM tolerance — the diff *is* the behaviour review, re-blessed deliberately | `overlay/tests/util.py::assert_golden` (users: `test_interaction.py`, `test_kanji.py`) | geometry/layout *is* the point and a metamorphic relation can't pin it; pair with an invariant oracle, never rely on pixels alone |
| **Property-based** (Hypothesis) | a `@given` strategy explores inputs; killed mutants/crashers become pinned `@example`s | `overlay/tests/test_sub_index_properties.py` | a pure-core invariant holds over a large input space a fixed table under-samples |
| **Stateful / model-based** | a `RuleBasedStateMachine` drives arbitrary action sequences against a model; the combination oracle rides on it | `overlay/tests/test_tooltip_statemachine.py` | interacting stateful features whose *sequences* (not single acts) carry the bug |
| **Humble-object boundary** (`Fake*`) | keep I/O glue dumb; test the logic against a hand-built fake of the out-of-process collaborator | `overlay/tests/util.py` (`FakeMpvServer` / `FakeTransport` / `FakeAnki`) | logic sits behind a socket/subprocess/display — never unit-test the glue itself |
| **Perf-regression gate** | a measured hot-path number checked against a committed baseline/budget, continuous history | `overlay/tools/perf_gate.py`, `overlay/examples/bench_*`, `jank_live` | a change can silently regress a latency/jank budget (isolate with py-spy self-time, per BENCHMARKS) |
| **Crash-repro-as-artifact** | a minimal committed reproducer for a crash class; fuzz/crosshair crashers graduate to pinned `@example`s | `overlay/examples/subinterpreter_crash_repro.py` | you fixed a crash whose *shape* (not just the one input) must stay dead |
| **Install / bootstrap smoke** | a cross-OS `uv tool install` + doctor smoke, bounded so it can't false-pass | `overlay/tools/install_smoke.py` (`e2e.yml`) | packaging / entry-points / first-run can break independently of the code |

## Every oracle test ships a negative control

A self-consistent oracle that *can't* fail is worthless. Pair each oracle with a permanent `*_has_teeth`
control that feeds a **deliberately-broken** input and asserts the oracle FIRES (mis-hits / diverges) —
e.g. `test_the_agreement_oracle_has_teeth` in `tests/test_tooltip_statemachine.py`, which drifts the
transform and proves the round-trip then mis-hits.

## The config matrix — extend-before-add

The shared scale × width × view × backend profiles live in `tests/util.py` (`PROFILES`). A new corner is an
**appended** `PROFILES` row / a `parametrize` case / an `@example` — one row, every property inherits it —
never a near-duplicate new file. Curate the profiles (representative, not the full cross-product) and
parametrise-outer / `given`-inner to avoid combinatorial explosion. *Adding* an assertion is a growth move;
*changing* an existing one is Sharpen's job — keep them separate, or a change-detector slips in.

## Prove an oracle is load-bearing

A green oracle test proves nothing on its own.

- **In the coding loop (cheap):** `uv run poe test-live tests/test_x.py --test <name>` negates each assert
  and re-runs — every live oracle must flip the test red. A test that stays green when its own assert is
  negated is vacuous (a swallowed exception, an unreachable assert after an early return, a tautology).
- **In the idle Grow loop (deep):** the four-arm gate (`overlay/tools/grow_gate.py`) additionally proves
  *genuine growth* — a scenario-encoding mutant the pre-existing suite missed — and newly-exercised lines.
  See `.agents/grow/{GUIDE,SPEC}.md`.

## Provenance

Grow loop (`.agents/grow/GUIDE.md`) and Sharpen loop (`.agents/sharpen/GUIDE.md`); the integration/e2e
coverage research (render↔hit-test agreement, cache-equivalence, config matrix, "assert oracles not
pixels"); pattern menus from Dwyer (specification patterns) and Segura et al. (metamorphic relations). The
external-oracle families + the [`coverage-matrix.md`](coverage-matrix.md) audit fold in the conformance /
differential / assembly research that previously lived only in memory notes, so the write-time menu is
complete — the fix for the differential-kind being rebuilt a 4th time before it was ever listed here.
