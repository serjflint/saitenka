# Responsiveness benchmark — in-mpv tooltip

The hosted benchmark portfolio, replica statistics, and CI publishing contract are documented in
[`docs/contributing/continuous-benchmarks.md`](docs/contributing/continuous-benchmarks.md). This file
keeps dated measurements and profiler evidence.

The perceived snappiness of the overlay is gated by a handful of latencies. This is the saved baseline
so future changes can be compared against it. Regenerate with:

```
uv run python examples/bench_responsiveness.py --reps 12
```

It runs headless against the real dict set via a fake mpv IPC, so numbers **exclude mpv's own
compositing + the socket round-trip** (a small, ~constant add) but include the real dictionary lookups,
structured-content layout, BGRA conversion, and the temp-file upload write. "Cold" = OS/SQLite page
cache warm but our per-word panel cache cleared (a fresh word mid-session); the very first hover after
launch is slower because the 1.3 GB MonoB index is read from disk once.

## KPIs and targets

Ranked by what the eye notices. These are the numbers to watch for regressions:

| KPI | Why it matters | Target |
|---|---|---|
| **Warm hover** (prefetched → shown) | the *common* case — prefetch warms the line while you read | **< 16 ms** |
| **Cold first paint** (hover → first pixels) | the headline; the viewport-first head | **p50 < 100 ms, p95 < 250 ms** |
| **Scroll frame** (one wheel step) | must stay under one display frame or scrolling stutters | **< 16 ms (60 fps)** |
| **Poll-tick hover hit-test** | per-tick cost must be tiny vs the 25 ms poll interval | **< 5 ms** |
| **Nested popup first paint** | first paint for an inner (scanned) word | **< 150 ms** |

Secondary / diagnostic only: time-to-complete (the tail streams in behind the head, so it isn't
blocking), cold sweep total (mitigated by prefetch), and the lookup / head-render / BGRA components
(for locating *where* a regression is). "Scroll speed" is not a latency — it's px/step
(`round(osd·0.12)` ≈ 130 px, coalesced per tick); what makes it feel good is the frame cost above.

## Baseline — 2026-07-21

Env: Apple M3 Pro · macOS 25.5.0 (arm64) · Python 3.13.5 · overlay commit `9d1864e` · single-threaded
(prefetch off, so the head path is measured directly). Line `門前の小僧習わぬ経を読む`, 1080p,
`tip_width` 640, `cap` 648 px. Dict set: 6 dicts + 7 freq + 1 pitch (`~/.config/saitenka/overlay.toml`).

| metric | p50 | p95 | mean | min | (ms) |
|---|---|---|---|---|---|
| first paint (cold: head render + upload) | 49.0 | 510.7 | 116.9 | 26.3 | |
| time-to-complete (finish deferred tail) | 138.0 | 152.0 | 139.9 | 131.9 | |
| warm hover (prefetched → upload only) | 1.3 | 5.2 | 2.1 | 0.7 | |
| scroll frame (one 130 px step) | 1.9 | 8.6 | 3.0 | 0.7 | |
| nested popup first paint (inner word) | 121.1 | 129.0 | 121.6 | 115.4 | |
| poll tick hover hit-test (`_update_hover`) | 0.4 | 0.5 | 0.5 | 0.4 | |
| horizontal sweep: cold, 5 words (total) | 539.5 | 735.8 | 584.6 | 503.4 | |
| horizontal sweep: warm, 5 words (total) | 5.0 | 20.1 | 7.2 | 3.4 | |
| *component:* dict lookup, 5 words | 36.3 | 52.5 | 36.7 | 30.7 | |
| *component:* head render, 5 words | 330.6 | 350.0 | 330.6 | 312.7 | |
| *component:* BGRA convert, tallest head | 89.2 | 102.2 | 92.1 | 87.4 | |

Verdict: warm hover, scroll, and hit-test are all far inside budget; cold first paint p50 is instant.

## Known weakness

Cold first-paint **p95 (~510 ms)** and BGRA-of-tallest (~90 ms): viewport-first renders **whole rows**
until it covers `cap`, so if a word's *first* definition body is very tall (a big MonoB entry), the
"head" overshoots to ~2000 px and costs nearly as much as the full panel. The ~860 ms → ~50 ms win
holds for typical words but not for a word whose first dict entry is enormous. Lever (future item):
**clip / stream the first def body itself**, not just defer later bodies.

## Pathological corpus — baseline 2026-07-21 (before Stage 6/7 levers)

