# Run 1 — 2026-08-22 — `render/banded.py` and the tooltip render path

**Scope:** scoped (not whole-repo) — `src/saitenka/render/banded.py` plus `app/tooltip_panel.py`,
`app/popups.py`, `app/tooltip_raster.py`, `render/window.py`. Run right after the warm-only main
thread landed on `codex/runtime-wp4-playback`.

**Agenda:** 13 `argued` rows from the first claim census of the module, handed over as claims to
attack. Isolated reviewer, no conversation context.

**Outcome:** two findings fixed the same day (`a2b8f1c4`); the rest carried into `census.json`.

---

## Verdict (reviewer)

The core geometry is excellent — exact, seam-proven, genuinely O(viewport). What had rotted is the
warm/cold bookkeeping on top: three warmth answers computed over three different band-enumeration
rules, with nothing making them agree. At 1080p with a tall entry, `missed_last_assemble` was
structurally always true, which latched `crisp_pending`, which made the poll loop re-composite and
re-upload the whole tooltip every 25 ms tick — against a docstring asserting the opposite. Separately
`_evict` no longer ran on any production path, so the class docstring's retained-pixel bound was
false in the shipped app.

`ARCHITECTURE.md` Stage 6 still documents the pre-warm-only engine. `poe all` contains no gate
touching any of this; every claim in the module is enforced by prose alone.

## Agenda results

| row | outcome | discriminator |
| --- | --- | --- |
| B3 `_on_render_loop` correct for both builds | survives as a predicate, **falsified as a guard** | the pool submits `render_body_band`, which never calls it; `_GUARD_MAIN_RENDER` is a parent-process global a spawned child re-imports as `False`. Neither non-main-thread half is exercised — all three arm sites are on the main thread |
| B4 `viewport_warm` overscan gap | **falsified, and it bites** | reproduced: `viewport_warm(0,300) is True` while `missed_last_assemble is True` after the compose `blit_panel` actually issues |
| B6 `last_frame_rasters` honesty | survives the race, falsified as documentation | the `n0`→delta window sits inside one `RLock` hold, so a worker cannot interleave; the comment at `banded.py:331` states a different, now-false reason |
| B7 `assert_no_interactive_raster` | **falsified** — single-thread-correct only | the counter is panel-wide; it reads 0 only because those tests run no worker |
| B8 duplicate raster benign | survives on pixels, falsified on meters | `CachedBlock` frozen and `_store`/`_add_geom` dedup under the lock; but each duplicate bumps `_sync_rasters`, `block_cache_misses`, `block_rendered_px` |
| B9 `set_height` idempotent | survives | `window.py:139` — pure assignment + memo drop; `warm_measure_to` re-checks `known(i)` under the lock |
| B10 immutable below the snapshot | survives | `to_bgra_array` ends in `np.ascontiguousarray`, so no memo aliases; the one in-place mutator gets a fresh array or a `.copy()` |
| B11 lock annotations | **the census row was wrong** | no live precondition violation; two *other* methods are misclassified — `precompose` and `_raster_scaled_band` ("lock-free half", acquires the lock) |
| B12 `_BAND_PX` ~9 ms | **falsified** | no band timing in `BENCHMARKS.md`; the cited plan file is git-ignored scratch that no longer exists |
| B16 `_assemble_warm_1x` equivalence | survives a spot check, genuinely unoracled | byte-identical over 5 rows × 6 offsets, but the only `warm_only` test in the tree was at `scale=2.0` |
| B17 `_row_band_spans` vs `_ensure_bands` | **falsified** | four query sites used the raw tiler: `_warm_plan`, `_assemble_bgra`, `_missing_regions`, `_warm_row` |
| B20 `render_ahead` overscan | falsified as prose, no live consequence | no production caller uses the default; only the prefetch tests do |

## Findings off the agenda

### P0 — `missed_last_assemble` is a permanent false positive; the tooltip re-uploads itself every tick

`_warm_plan` set `missing = True` for **any** uncached band of **any** visible row — not the bands
the frame reads. `_blit_native` latched `crisp_pending` from it; `apply_pending_crisp` cleared on
`viewport_warm`, which asked the narrower question. Closed loop.

**Discriminator** (1080p, tall entry, `full_height=4908`, `view_h=648`): `viewport_warm=True` with
`crisp_pending=True, partial=True` on every tick, rect unchanged, 2.44–4.96 ms main-thread CPU per
tick and one `overlay-add` per tick against a 25 ms poll interval and a `< 5 ms` KPI.
*Reviewer's own harness, fake IPC — the live count was left in "could not verify".*

