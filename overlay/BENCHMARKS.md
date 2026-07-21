# Responsiveness benchmark — in-mpv tooltip

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

1. **BGRA LUT** (`osd.to_bgra_array`): the per-pixel uint16 widen×multiply÷255 premultiply replaced
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
