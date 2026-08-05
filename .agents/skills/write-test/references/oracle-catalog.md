# Oracle & invariant catalog

The menu of **metamorphic / invariant oracles** to assert — the design contracts a feature must satisfy
*and* the platform-independent way to test it. Consult it when writing a rendering / cache / config /
interaction feature (design-time) or its test (author-time). Distilled from the Grow/Sharpen loop research
so the knowledge is at hand while building, not only in the idle loops.

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

## The families

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

**Pattern menus to instantiate from** (no importable graphics/UI catalog exists — author against your seam):
Dwyer specification patterns (Absence / Universality / Existence / Precedence / Response × scopes) → cache
monotonicity, eviction-invariance, back-stack ordering; Segura et al. metamorphic-relation patterns
(input-/output-equivalence, combinatorial, permutative) → the config matrix and cache key/value relations.

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
pixels"); pattern menus from Dwyer (specification patterns) and Segura et al. (metamorphic relations).