The worst first-lookup words: the 3 largest-glossary entries per dict (auto-discovered from the built
SQLite indexes) + hand-picked multi-sense words. Regenerate with:

```
uv run python examples/bench_responsiveness.py --pathological --reps 8
```

Env: Apple M3 Pro · macOS 25.5.0 · Python 3.14.6 (3.14t) · 1080p · tip_width 640 · cap 648 px ·
6 dicts + 7 freq + 1 pitch. **Targets: cold p95 < 150 ms per word · first-hover-after-launch < 300 ms.**

First-hover-after-launch (fresh SQLite connections, 上げる): **290.8 ms** (target met, barely; OS file
cache warm — no sudo purge).

| word | source | p50 | p95 | max | (ms) |
|---|---|---|---|---|---|
| 上げる | Bilingual | 181.8 | 183.7 | 183.7 | |
| 挙げる | Bilingual | 178.5 | 187.5 | 187.5 | |
| 揚げる | Bilingual | 180.0 | 181.6 | 181.6 | |
| 気 | Bilingual2 | 234.7 | 244.0 | 244.0 | |
| 手 | Bilingual2 | 157.4 | 158.8 | 158.8 | |
| 目 | Bilingual2 | 168.5 | 202.0 | 202.0 | |
| に | MonoC | 94.5 | 96.9 | 96.9 | |
| の | MonoC | 82.4 | 84.3 | 84.3 | |
| 取る | MonoC | 323.9 | 326.5 | 326.5 | |
| 眼 | MonoD | 170.6 | 172.7 | 172.7 | |
| とる | MonoB | 328.2 | 329.2 | 329.2 | |
| 捕らぬ狸の皮算用 | MonoB | 124.6 | 125.6 | 125.6 | |
| 取るに足りない | MonoB | 125.2 | 128.0 | 128.0 | |
| 執る | MonoE | 321.7 | 329.0 | 329.0 | |
| 採る | MonoE | 326.4 | 338.8 | 338.8 | |
| 出る | hand-picked | 131.9 | 137.2 | 137.2 | |
| かける | hand-picked | 247.3 | 284.8 | 284.8 | |
| 見る | hand-picked | 89.4 | 91.6 | 91.6 | |
| 行く | hand-picked | 116.6 | 118.3 | 118.3 | |
| いい | hand-picked | 46.4 | 47.1 | 47.1 | |
| **WORST** | over all words | **328.2** | **338.8** | **338.8** | |

Verdict: 9 of 20 words MISS the 150 ms p95 target — the 取る family (~330 ms) and 気/かける (~250–285 ms)
are the words whose first def body is a single enormous block. This is the Stage 6 lever's job.

## After Stage 6 — deferred walk + mid-def raster clip (2026-07-21)

Profiling showed the assumed culprit (raster overshoot) was only half the story: `panel_rows` walked
EVERY def's structured content eagerly at build time, and the SC-walk of one 取る-class def alone costs
~230 ms. Stage 6 therefore (a) moved the walk inside the deferred row thunks (one row per def body —
the head only walks the defs the viewport shows), and (b) added mid-def raster clipping
(`render_document`/`render_flow` `max_height`): the boundary def paints only the covering strip and
finish() re-renders it fully, so the composed full panel stays byte-identical.

Pathological corpus after Stage 6 (same env/flags):

| KPI | baseline | after Stage 6 | target |
|---|---|---|---|
| WORST cold p50 (over 20 words) | 328.2 ms | **127.8 ms** | |
| WORST cold p95 | 338.8 ms | **132.1 ms** | < 150 ms ✅ (all 20 words) |
| WORST cold max | 338.8 ms | 132.1 ms | |
| first-hover-after-launch (上げる) | 290.8 ms | **118.7 ms** | < 300 ms ✅ |

Standard smoke-line benchmark also improved across the board (reps 8):

| metric | baseline (2026-07-21) | after Stage 6 |
|---|---|---|
| first paint cold p50 / p95 | 49.0 / 510.7 | **21.8 / 46.1** |
| nested popup first paint p50 | 121.1 | **34.1** |
| horizontal sweep cold (5 words) p50 | 539.5 | **150.0** |
| BGRA convert, tallest head | 89.2 | **6.3** (head is now a bounded strip) |
| warm hover p50 / scroll frame p50 | 1.3 / 1.9 | 0.5 / 0.6 |

## After Stage 7 — BGRA LUT · SQLite mmap · observe_property (2026-07-21)

