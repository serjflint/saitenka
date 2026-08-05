window.BENCHMARK_DATA = {
  "lastUpdate": 1785916783096,
  "repoUrl": "https://github.com/serjflint/saitenka",
  "entries": {
    "Saitenka render (synth)": [
      {
        "commit": {
          "author": {
            "email": "serjflint@gmail.com",
            "name": "Sergei Iakhnitskii",
            "username": "serjflint"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "9607aa6a06f50f7930d80e986b263a9967682e90",
          "message": "feat(perf): dict-free synth gate + continuous history + live-mpv jank harness (#33, #32) (#180)\n\n* feat(bench): dict-free deterministic synth benchmark + noise-characterized perf gate (#33)\n\nThe perf gate shelled `--vocab`, which needs real dicts in overlay.toml — so it\ncould never run in CI. Add a dict-free `--synth` mode (constructed entries, no\nrandomness → identical numbers on any machine/commit) and point the gate at it,\nunblocking the continuous-history dashboard (#33/#32).\n\n- `--synth` renders the shipping windowed viewport over synth_corpus() spanning\n  short/medium/tall entries; a warmup pass keeps the first-render outlier out of p99.\n- `--loops` characterizes run-to-run variance (CV). Measured: median CV ~1%,\n  per-loop p99 CV ~25% (tail metric). The gated p99 is pooled across loops → steady.\n- Per-metric gate tolerance from that data: median +50%, p99 +100% (a flat +50%\n  flapped — p99 resamples ~50-75% back-to-back). Both still catch a 2-4x rot.\n- `to_bench_json` emits github-action-benchmark customSmallerIsBetter for gh-pages.\n\n* ci(perf): continuous perf history via github-action-benchmark on gh-pages (#33/#32)\n\nThe dict-free `--synth` bench now feeds github-action-benchmark — the FOSS-standard\ngh-pages history + charting pattern, no external service. Two jobs, per the action's\nPR-safety rule:\n- store (push to main): auto-push to dev/bench/data.js → time-series chart on Pages,\n  commit-comment alert past 2x.\n- compare (PR): compare vs stored history, comment on the PR, never push.\n\nTrend signal only (fail-on-alert: false) — GH runners are wall-clock-noisy; the hard\nguard stays local `poe perf-check`. Pinned to 3.13 (prebuilt fugashi, no MeCab build).\nOne-time gh-pages branch + Pages setup documented in the workflow header.\n\n* feat(jank): live-mpv frame-drop harness + shared live setup, on the perf dashboard (#32)\n\nThe headless benches can't see mpv's compositor, so real-time jank went unmeasured\n(#32.1). Add examples/jank_live.py: drive the overlay against a real PLAYING mpv and\npoll mpv's own frame-drop-count / vo-delayed-frame-count per interaction.\n\n- Extract the real-mpv setup from test_live_mpv.py into tests/live_harness.py so the\n  smoke tests and the jank harness share one boot (paused=False lets the VO advance).\n- Pure reduce_jank_samples seam (cumulative counters → per-step deltas, clamped ≥0),\n  unit-tested without mpv; to_bench_json feeds the same gh-pages dashboard.\n- poe jank-live (--max-drops catastrophic gate); wired into the e2e Linux/Xvfb tier,\n  storing a second series next to the render bench.\n\n* fix(jank): satisfy the render-cache signature in the live harness\n\nMiniDS lacked dicts/freqs/pitches, so dict_set_signature crashed on the cold-paint\npath when a render-cache file exists on disk (a dev machine; a fresh CI runner has\nnone, which is why the smoke tests passed). Add empty collections. Also document that\nthe pause-lease makes the interaction-phase drop counts ≈0 by design — the baseline\nplayback window is the real overlay-during-playback signal.\n\n* docs(benchmarks): document the three-layer perf regression guarding (#33/#32)\n\nThe canonical benchmarks page now maps the local rot-guard, the github-action-benchmark\ngh-pages history, and the live-mpv jank harness — pointing at the poe tasks/workflows as\nthe source of truth rather than restating commands.",
          "timestamp": "2026-08-05T10:58:55+03:00",
          "tree_id": "6bdc172edf35dfde4e9c94642c1ccd0fc043c29d",
          "url": "https://github.com/serjflint/saitenka/commit/9607aa6a06f50f7930d80e986b263a9967682e90"
        },
        "date": 1785916782715,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.103,
            "range": "±3.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.215,
            "range": "±8.8%",
            "unit": "ms"
          }
        ]
      }
    ]
  }
}