Independently reproduced against `WindowedPanel` directly, and the two causes separated: overscan
dominates at realistic input, the unfiltered tiler bites at `overscan=0` on a tall row. **Age: new** —
introduced by the warm-only migration under review.

**Fixed** `a2b8f1c4`: count a band missing only when `_clip_band` places it in the frame, and derive
`viewport_warm` from the same plan.

### P1 — `_evict` never runs on the interactive path

Reached only from `_viewport_bgra_locked` and `viewport()`; the shipped main thread takes
`_assemble_warm_1x`. **Discriminator:** 4.00 → 9.47 → 12.29 MB across eight notches, `_cap=128` never
engaging. Reproduced at 0.51 → 3.29 MB on a smaller panel. **Age: new**, same migration.

**Fixed** `a2b8f1c4`, evicting over the lookahead's whole reach rather than the compose's overscan —
the narrower window drops what `render_ahead` just warmed.

### P1 — the `_row_bands` / `_row_band_spans` split is a live divergence

A non-body row taller than `_BAND_PX` is stored as one band; the tiler invents a second that is
missing forever. Also makes `skeleton_frame` paint grey over correct pixels. **Fixed** `a2b8f1c4`.

### P2 — the production 1× compositor was untested

Every banded test drove `viewport()`/`viewport_bgra()` **non-warm** — the rastering, evicting path
that `guard_main_render` exists to forbid on the main thread. That is why all three findings above
were simultaneously green. **Fixed** by `tests/test_warm_compose.py`.

### P2 — `last_frame_rasters` / `missed_last_assemble` are panel-wide fields read outside the lock
### P2 — `ARCHITECTURE.md` Stage 6 documents the previous engine
### P3 — `skeleton_frame` has no production caller; the whole RGBA tier is bench/test-only; `_geom_seen` is never cleared; four docstring/reality mismatches; process scars and a dangling `vibe/` reference

## What is genuinely good (reviewer)

`render/window.py` as a pure core — PIL-free, integer-exact, with its invariant stated once in the
right place, which is why the geometry produced no findings. `_clip_band`, because two compositors
that cannot express clipping differently cannot drift. The band as the unit of raster, cache *and*
eviction. `_render_ahead_parallel` deriving dispatch from the executor in hand — a structural
impossibility argument, not a comment. The measure/raster split. Geometry retained past pixel
eviction. `_NativeView` collapsing a four-value quad plus its device derivations.

## The principles answer (reviewer)

Soft-first/crisp-later is bought and paid for: soft-only loses hi-dpi crispness (the regression the
scale-boundary rewrite fixed); crisp-only costs ~28 ms with no cancel point against ~2 ms for the 1×
warm, so a cold hover would blow the 16 ms budget every time. Keep warm-only on the main thread
unreservedly — finish it, so the guard has nothing left to guard. Keep per-band locking's
granularity but cut the *number* of hand-written "call under the lock" contracts. `_first_view` is
not a tier; it is a memoised answer to one query and should live inside the 1× tier.

## What could not be verified

| claim | why | what would settle it |
| --- | --- | --- |
| `_BAND_PX = 256` is the right unit | no band timing anywhere; `perf-check` is not in `all` | a py-spy/Scalene self-time run at production width across 128/256/512, dated into `BENCHMARKS.md` |
| the four newer perf numbers in docstrings | cite no artifact | same |
| the GIL-build ProcessPoolExecutor path works end to end | no test exercises it | a GIL-build integration test asserting pixel parity with the threaded path |
| whether any *real* non-body row exceeds 256 px | reproduced synthetically only | instrument `_ensure_bands`' non-body branch across the pathological corpus. If never, B17's whole class can be **deleted** rather than unified |
| whether P0 was visible to a user in real mpv | measured against a fake IPC | `poe smoke-live`, tall entry held open 30 s, counting `overlay-add` — expected 1 |
| whether duplicate rasters occur in production | the lane is `capacity=1` | a counter on `_warm_row`'s post-raster membership check |

## Notes for the next run

- The agenda did **not** tunnel the reviewer: both fixed findings were off-agenda, and the reviewer
  corrected a census row (B11). One run; see the skill's discard rule.
- The census's own error mode showed up immediately: B4 was scored *harmless*, and it was the P0.
  A census row is a claim, not a finding.
