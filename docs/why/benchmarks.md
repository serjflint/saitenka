# Performance & benchmarks

Saitenka is built to be fast enough to sit *inside* your player without disturbing playback — no
dropped frames while the video runs, no stall when you hover a word.

!!! note "All numbers below were measured on one machine: an Apple M3 Pro."

    Latencies are hardware-dependent. Every figure here is "on an M3 Pro" (macOS 25.5.0, arm64) —
    treat them as a reference point, not an absolute guarantee. Run the harness on your own hardware
    (see [Run it yourself](#run-it-yourself)) to get numbers that mean something for you.

## Headline numbers

Measured on an Apple M3 Pro, post-optimization (after the Stage 6/7 render levers). The full
engineering log — every baseline, every lever, the raw percentile tables — lives in
[`overlay/BENCHMARKS.md`](https://github.com/serjflint/saitenka/blob/main/overlay/BENCHMARKS.md).

| Metric | Measured (M3 Pro) | Target |
|---|---|---|
| Warm hover (prefetched → shown) | p50 ~0.5 ms | < 16 ms |
| Cold first paint (hover → first pixels) | p50 ~21.8 ms · p95 ~46.1 ms | p50 < 100 ms · p95 < 250 ms |
| Scroll frame (one wheel step) | p50 ~0.6 ms | < 16 ms |
| Poll-tick hover hit-test | ~0.4–0.5 ms | < 5 ms |
| Pathological corpus (20 worst words) | worst p95 ~132 ms | < 150 ms (all 20) |

The common case — a word you hover after the background prefetch has already warmed the line —
paints in about **half a millisecond**. A genuinely cold word (fresh mid-session) shows its first
pixels in about **22 ms**, well under one display frame's worth of the budget.

## What the targets mean

The targets aren't arbitrary — they're a KPI budget ranked by what the eye actually notices, each
tied to a perceptible failure if it's missed:

| KPI | Budget | Why the budget |
|---|---|---|
| Warm hover | < 16 ms | The common case: prefetch warms the line while you read, so the shown panel must feel instant. |
| Cold first paint | p50 < 100 ms · p95 < 250 ms | The headline latency — a fresh word's first pixels. p95 guards the tail, not just the median. |
| Scroll frame | < 16 ms | One wheel step must fit inside a single 60 fps frame or scrolling visibly stutters. |
| Hit-test | < 5 ms | Runs every poll tick (25 ms interval); the per-tick cost must be a small fraction of it. |
| Nested popup | < 150 ms | First paint for an inner (scanned) word, the heaviest interactive path. |

Anything crossing the 16.7 ms frame budget risks dropping a frame; anything crossing the poll
interval risks stalling the loop that watches the subtitle and mouse. The measured numbers sit inside
every budget with margin.

## Honest caveats

The numbers are good, but they are not the whole story — and hiding the rough edges would make this
page useless.

- **Cold p95 tail on pathological entries.** A few monolingual-dictionary words have a *first*
  definition body that is one enormous block (the 取る family, 気, かける). Under `--stress` — a
  sustained, deliberately worst-case run with zero idle time and a shrunk cache — those cold entries
  have been observed blowing the frame budget (**MAX ~950 ms** per op on the full 19-dict rig). This
  is a known weakness: the current render defers *later* definition bodies but still renders the first
  one whole. The open lever is to clip/stream that first body too. Normal idle-paced usage doesn't hit
  this, but sustained heavy scrolling on the worst words can.
- **Single-machine.** Everything here is one Apple M3 Pro. A slower CPU, a spinning disk, or a
  cold OS file cache will shift these numbers — especially the disk-cold *first* hover after launch
  (the 1.3 GB monolingual index is read from disk once).
- **Free-threaded Python helps.** Saitenka runs on the free-threaded (no-GIL) Python 3.14t build,
  which lets the background prefetch and rendering work run in real parallel with the poll loop.
  On a GIL-enabled interpreter that parallelism collapses to time-slicing, so warming lags more.

## Run it yourself

The benchmarks are reproducible — the harness is committed and runs headless against your real
dictionary set via a fake mpv IPC:

```bash
uv run python examples/bench_responsiveness.py --reps 12
```

Add `--pathological` for the 20-worst-words corpus, `--stress` for the sustained chained session, or
`--timeline` for an idle-paced run that models real passive watching. The full walkthrough of what
each mode measures, and every historical baseline, is in the engineering log:

- **Harness:** [`examples/bench_responsiveness.py`](https://github.com/serjflint/saitenka/blob/main/examples/bench_responsiveness.py)
- **Full log:** [`overlay/BENCHMARKS.md`](https://github.com/serjflint/saitenka/blob/main/overlay/BENCHMARKS.md)

## Guarding against regressions

Three layers stop a perf regression from rotting in unnoticed, each tuned to a different noise profile.
The exact commands live in `pyproject.toml`'s `[tool.poe.tasks]` and the workflows — this is the map.

| Layer | What it is | When it runs | Signal |
|---|---|---|---|
| **Local rot-guard** | `poe perf-check` ratchets the dict-free `--synth` render bench against `perf-baseline.json` (per-metric tolerance: median +50%, tail-noisy p99 +100%). `poe perf-bless` after a deliberate change. | Locally, on demand (hard fail). | Catches a 2-4× rot before you push. |
| **Continuous history** | [`github-action-benchmark`](https://github.com/benchmark-action/github-action-benchmark) charts the same `--synth` numbers on a gh-pages dashboard; PRs get a comparison comment. | Every push to `main` (stores) + every PR (compares). | Trend over time — a chart, not a gate. |
| **Live-mpv jank harness** | `poe jank-live` drives the overlay against a real *playing* mpv and polls mpv's own `frame-drop-count` / `vo-delayed-frame-count` — the only signal that sees mpv's compositor. | The e2e GUI tier (Linux/Xvfb, weekly/tag). | Real-time dropped frames. |

The `--synth` corpus is dict-free and deterministic on purpose: it needs no `overlay.toml`, so the same
numbers come out on any machine and any commit, which is what makes CI history meaningful. Run
`poe synth-bench` (add `--loops N` to see run-to-run variance) or `poe jank-live` locally to reproduce
either. The gh-pages dashboard is at `https://serjflint.github.io/saitenka/dev/bench` once the history
branch is enabled.

See also: [how Saitenka compares to other tools](comparisons.md).