Three independent levers, all byte-identical / behavior-preserving:

1. **BGRA LUT** (`bgra.to_bgra_array`, compatibility-exported by `osd`): the per-pixel uint16 widen×multiply÷255 premultiply replaced
   by a flat `np.take` gather from a precomputed 256×256 table (64 KB, L2-resident). Property test
   pins byte-identity vs the reference formula over random RGBA.
2. **SQLite mmap** (`dictionary.Dictionary._conn`): `PRAGMA mmap_size=1073741824` +
   `cache_size=-65536` (64 MiB) on every read-only per-thread connection — cold lookups hit mapped
   memory instead of pread round-trips.
3. **observe_property** (`controller`): `sub-text`/`mouse-pos`/`osd-dimensions`/`pause`/
   `secondary-sub-text` are now event-driven — `run()` registers `observe_property` + one seeding
   read each, and the poll loop consumes buffered `property-change` events. The 3–5 blocking
   `get_property` round-trips per 25 ms tick are gone (this saves real-mpv socket latency that the
   fake-IPC benchmark below cannot see). Dwell/hysteresis timers still tick on the loop.

Pathological corpus (same env/flags): WORST cold p95 **132.1 → 133.2 ms** (noise-level — the corpus is
CPU-bound and page-cache-warm, so levers 2–3 don't show here), first-hover-after-launch 118.7 →
118.4 ms. Standard smoke line: first paint cold p50/p95 29.3/64.4, sweep cold 145.0, warm hover 0.5,
scroll 0.5 — all within noise of the post-Stage-6 numbers. The mmap + observe_property wins are in
disk-cold first hovers and live-mpv tick latency, both outside this harness's measurement envelope;
targets remain met with margin (cold p95 < 150 ms ✅ all words · first-hover < 300 ms ✅).

## Harness upgrades — tail latency, GIL guardrail, upload isolation, stress (2026-07-22, v0.2.0)

Since this is a **real-time overlay** (it must not stall the poll loop or drop a video frame), the
harness now reports the jank tail and the runtime that produced it, not just means:

- **p99 + CV** on every metric. p99 is the jank tail (a p99 over the 16.7/33 ms frame budget drops a
  frame even when p50 looks fine); CV (stdev/mean) is the run-to-run stability that decides whether a
  metric is safe to regression-gate at all.
- **Runtime line + GIL guardrail.** Every run records `Py_GIL_DISABLED` and the *live* `sys._is_gil_enabled()`
  read **after** the workload (fugashi re-enables the GIL on first use, not at import). `--require-ft`
  fails the run if the GIL came back — catching the silent worker-scaling collapse.
- **Layer-isolated timing.** The cold path is split into `dict lookup` / `head render` / `BGRA convert`
  / **`upload write` (warm reuse vs cold fresh+fsync)**. This settled the suspected ~55 ms temp-file
  "floor": the write is **~1 ms** (warm and cold alike) — the number in older notes was the whole cold
  first-paint, which is **render + lookup bound**, not IO. So mmap/shared-memory upload is not worth it.
- **`--json`** emits a diffable baseline (metrics + runtime).

### `--stress` — sustained chained session

`bench_responsiveness.py --stress` chains cold hover → scroll → nested popup → scroll → dismiss over
60+ distinct heavy entries for N rounds, surfacing what the isolated micro-benchmarks can't: panel-cache
eviction thrash (the 48-entry LRU cap), nested-state churn, and memory growth across a session. It
reports the per-op frame-latency tail (**MAX** = the jank signal) + peak RSS + growth, and can gate on
`--max-frame-ms` / `--max-rss-mb`. The robustness half is always-on in the gate (`tests/test_stress.py`:
no crash, cache stays ≤ 48, tooltip/nested overlays torn down with no ghost).

First run on the full 19-dict rig flagged **MAX ~950 ms / p99 ~920 ms** per op — cold pathological
monolingual entries blowing the frame budget under load (the known cold-p95 weakness, now visible in a
sustained scenario). Confirms the open lever: **clip/stream the first def body**, not just defer later ones.

### `--timeline` — idle-paced session (the felt-experience ground truth)

`--stress` is deliberately worst-case: zero idle time, a shrunk 24-entry cache (vs. the real 128
default), back-to-back heavy words. Real usage is the opposite — idle dominates (video plays, mouse
doesn't move) punctuated by occasional hovers, with the background prefetch worker (`prefetch_lookahead`)
warming ahead during the idle gaps. `--timeline` (`vibe/hot-path-idle-spreading-plan.md` Stage 1) models
that: synthetic subtitle cues built from the real episode vocabulary (`examples/vocab.json` — 608 words
from one Nippon Sangoku episode), advanced on a real clock (`time.sleep` between cues, so the real
prefetch threads get real wall-clock idle time, not simulated time), with occasional injected hovers.
Reports hover latency split **idle-warm** (the word's dictionary entries were already decoded before the
hover) vs. **cold** (decoded synchronously, on hover), plus the worker's **lead time** (enqueued-as-upcoming
→ decoded) against the idle budget it actually had (`lookahead × dwell`).

> **The 2026-07-26 baseline below is not comparable to a current run.** Prefetch moved onto a
> registered runtime job lane, and the bench's `FakeIPC` inherits `NoSessionRuntime`, whose
> `register_runtime_job_lane` returns `False` — so `start_prefetch` returned early and every run
> between that migration and 2026-08-22 measured a SessionController with **no prefetch at all**, reporting the
> resulting synchronous decodes as "the worker fell behind". The harness now installs a real gateway
> (`_runtime_ipc`); re-baseline before comparing.

Baseline — 2026-07-26, defaults (`--timeline-cues 80 --timeline-dwell-s 0.3 --timeline-lookahead 3`,
900ms idle budget), 9 dicts + 9 freq + 1 pitch:

| metric | p50 | p95 | max | n |
|---|---|---|---|---|
| hover latency — idle-warm | 36.8 | 75.7 | 75.7 | 20 |
| hover latency — cold | — | — | — | 0 |
| worker lead time (enqueued → decoded) | 175.5 | 410.6 | 410.6 | 19 |

**0/20 misses** — at this dwell/lookahead the worker never fell behind; every hover in the run landed
on an already-decoded word. **But "idle-warm" is not free**: idle-time warming (Stage 2) is decode-only
(`entry_for`, no layout) — it does *not* pre-render the panel (Stage 4, pre-compiling heads, is explicitly
deferred/opt-in because a pathological word's full render costs seconds, see `panel_mem.py` in the plan).
So a first hover on a passively-watched (never engaged/paused) line still pays real layout + BGRA + upload
cost even when fully idle-warm — this 36.8ms p50 is *that* cost, not the ~1-2ms "warm hover" KPI above,
which assumes a FULL panel prefetch (only triggered once the user is already engaged — paused or hovering
the video — which normal passive reading isn't, until the moment of the hover itself). Confirms the plan's
Stage 4 lever (opt-in current-line head pre-compile) is the next step if this 30-75ms first-hover cost on
a fresh line ever needs to shrink further; not yet built.

### Profiling & continuous benchmarking (verified 2026-07)

To *explain* a regression (not just report it): **Scalene** is the one profiler verified to work on
free-threaded 3.13t/3.14t (full CPU+memory, with a GIL-activity timeline) — use it for "is this
GIL/native/alloc bound". `py-spy --native` and `viztracer` are great for GIL-on runs but their
free-threaded support is **unconfirmed** (both read/monitor interpreter internals that no-GIL changes) —
check their trackers before relying. `pytest-benchmark` auto-disables under `pytest-xdist`, so any
micro-suite must run serially, separate from the `-n auto` gate — the custom harness stays primary.

## Gating a noisy metric — quantile, sample count, bound (2026-08-25)

The repo runs four benchmark gates. Two arrived at the right policy independently, one had to be
fixed after five CI failures in forty runs, and none of them stated the policy anywhere a reader of
the other three would find it. This is that statement.

### A percentile is a rank, so it is only as stable as `n`

A sample quantile is one particular order statistic. Which one is `n - i` counting from the worst,
where `i` is the index the implementation picks — and the two here differ:
`native_subtitle_integration_benchmark.py` uses `round((n-1)*q)` (the numpy *nearest* method),
`libass_prototype_benchmark.py` uses classical nearest-rank `ceil(n*q)`. They agree on every case
below, but they are not the same function; check the one you are reasoning about.

| quantile | n | which order statistic | usable as a gate? |
| --- | --- | --- | --- |
| p99 | 303 | 4th worst | **no** — an outlier picker; moves with one scheduler hiccup |
| p99 | 1000 | 11th worst | yes |
| p95 | 30 | 2nd worst | **no** |
| p95 | 200 | 11th worst | yes |

**Roughly the 10th-worst sample or deeper, or it is not a gate.** The estimate rests on the `k ≈
n(1-q)` samples above the quantile, so its relative standard error is about `1/√k`: k=10 gives ~32%,
k=3 gives ~58%. That is the whole reason a p99 over 303 samples cannot hold a bound to within a few
percent.

The step this came from failed 5 times in 40 CI runs — a different budget each time, with
`interaction_cpu_delta_p99_ms` implicated in most. Its threshold had already been doubled once, which
is what makes the estimator rather than the bound the suspect.

### The order to decide in

1. **Quantile from meaning.** p99 is the tail a viewer feels as a dropped frame. Do **not** drop to
   p95 to buy stability — that measures something easier, not something truer, and hides exactly the
   events the gate exists to catch.
2. **`n` from the quantile.** Enough samples that the chosen quantile has a stable rank.
3. **When `n` cannot reach it, add measurements — never lower the quantile.** Two devices, and they
   are *not* interchangeable:
   - **Pooling** repeats into one sample set (`perf_gate.py --loops`) raises `n`, so it raises the
     order statistic and fixes the rank.
   - **Median across trials** of each trial's own quantile (`native_subtitle_integration_benchmark.py`)
     leaves the rank exactly where it was and only cuts the dispersion of a same-rank estimate.

   Pooling is the stronger device, so prefer it — unless the trials are not exchangeable. They are
   not here: trial 1 carries the warm-up (+53 MiB RSS against +5-8 for trials 2-3), and pooling would
   blend its samples into the others. Replayed over 52 archived runs, the median rule also scored
   better (1 run over budget against 2 for pooling).
4. **Bound from an anchor or from measured noise.** An anchor is physical and does not move:
   `interaction_wall_p99_ms` is `1000/60`, a frame interval. Everything else comes from the observed
   CV — `perf_gate.py` carries `median CV ~1%`, `p99 CV ~25%`, and sets per-metric tolerances from
   exactly that.

Tightening a bound "to be safe" manufactures flakes. A bound moves when its anchor or its measured
noise moves.

### Where each gate stands

| gate | estimator | order statistic | verdict |
| --- | --- | --- | --- |
| `tools/perf_gate.py` | p99 pooled across `--loops` | raised by pooling | meets rule 4, not rule 2 — its own docstring records that p99 "resamples 50-75% between back-to-back runs **even pooled**", so what holds it is the +100% tolerance derived from that CV, not the rank |
| `tools/libass_prototype_benchmark.py` | p99 over 1000 warm samples | 11th worst | conforms |
| `tools/libass_prototype_benchmark.py` (cold) | p95 over 30 process starts | 2nd worst | below the floor and left there — reaching it would cost ~200 real process starts per CI run — so the bound is set from measured noise instead (300 ms; see below) |

### The cold bound was inside its own noise (2026-09-01)

The row above used to justify the below-floor estimator by calling 100 ms "a smoke bound an order of
magnitude above observed cold latency". That was wrong, and it is the reason the gate fired.

Measured across 13 archived macOS runs, this estimator spans **31.5-100.6 ms, median 49.7** — so the
bound sat at 2x the median and *inside the spread of its own estimator*, not an order of magnitude
above it. It first failed at 100.6 ms, with the same run's warm p99 at 603 us against a 90-385 us
range: every independent metric inflated together, which is a runner running ~2x slow, not a render
that regressed.

Rule 4 says a bound comes from an anchor or from measured noise. There is no physical anchor for a
cold process start, so it comes from the noise: 300 ms is 6x the median and 3x the worst observed,
which still catches gross breakage while surviving a degraded runner. Re-derive it from the archive
if that spread moves — do not tighten it toward the typical value, which is what produced this flake.

A below-floor estimator is only as good as the claim excusing it. If a rank is not defensible, the
number excusing it has to be re-measured, not asserted once and inherited.
| `tools/native_subtitle_integration_benchmark.py` | median across trials of each per-trial p99, single-trial breach capped at 2x | **still 4th worst per trial** | the rank was never fixed — trials are not exchangeable, so pooling was rejected. What holds this gate is the trial median plus the 2x cap, not the rank |

Two of the four do not meet the rank floor, and say so rather than claiming it. The floor is the
first thing to reach for; when a gate cannot, it owes the reader the device that replaces it.

A trial that reports a *validity* failure (a missed presentation cadence) is discarded, not counted
as a performance failure — otherwise the noisiest thing the harness can observe becomes the clause
most likely to fail. Leak meters (`retained_rss_growth_mib`) stay conjunctive across trials: a median
lets the other trials vote a leak away.
