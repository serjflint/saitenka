window.BENCHMARK_DATA = {
  "lastUpdate": 1788645235614,
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
      },
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
          "id": "55cd10730c4859ccc83db4d2048c9abfb8093804",
          "message": "test(coverage): assert the benchmark scenario signals that had no conventional test (#181)\n\nA coverage audit of the benchmark scenarios against the suite found the render/cache/\ninteraction seams thoroughly covered (pixel-identity + picking-oracle + concurrency\ninvariants), with four genuine holes — signals a benchmark headlines but nothing asserted:\n\n- scroll_frame.jank counter (the --scroll-jank headline): bumps only past the threshold.\n- subtitle_render span: emitted (was produced, never asserted).\n- tip_compose `kind` attribute: base/nested/clicked classification pinned.\n- the --stress chain (show→scroll→nested→scroll→dismiss) as ONE accumulating session\n  through the REAL hit-test path (_hit/_scan_hit) — test_stress.py runs the same chain\n  but via direct entry points, bypassing hit-testing.",
          "timestamp": "2026-08-05T11:04:42+03:00",
          "tree_id": "8c60427561aee2b3497802d6df7850934391cbee",
          "url": "https://github.com/serjflint/saitenka/commit/55cd10730c4859ccc83db4d2048c9abfb8093804"
        },
        "date": 1785917122305,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.928,
            "range": "±3.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.013,
            "range": "±11.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f07b8b9a173c00af28507ed1746e332e9248e790",
          "message": "feat(prewarm): atlas heartbeat metrics + startup plan + --atlas-plateau stop (#182)\n\nA `--limit 0` atlas sweep is >1M words into an uncapped atlas, but the heartbeat\nshowed only a mask count (bytes hard-coded 0), so a saturated glyph population\nread as a hang and disk growth was invisible. Add a startup plan (total /\nalready-done / footprint), real per-checkpoint bytes + new-mask delta + skipped\n+ marginal-rate size projection, and an opt-in `--atlas-plateau` early-stop.",
          "timestamp": "2026-08-05T11:46:12+03:00",
          "tree_id": "016ebe7fbfd80dc646b42a657fa8bb4cc525cb3a",
          "url": "https://github.com/serjflint/saitenka/commit/f07b8b9a173c00af28507ed1746e332e9248e790"
        },
        "date": 1785919600427,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.731,
            "range": "±3.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.43,
            "range": "±8.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "bd600b741400241294caee1b76da02ec2252688a",
          "message": "fix(prewarm): project atlas size from the cumulative rate, not one checkpoint (#183)\n\nThe size projection extrapolated a single checkpoint's Δbytes over the ~1M-word\nhorizon, so SQLite's page-quantised (lumpy) growth swung it ±80 MB between\nheartbeats (927 → 934 → 1004 MB). Use the cumulative bytes/word since the run's\nfirst raster instead — it averages the page noise out and converges as the run\nprogresses.",
          "timestamp": "2026-08-05T12:09:54+03:00",
          "tree_id": "f27b4dd2f61a3c2a846c494cab72f7ce3abbcb0e",
          "url": "https://github.com/serjflint/saitenka/commit/bd600b741400241294caee1b76da02ec2252688a"
        },
        "date": 1785921028483,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.678,
            "range": "±2.6%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.34,
            "range": "±12.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "2bdcdeb5d092a5fcf324a9d2fc930cde72e15b21",
          "message": "feat(prewarm): surface already-cached masks + progress denominator in heartbeat (#184)\n\nThe heartbeat showed skipped (resume ledger) and +new masks, but not the OTHER\ndedup layer: masks re-rastered whose rows already existed (INSERT OR IGNORE). So\na re-scale run (--atlas-scale 1.0 after 1.5, whose 1× reference masks the 1.5\npass already built) read as \"+0 masks · 0 skipped\" — opaque. Count inserted vs\nignored in MaskAtlas.put and show \"+N new (M already cached)\", plus an\nm/to_raster progress denominator.",
          "timestamp": "2026-08-05T13:02:01+03:00",
          "tree_id": "aa722292d1a71a62a2605197e690b2ed5874dd72",
          "url": "https://github.com/serjflint/saitenka/commit/2bdcdeb5d092a5fcf324a9d2fc930cde72e15b21"
        },
        "date": 1785924145618,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 3.139,
            "range": "±4.8%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 5.017,
            "range": "±12.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "cf7f5b41aa7206d84620e543b58a8c1da17c4981",
          "message": "feat(prewarm): cross-scale read-check + explicit reference-build note (#185)\n\nThree fixes to the atlas-scale UX and correctness:\n\n1. Explicit: a run at scale N builds the 1× REFERENCE masks AND the N× native\n   masks — the help + startup line now say so (one 1.5 run covers 1.0 and 1.5;\n   no separate 1.0 run needed).\n2. Cheap read-check: split the resume ledger into reference (1×) and native\n   passes, so a run at any scale skips a reference another scale already built\n   (done(1.0)) BEFORE rastering — no getmask2, no wasted CPU. A one-time\n   backfill (native-done ⇒ reference-done) fixes existing ledgers.\n3. --atlas-scale 0 now reads tip_scale exactly as the runtime does (top-level,\n   not a nested [tooltip] key the runtime ignores), so they can't disagree.",
          "timestamp": "2026-08-05T15:15:56+03:00",
          "tree_id": "a272aa51817e28fe269a586f97f5dab1b77e4ff0",
          "url": "https://github.com/serjflint/saitenka/commit/cf7f5b41aa7206d84620e543b58a8c1da17c4981"
        },
        "date": 1785932185405,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.767,
            "range": "±2.9%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.669,
            "range": "±8.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "df0616b4cd0413896da3765791ccc9fba5532bd7",
          "message": "feat(grow): the Grow loop — an idle-time engine that writes the missing tests (#187)\n\n* test(dict): deterministic regression for the entry-cache eviction race\n\nThe _entry_cache TOCTOU (a cached lookup's get→move_to_end racing a concurrent popitem eviction,\n942ca3c) has a sub-microsecond window that stress-parallel testing hits ~0/200 — it reached\nproduction uncaught. Force the exact interleaving with blanket's bytecode injector (a pause at the\nhit-path move_to_end, no source edit); under _entry_lock the evictor blocks on the lock and the race\ncan't occur. A negative control (no-op lock ≈ pre-fix) reproduces the KeyError, so the regression\ncan't rot into a vacuous pass.\n\nblanket is an opt-in `grow` dependency group (MIT, pure-Python, runs under 3.14t); the module\nimportorskips it so the default suite skips it. First worked example of the Grow loop's concurrency\ngate arm (see vibe/grow-loop-plan.md).\n\n* feat(grow): grow_gate.py — the 4-arm deterministic teeth-gate\n\nMirror of sharpen_gate, reversed: guards an ADDITION (a new test must provably\nADD power) rather than an EDIT. Four pure, injectable arms + a CLI wiring the real\nsubprocess/coverage primitives:\n\n  1 property-mutant  survives-old + killed-new (reuses sharpen_gate replay)\n  2 oracle-liveness  negate each assert; >=1 must flip the test red; no trivial/dead\n  3 context-delta    grown suite lights a line the existing suite never ran\n  4 concurrency      paired regression(passes-guarded) + control(fails-unguarded)\n\n15 unit tests drive every arm through its injected primitive (no real cosmic-ray/\npytest/coverage), mirroring test_sharpen_gate.py.\n\n* feat(grow): grow_ledger.py + grow_triage.py — semantic gap memory + product triage\n\ngrow_ledger: the durable memory keyed SEMANTICALLY (gap_id = hash(source,\ntarget_symbol, dimension) + per-symbol target_sha over the AST source), so a closed\ngap survives unrelated line-drift and reopens only when its own symbol changes\n(the Q3 termination result from proto_grow_ledger, made permanent by 16 rot-guard\ntests). Handles dotted Class.method targets; never crashes on a moved symbol.\n\ngrow_triage: ranks the PRODUCT of value (ruff-analyze fan-in + churn) and\nunder-specification (private-attr seam proxy + opt-in survivors/dead-contexts) —\na target is grown only if BOTH are high (Sharpen sums; Grow multiplies). Pure\nscore_candidates is unit-tested (5 tests lock product-not-sum); real gatherers are\nsubprocess glue like sharpen_triage. Smoke against the repo picks controller.py.\n\n* test(tooltip): stateful render<->hit-test agreement across a session (Grow G4)\n\nThe canonical Grow worked example (test-coverage-plan Phase 3). test_scale_boundary\nproves the agreement oracle for a SINGLE action; both motivating regressions emerged\nfrom INTERACTION across a session (scroll-time panel drift, navigate x key-gated\nfeature). A RuleBasedStateMachine drives the REAL controller through arbitrary\nhover/scroll/navigate/back/open_nested/resize sequences and after every step asserts\n(a) model==impl (shown / nav-depth / nested) and (b) every visible drawn scan cell +\nlink box round-trips through the real _scan_hit / _tip_link_hit / _nest_link_hit.\n\nIncludes a permanent negative control (test_the_agreement_oracle_has_teeth): a 40px\ntransform drift mis-hits, proving the invariant is falsifiable, not vacuous (arm-2\noracle-liveness made permanent). integration-marked; ~5s; hypothesis.event drift\nsignal for the coverage-context gate.\n\n* docs(grow): .agents/grow loop artifacts — SPEC/GUIDE/ADAPTERS/contracts/PROMPTS/harness\n\nDistills vibe/grow-loop-plan.md into the agent-facing loop, mirroring .agents/sharpen\n(reversed direction — writes missing tests vs fixes existing):\n\n- SPEC.md   terse process spec: 4 outcome classes, the 4-arm gate, product triage,\n            semantic ledger key, extend-vs-add boundary, anti-bloat, Sharpen handshake\n- GUIDE.md  reader's guide (coverage-is-a-lower-bound, the worked state-machine + race)\n- ADAPTERS.md provider-neutral host contract; the additive constraint; applicable-arm gate\n- contracts.json + PROMPTS.md  schemas + role prompts (isolated author/skeptic/judge)\n- harness.js  Claude Workflow adapter: triage+scenario-map -> author(additive) ->\n            objective 4-arm gate -> isolated skeptic -> judge -> ledger/PR; fail-closed\n            PR, red-on-pristine routes to a filed bug, dry-run default\n- scripts/smoke.sh  grep-free rot-guard (node --check, contract-version match, tool presence)\n\n* docs(grow): Stage 6 backtest against the held-out gap corpus\n\nDry-run of the loop against its motivating regressions (recorded, not tuned-to-pass):\n3/5 surfaced-and-closed with demonstrated teeth, one per gate arm the corpus exercises\n(arm-4 race, arm-3 dead-config scale, arms-1/2/3 hit-test agreement). The two open\nitems validate the non-trivial paths honestly: the nested scroll-to-true-bottom gap\n(dabfdd9, off-branch) is a covered-but-under-specified dimension left OPEN on purpose;\nthe honorific bug is red-on-pristine → routes to a filed product issue (outcome class 2).\nNo corpus gap falls outside the four arms. smoke.sh guards BACKTEST.md too.\n\n* fix(grow): close adversarial-review teeth gaps C1-C8 in the gate/ledger/triage\n\nWave B — code fixes from the independent review (each with a regression test):\n  C1 state machine now asserts the one-panel invariant (hit_target panel IS the drawn\n     panel) — the deterministic guard against a reintroduced two-panel split; the\n     round-trip alone was self-consistent by construction.\n  C2 growth_adhoc_gate: arm-1 (survives-old + killed-new) via an author text mutant,\n     off the cosmic-ray allowlist → available for any module, non-optional for scenarios.\n  C3 context arm --deselect: keep the grown test out of the OLD baseline so\n     extend-before-add no longer collapses the delta to a false bounce.\n  C4 additive_gate: a real adds-only assert-node diff (anticheat_diff missed same-tier\n     value changes, waving a mutative edit through as 'additive').\n  C5 triage universe now includes untested modules (under-spec 1.0) + low-confidence\n     warning when no survivors/contexts signal — untested valuable code was invisible.\n  C6 concurrency_gate reconciled with the shipped passing-control: both PASS + the\n     control's oracle is arm-2-live (teeth), matching test_cache_race.py.\n  C7 ledger target_sha via ast.unparse over ALL same-name nodes (reopens on a decorator\n     swap / overload change; comment-only edits don't).\n  C8 liveness counts pytest.raises/warns as live oracles (was bouncing valid exception\n     tests as no_asserts).\n\npoe all green; 51 tools tests + 16 grow suite tests pass.\n\n* docs(grow): correct overclaims + sync .agents/grow to the fixed gate (contract v2)\n\nWave A — the record, honest, matching the C1-C8 fixes:\n  SPEC     arm-1 off-allowlist + non-optional for scenarios; arm-4 passing-control;\n           arm-3 --deselect; additive_gate (not anticheat_diff) as the Grow<->Sharpen\n           boundary; an explicit 'honest scope' note (arm-3 alone = dead-config only).\n  GUIDE    arm-4 passing-control; state-machine framing corrected (one-panel invariant\n           vs self-consistent round-trip).\n  ADAPTERS additive via grow_gate.additive; applicable-arm gate (scenario arm-1 required,\n           concurrency both-pass + control liveness).\n  PROMPTS  author supplies the scenario mutant / concurrency nodes; gate steps updated.\n  harness  contract v2: additive subcommand, growth-adhoc (arm-1 required), context\n           --deselect, concurrency control-liveness; proposal schema carries the mutant.\n  contracts v2 + the new proposal fields.\n  BACKTEST honest tally — ~1/5 decisive deterministic teeth (not 3/5); the agreement\n           oracle is a coverage + inverse-transform + one-panel guard, not two-panel teeth.\n\nsmoke green (v2 in sync, node --check passes); poe all green.\n\n* feat(grow): real coverage-context signal + arm-1/arm-3 alternative-proof gate\n\nKills the two residual gaps, both surfaced by a live loop run:\n\nGap 2 (C5-deep) — a REAL under-spec signal for triage. New grow_contexts.py runs the\nsuite under pytest-cov + xdist (the poe cov fast path — a serial coverage run is\nglacial under 3.14t) with --cov-context=test, and per module counts uncovered +\nweakly-covered (≤1 test) lines. Emits {module: n} for grow_triage --contexts-json.\nTriage reweighted: when a real signal is present it DOMINATES under-spec and the\nprivate-attr seam proxy drops to a tiebreak (seam scales with test volume, not\nadequacy). Live: the signal re-ranks to genuinely under-specified modules and drops\nthe low-confidence warning. Injectable aggregate() unit-tested (3 tests).\n\nGap 1 (arm-1 end-to-end) — the live loop drove growth-adhoc for real on\npanel.py::header_add_rect(speak_button=False): author supplied a 1-line mutant, old\nsuite SURVIVED it, grown test KILLED it, CUT restored clean → PASS. Added --deselect\nto growth-adhoc (mirror the context arm) so an extend-before-add mutant's old baseline\nexcludes the grown test.\n\nGate-composition fix (found live): arm-1 and arm-3 are ALTERNATIVE growth proofs, not\nboth-required. arm-3 is LINE-level, so a covered-but-under-specified BRANCH of an\nalready-covered line (the flagship class) lights no new line and bounces arm-3 though\narm-1 proves it (panel.py ternary: arm-1 PASS, arm-3 BOUNCE). Gate is now additive AND\nliveness AND (arm-1 OR arm-3). Updated SPEC/harness/smoke.\n\nBoth reviewers UPHELD the panel.py grow (dry-run, reverted). poe all + 54 tools tests\n+ smoke green.\n\n* feat(grow): self-reflection phase — the loop introspects itself every run\n\nEvery run now ends with a Reflect phase (harness contract v3): an ISOLATED agent\nreads the factual run trace (arms run/bounced/n-a, retries, review verdicts, outcome,\nnotes) and does introspect → reflect → improve, filing loop-improvement proposals to\n.reflection.grow.jsonl. Institutionalises the dogfooding that found the 8 review flaws\n+ the arm-1/arm-3 composition bug, so it's a standing mechanism not a lucky accident.\n\n- grow_reflect.py: reflection ledger keyed hash(category,subject); recurrence counted\n  at the manifest loop_version (mirrors Sharpen toolset_version — a landed loop-fix\n  bumps it and resets); escalated() surfaces findings seen >=2x. 6 unit tests.\n- harness: Reflect runs at EVERY terminal exit (bounced/dropped/no-candidate are the\n  richest lessons) via finish(); accumulates the trace through the run; REFLECTION\n  schema. ADVISORY ONLY — writes just the ledger, never edits a tool/spec/harness/\n  product file (self-modification guard); self_referential proposals flagged.\n- SPEC/GUIDE/ADAPTERS/PROMPTS + contracts v3 + smoke (Reflect phase + grow_reflect).\n\npoe all + 60 tools tests + smoke green.\n\n* fix(grow): reflector reuses an existing finding's subject so recurrence accumulates\n\nTesting the self-reflection phase by running it surfaced a real flaw: recurrence keys\non finding_id=hash(category,subject), but an LLM won't reproduce byte-identical subject\nstrings across runs, so the same weakness would mint a fresh id every run and never\nescalate. Fix: the reflector now reads the existing ledger FIRST and reuses a matching\nfinding's exact category+subject verbatim (mint new only for a genuinely new weakness).\nVerified live: two runs of the same failure class on different modules → subjects\nreused → recurrence 1→2 → all three findings escalated. harness/PROMPTS/SPEC.\n\nTest also confirmed the advisory guardrail (reflector touched ONLY the ledger) and\nanti-Goodhart restraint (filed nothing false, with reasoning).",
          "timestamp": "2026-08-05T19:55:44+03:00",
          "tree_id": "3fc6f5f4e2fcc2cf237856d120a3394b6d286257",
          "url": "https://github.com/serjflint/saitenka/commit/df0616b4cd0413896da3765791ccc9fba5532bd7"
        },
        "date": 1785948996707,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.549,
            "range": "±2.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.489,
            "range": "±5.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8784f2c57142bd24ea6a7994a76924bb2b06420c",
          "message": "feat(tokenize): dictionary-attested compound merging (Yomitan longest-match) (#186)\n\nAdjacent content tokens merge into one token when their joined span — the tail\ndeinflected to its dict form — is an exact dictionary headword (応急+処置 →\n応急処置, 走り+出した → 走り出す), so a compound UniDic over-splits is one\nhover/hit-test/colour/mine unit instead of fragments. Backed by a batch\nexistence probe (DictionarySet.terms_exist); applied at the controller's\nper-line token build after merge_inflected, and a no-op until dicts load.",
          "timestamp": "2026-08-05T19:56:41+03:00",
          "tree_id": "0e399e8d4578bf2ff0c219117bdfbc1fe7673689",
          "url": "https://github.com/serjflint/saitenka/commit/8784f2c57142bd24ea6a7994a76924bb2b06420c"
        },
        "date": 1785949025652,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.749,
            "range": "±3.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.499,
            "range": "±7.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4c754052c93bbe8dc05ddad91bada9a362d346bc",
          "message": "fix(deinflect): keep every distinct trace like Yomitan (kuru potential-or-passive) (#188)\n\nRework engine.deinflect()'s BFS to Yomitan's per-chain cycle guard (a rule can't\nre-apply to a text already in its own ancestry) as the termination bound, plus a\n(text, cond_out, chain) dedup that drops only genuine duplicate traces. A state\nreachable two ways now surfaces both — 来られる is recognised as potential-or-passive,\nnot only passive — clearing the last 12 Yomitan conformance-corpus vectors. The\nsingle inflection_chain label is unchanged. Bumps saitenka-deinflect 0.1.0 → 0.1.1.",
          "timestamp": "2026-08-05T20:54:34+03:00",
          "tree_id": "1b0517f48dd99b5091cf14e1ec5d509d2b8dcde8",
          "url": "https://github.com/serjflint/saitenka/commit/4c754052c93bbe8dc05ddad91bada9a362d346bc"
        },
        "date": 1785952506800,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.703,
            "range": "±3.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.577,
            "range": "±10.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b0ac9940d13dbfab739f2223327b4069c18ab698",
          "message": "docs(agents): distil loop research into skills; curate + adversarially review AGENTS.md (#190)\n\n* docs(testing): surface loop test-adequacy knowledge for the coding loop\n\nThe Sharpen and Grow loops apply a body of test-adequacy knowledge reactively, at\nidle time. Applied proactively while building, the same knowledge prevents the\ndefects the loops catch later — but it was trapped in the idle-loop docs and the\ngit-ignored research. Distil the feature-time half into the always-on / on-task\nsurfaces:\n\n- new .agents/skills/write-test/references/oracle-catalog.md — the invariant /\n  metamorphic-oracle families (agreement round-trip, warm==cold cache-equivalence,\n  scale-invariance, feature-toggle consistency, the concurrency oracles),\n  assert-oracles-not-pixels (platform-independence), the negative-control\n  discipline, and the extend-before-add config matrix (tests/util.py), each pointing\n  at its canonical in-tree example.\n- AGENTS.md §Testing: one reframe bullet (adequacy != coverage; covered-but-under-\n  specified is the target) → the catalog.\n- write-test skill: an oracle/property assertion step, an extend-before-add step, and\n  a load-bearing check in Verify.\n- advisory `poe test-live` — arm-2 oracle-liveness (tools/grow_gate.py) exposed for\n  the coding loop to prove one new test isn't vacuous, without the whole idle loop.\n  Opt-in, NOT in `poe all`.\n- write-test smoke rot-guards the catalog + the example tests it cites (grep-free).\n\nNo product/behaviour change; advisory-only.\n\n* docs(skills): add self-contained agents-md skill for config-surface curation\n\nA recurring decision — where does a new agent-facing convention belong, and is\nAGENTS.md still minimal? — had no consultable home. Add a thin, on-demand skill that\nencodes the routing across saitenka's surfaces (AGENTS.md / .agents/rules /\n.agents/skills / .agents/hooks / poe required-vs-advisory gates / .agents/mcp), the\ninclusion test, the persist-as-what call, and keep-it-fresh review.\n\nSelf-contained per the skill-authoring guidance: it packages its own vendored,\nevidence-based guide (references/writing-agents-md.md, reframed for this repo's\nopen-source/git reality from an internal-frameworks original) rather than depending on\nan external file or another skill. Ships a grep-free scripts/smoke.sh that rot-guards\nthe vendored guide + every surface it routes to. Description within the store limits\n(892 chars, no angle brackets, kebab name).\n\nPer the guide's own minimality thesis AGENTS.md is left unchanged (no skills catalog):\nskills auto-surface via their description, and dev-gate/agent-tooling are already\npointed at contextually.\n\n* docs: apply adversarial AGENTS.md review — cut drift, fix stale skill claims\n\nAn isolated adversarial review (contribute skill, two fresh grounded reviewers) found\nno P0/P1 — the file is accurate — but real drift/staleness. A reachability check first\nestablished that for Claude Code only AGENTS.md is auto-loaded (CLAUDE.md -> @AGENTS.md);\nskills load on-demand but .agents/rules/ is NOT injected — so content that must be seen\ncan't simply move to a rule.\n\nAGENTS.md:\n- Testing \"adequacy\" bullet restated the oracle-catalog it points to, and asserted a\n  false \"every oracle ships *_has_teeth\" universal (only one such test exists) ->\n  compressed to the reframe + pointer; the catalog is sole owner.\n- Dangling `uv-python` skill pointer (no such repo skill) -> cut.\n- Fork-bomb + PreToolUse-hook fact stated twice -> the rules bullet now points to the\n  Tooling section that explains it.\n- Escape-recovery block tightened but KEPT in AGENTS.md (moving it to searching.md would\n  hide it from Claude, which never loads that rule).\n- Deduped \"No process scars\" (Documentation restated Comments).\n\ndev-gate skill:\n- \"The repo has no CI\" was false — CI (.github/workflows/ci.yml) mirrors poe all; fixed.\n- Stale \"3.13 tasks set UV_PROJECT_ENVIRONMENT=.venv-{fuzz,cx}\" — they use uvx /\n  uv run --no-project ephemeral envs; corrected + pointed at the canonical AGENTS.md\n  \"Fuzzing & symbolic checks\".\n\nKept (critical judgment): the Comments/Documentation guardrails the review called\n\"inferable virtue\" stay — this session proved them load-bearing (they caught the Testing\nover-write). The adequacy sections stay canonical in AGENTS.md (no skill owns them).\n\n* feat(agents): make .agents/rules/ actually always-loaded for Claude\n\nAGENTS.md called .agents/rules/ \"always-on\", but for Claude Code that was\naspirational — only the PreToolUse hook enforced the shell-search ban; the rule\nTEXT was never in context (CLAUDE.md -> @AGENTS.md is the sole auto-load, and\nnothing injects .agents/rules/). Claude Code 2.0.64 ships a native .claude/rules/\ndirectory (a no-`paths:` rule loads globally); materialize it from the canonical\nsource with a git-ignored `.claude/rules -> ../.agents/rules` symlink, mirroring the\nskills symlink. Documented in the .agents/rules/ bullet + the agent-setup activation\ntable; verify with /memory.\n\nWith searching.md now genuinely always-loaded, the fork-bomb escape-recovery drill\nmoves there (its proper home) — AGENTS.md keeps a one-line breadcrumb (safe even\nbefore /memory confirms the load).\n\n* docs(skills): add test-adequacy skill; compress AGENTS.md adequacy sections\n\nMutation auditing + Fuzzing & symbolic checks were ~42 always-loaded lines of deep,\nopt-in-tool reference. Route the depth to a new on-demand `test-adequacy` skill\n(mutation/fuzz/crosshair — allowlist, survivors->property+@example, crash-repro\nworkflow, 3.13-env pinning, HypoFuzz-licence rationale). AGENTS.md keeps both headings\n(anchors in sharpen docs / conftest / test_mutate_targets stay valid) compressed to the\nalways-relevant principle + poe task names + a pointer; names poe tasks, never\ninvocations (poe is SSOT for how). dev-gate + write-test descriptions repointed to the\nskill as the adequacy owner. Self-contained, grep-free smoke.\n\n* docs: add \"Quality gates — when to run what\" decision tree\n\nUnifies the quality stack into one tight-loop routing map: cheapest sufficient check\nfirst (poe affected -> poe all), escalate by change-risk to write-test / test-adequacy\n/ the contribute adversarial review / the Sharpen+Grow loops — poe all as the floor,\nthe rest as escalations. High-value always-on routing (names saitenka's own stack), so\nit earns always-loaded placement while the detail stays in each skill/loop.\n\n* fix(agents): correct the rules-load verification pointer\n\n/memory is Claude Code's memory-file editor, not a loaded-instructions viewer, so it\ncan't confirm .claude/rules/searching.md is loaded. Point at the InstructionsLoaded\nhook (logs which instruction files load, when, and why) instead — in the AGENTS.md\nrules bullet and the agent-setup table.",
          "timestamp": "2026-08-05T22:12:30+03:00",
          "tree_id": "4b8e957b47235aa03ca6fbb7e2992cb1e346483b",
          "url": "https://github.com/serjflint/saitenka/commit/b0ac9940d13dbfab739f2223327b4069c18ab698"
        },
        "date": 1785957200980,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.826,
            "range": "±3.6%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.072,
            "range": "±13.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "448c5e1d5f226dc97484a67ebb5f0cf5cdfedbbc",
          "message": "feat(mining): animated (motion) screenshots on mined cards (#92) (#189)\n\nOpt-in [mine].animated_screenshot captures a short animated clip of the scene\nas the card image instead of a still — better sense of the scene, pairs with\nthe cue audio. Prefers WebP (small, sharp) and falls back to an animated GIF\nwhere ffmpeg lacks libwebp (Homebrew ffmpeg 8, Windows \"essentials\" builds), so\na clip is produced out of the box on every platform; only a build with no\nwebp/gif encoder at all keeps the still. The still JPG is always captured\nlocally as the preview + fallback.\n\nanimated_height/fps/quality/max_secs are the quality↔storage knobs; a\nCtrl+Shift+m shortcut ([mine].video_key) mines the hovered word with a clip for\none card without turning it on globally. Config threads through both the run and\nattach seams; doctor reports the animated capability.",
          "timestamp": "2026-08-05T22:24:27+03:00",
          "tree_id": "2b17c805b6e4e984d53fab5b7c8233ed65ffb5ed",
          "url": "https://github.com/serjflint/saitenka/commit/448c5e1d5f226dc97484a67ebb5f0cf5cdfedbbc"
        },
        "date": 1785957892248,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.68,
            "range": "±3.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.483,
            "range": "±20.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "7978b531fdf4a7338a87565ee80ea0dc098663cd",
          "message": "docs(agents): add the dream + skill-creator skills (#195)\n\n* docs(agents): add the dream skill — local-memory hygiene + Basic Memory uplift\n\n* docs(agents): add the skill-creator skill; route AGENTS.md skill-authoring to it",
          "timestamp": "2026-08-05T23:46:32+03:00",
          "tree_id": "165ca2b3daa7cff54376bfca36ecd0afdddcd494",
          "url": "https://github.com/serjflint/saitenka/commit/7978b531fdf4a7338a87565ee80ea0dc098663cd"
        },
        "date": 1785962825298,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.949,
            "range": "±3.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.914,
            "range": "±20.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "606bfeb016454fdfca1c63b766f860af4ded9471",
          "message": "feat(mining): configurable note type — field map, card kind, presets (#101) (#191)\n\nMining is no longer Lapis-only under a configurable-looking model name. `[mine]`\nnow takes a `preset` (Lapis/Kiku), a `card_kind` marker, and a `[mine.fields]`\nlogical→real map, threaded through both the attach and run seams. `doctor`\nvalidates the effective field map against the note type; unknown fields are\ndropped at build time so the note still adds.\n\nDefault card kind switches from IsSentenceCard to IsWordAndSentenceCard (the\nLapis/Kiku default); `card_kind = \"sentence\"` restores the old marker.",
          "timestamp": "2026-08-05T23:55:35+03:00",
          "tree_id": "3c49f1602df3c9342c44e63d4f1c8617a7049cb0",
          "url": "https://github.com/serjflint/saitenka/commit/606bfeb016454fdfca1c63b766f860af4ded9471"
        },
        "date": 1785963366019,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.939,
            "range": "±3.6%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.178,
            "range": "±22.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f9a138d931541aec6e908f264999558b8caa8057",
          "message": "feat(mining): Yomitan-style per-field marker templates ([mine.card_format]) (#192) (#197)\n\nEach note field becomes a template of {marker} tokens (Reading = \"{furigana}\",\nSentence = \"{cloze-prefix}<b>{cloze-body}</b>{cloze-suffix}\"), so one field can\ncombine markers and one marker can fill several fields. Opt-in; wins wholesale\nover [mine.fields]. Twenty markers, each filled from real data (readings/pitch\nfrom dictionaries); doctor warns on an unfillable marker or a missing field.\n\nNew card_markers.py (build_markers/render_card_format/anki_furigana/cloze);\nDictionarySet.pitch_field; dedup keys off the field that actually holds\n{expression} under card_format.",
          "timestamp": "2026-08-05T23:57:26+03:00",
          "tree_id": "272ff0461fca1026ba816d1d239d89c7772545e6",
          "url": "https://github.com/serjflint/saitenka/commit/f9a138d931541aec6e908f264999558b8caa8057"
        },
        "date": 1785963470466,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.713,
            "range": "±2.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.376,
            "range": "±9.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1fc4ec3b627a702c386016bdaa5d5a1ff7c4acfc",
          "message": "refactor(mining): single source of truth for the card-format marker catalog (#193) (#198)\n\n* docs(dream): backtick the MEMORY.md pointer example so lychee ignores it\n\nThe illustrative `- [Title](file.md) — hook` was written as a bare Markdown\nlink, so `poe links` (lychee) resolved it as a real relative link to a missing\nfile.md and failed the gate. Backticking it keeps the example readable and out\nof the link checker.\n\n* refactor(mining): single source of truth for the card-format marker catalog (#193)\n\nThe [mine.card_format] marker vocabulary was duplicated across three\nhand-maintained places — MARKERS (doctor's validator), the build_markers dict\nliteral (the producer), and the docs table — with nothing keeping them in sync.\nDrift had a user-visible cost: a producer-only marker made doctor false-warn a\nvalid marker; a listed-but-unproduced marker shipped an empty field.\n\nOne CATALOG of Marker entries (name, ship/deferred status, source, producer) is\nnow the sole source: MARKERS and build_markers both derive from it, and the docs\ntable is generated from it (poe docs-markers → a committed fragment\ninclude-markdown'd into the Configuration page, guarded by a golden test). Adding\nor deferring a marker is a one-line edit that can't desync the three.\n\nFolds the not-yet-groundable markers (word audio #93, pitch-accent-graphs,\nsentence-furigana, furigana-plain) into the catalog as deferred: out of MARKERS\n(so doctor still flags them) and named in the docs as unsupported. card_format\nrender output is unchanged.",
          "timestamp": "2026-08-06T00:31:00+03:00",
          "tree_id": "587d39781e36a758cc3557b755ae32953f32d599",
          "url": "https://github.com/serjflint/saitenka/commit/1fc4ec3b627a702c386016bdaa5d5a1ff7c4acfc"
        },
        "date": 1785965510029,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.712,
            "range": "±3.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.446,
            "range": "±9.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e0db7b2184040d36ce9252fe137783699867c02a",
          "message": "feat(setup): arrow-key + type-to-filter wizard prompts behind one shared seam (#194) (#199)\n\n`saitenka setup` drove every choice through hand-rolled `input()`: the deck picker\nprinted the top-12 by size and made you type the rest from memory; note-type and\ncard-kind were type-a-name-or-number lists. On a 50-deck collection that is the\nfriction the installer exists to remove.\n\nA new `prompt` module is the single seam for asking: `confirm` / `select` /\n`autocomplete` / `text` (+ a `spinner` for the silent AnkiConnect round-trips).\nOn a real terminal they use questionary — arrow-key selection and type-to-filter,\nwith the deck pickers accepting a brand-new name (the mining deck is created on\nfirst mine). It degrades cleanly, gated on `sys.stdin.isatty()`: a non-tty (the\nmpv plugin spawns the wizard console-less), `--yes`, a legacy Windows console (any\nTUI failure is caught), or `SAITENKA_NO_TUI=1` fall back to today's numbered-list /\n`[y/N]` prompts unchanged. questionary is lazy-imported inside the interactive\nbranch only, so it never touches a console-less run or the render pipeline.\n\nThe ~4 duplicate `_ask` / `_prompt` / raw-`input()` definitions across\nsetup_wizard, init_wizard, and cli.py collapse onto this one seam. The pure,\nseparately-tested core (`rank_decks`, `default_known_deck`, `intersect_default`,\n`anki_config_fragment`) is untouched — only the interactive glue moved. Adds a\n`questionary` dependency (MIT → prompt_toolkit BSD → wcwidth MIT).",
          "timestamp": "2026-08-06T01:19:35+03:00",
          "tree_id": "564dce5bc2df53c178dd226fa1799f5fe766349b",
          "url": "https://github.com/serjflint/saitenka/commit/e0db7b2184040d36ce9252fe137783699867c02a"
        },
        "date": 1785968426743,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.654,
            "range": "±1.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.92,
            "range": "±3.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "467b3cfb0c21bb49c0536df6a14c7f46208a9563",
          "message": "Merge pull request #200 from serjflint/refactor/30-lifetime-composition\n\nrefactor(#30): decompose the Reader god-object by lifetime (session⊃episode⊃interaction)",
          "timestamp": "2026-08-06T09:16:03+03:00",
          "tree_id": "c7c39c3d7057693aa5c89090306b1f67b11aeb75",
          "url": "https://github.com/serjflint/saitenka/commit/467b3cfb0c21bb49c0536df6a14c7f46208a9563"
        },
        "date": 1785996990678,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.868,
            "range": "±3.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.765,
            "range": "±12.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4ac7db18bec1f90808287228d5126ab7c953a250",
          "message": "fix(doctor): declutter output, one-line Anki-down errors, CLI smoke test (#201)\n\n* fix(anki): don't log tracebacks for expected AnkiConnect-down\n\nAnki can be closed at any moment, so a connection-refused while refreshing the\nknown-word cache or validating mining fields is an expected steady state, not a\nbug. Both sites logged it at debug with exc_info=True, dumping a full urllib\ntraceback into the log — which then surfaced as a screenful in `doctor`.\n\nAdd `anki.is_unreachable(exc)` (True for _AnkiRetryable or a raw OSError/URLError)\nand log those compactly, reserving the traceback for genuinely unexpected faults.\n\n* test(cli): registry-driven smoke test for every command's --help\n\nIterate the cyclopts command registry and assert each command's `--help` binds\nits full signature and exits 0, in-process. Catches a command that breaks on\nload (bad annotation, missing import, wiring) the moment it's added — unlike the\nhardcoded SUBCOMMANDS list, which silently drifts. Not a live test: --help\nshort-circuits before the command body, so it proves the wiring stands up.\n\n* refactor(doctor): collapse informational lines behind --verbose\n\nA healthy `doctor` was a 44-line wall of green. Add a `Check.info` flag for\npassing, purely-informational lines: the default view hides them, `--verbose`\nshows them, `--json` always carries the full set. Cut ~27 noise lines to ~17.\n\n- platform / PowerShell \"n/a\" lines → info off-Windows\n- the full per-dictionary list → info; the visible line is now per-kind counts\n  (`dicts: N · freq: M · pitch: K`); a configured-but-missing title still fails\n- safe `sub-auto`, unset mpv sockets, non-running SubMiner, disabled telemetry → info\n- one Anki-down warning: the `known` check defers to `anki` instead of warning twice\n- drop `check_perf` from doctor (its per-op/RSS is the doctor's own process, not a session)\n- collapse recent-log-errors to a compact per-record line, filtered by structured level\n- footer names the hidden count; telemetry line names its subject\n\nprint_report split into _shown_checks/_print_footer (drops below the complexity gate).",
          "timestamp": "2026-08-06T09:55:44+03:00",
          "tree_id": "9c1271dffba68aaeb8d7ddd0d13734f55869c2c8",
          "url": "https://github.com/serjflint/saitenka/commit/4ac7db18bec1f90808287228d5126ab7c953a250"
        },
        "date": 1785999377823,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.674,
            "range": "±3.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.615,
            "range": "±9.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "40bdceb28fa427abc848c175e2c6139fafeee1f6",
          "message": "Merge pull request #202 from serjflint/feat/overlay-hidpi-and-telemetry\n\nfix(overlay): hi-dpi chrome scaling + overlay-draw geometry telemetry",
          "timestamp": "2026-08-06T10:17:22+03:00",
          "tree_id": "36a9c771e412fc0ab8f03a5bffb791ff7153e25b",
          "url": "https://github.com/serjflint/saitenka/commit/40bdceb28fa427abc848c175e2c6139fafeee1f6"
        },
        "date": 1786000670569,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.79,
            "range": "±2.8%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.51,
            "range": "±8.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "2b0266586c0835fe523a0ab5966d5947d514c76f",
          "message": "Merge pull request #203 from serjflint/feat/doctor-stale-overlay-guard\n\nfeat(doctor): warn when a stale overlay process is running vs the installed build",
          "timestamp": "2026-08-06T11:25:18+03:00",
          "tree_id": "0523119919f5da5a722cc96fc9297a7a3f292902",
          "url": "https://github.com/serjflint/saitenka/commit/2b0266586c0835fe523a0ab5966d5947d514c76f"
        },
        "date": 1786004746952,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.718,
            "range": "±2.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.323,
            "range": "±7.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b518df2593d567588cfb208c23f81c0b453f2135",
          "message": "refactor(anki): AnkiConnect SSOT + Anki-optional as a tested invariant (#204)\n\n* refactor(anki): one AnkiConnect client, probe and error-set (SSOT)\n\nThree separate AnkiConnect request paths (the client, doctor's probe, the\ncoloring path's _ankiconnect) collapsed onto a single parametrised\n`Anki._call(*, timeout, attempts, trace)`. `anki_reachable` and doctor's\n`_anki_call` now route through it too, and the four duplicated except-tuples\nbecame the exported `ANKI_DOWN_ERRORS`. `is_unreachable` stays the sole\n\"just down\" classifier.\n\nFixes a latent bug: the coloring path's `_ankiconnect` silently dropped the\nAnkiConnect apiKey, so a keyed setup would fail known-word coloring.\n\n* feat(run): warn distinctly when Anki can't be started\n\n`_maybe_start_anki` ignored `launch_anki()`'s result and always printed the same\n\"launching…\" note. Now it threads the outcome: when Anki isn't found or the\nlaunch fails, `run` prints a distinct `warning: Anki is unavailable and couldn't\nbe started…` to the terminal and logs it, instead of implying a launch that\nnever happened. The console callback is `on_unreachable(*, launched)`.\n\n* test(anki): default the suite to Anki-down + optional-component contract\n\nAnki is usually closed in production, yet an autouse fixture forced it reachable\nfor all ~1700 tests, so the degradation path was the rare case (how the\ntraceback-on-expected-down bug survived). Flip the default: `_anki_down` makes\nAnkiConnect unreachable and neutralises `launch_anki`; tests that need it up opt\ninto the new `anki_up` fixture (only 2 — the ⊕-button-click tests).\n\nNew `test_anki_optional.py` names the invariant: the SSOT probe up/down, the\ncompact-vs-traceback logging (with a negative control), and the \"couldn't start\"\nterminal warning. Any code that hard-requires Anki now fails a test.",
          "timestamp": "2026-08-06T11:41:41+03:00",
          "tree_id": "018f28b8133f59271dc17288890095f6f0a61fb7",
          "url": "https://github.com/serjflint/saitenka/commit/b518df2593d567588cfb208c23f81c0b453f2135"
        },
        "date": 1786005728433,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.799,
            "range": "±3.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.935,
            "range": "±14.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "65e6b3736f40cd5f5a556e3b4770b75938f39a38",
          "message": "test(extras): contract that optional packages stay optional (#205)\n\nThe base wheel (no extras) must import without deinflect / taffylite / jamdict /\nOpenTelemetry. Assert it behaviourally: in a fresh interpreter, importing the\nconsole-script entry point (`overlay.app.cli`) pulls none of them into\nsys.modules — they load lazily, only when their feature is used. Holds whether\nor not the extras are installed (checks what the eager graph touches, not disk),\nand bites: importing a module that DOES hard-import an extra surfaces it.\n\nComplements test_anki_optional.py — that's the optional *service* (Anki down),\nthis is the optional *packages* (extra absent).",
          "timestamp": "2026-08-06T11:47:05+03:00",
          "tree_id": "abf1a589b9e15d8752008fbd8c4d77c56e119b6e",
          "url": "https://github.com/serjflint/saitenka/commit/65e6b3736f40cd5f5a556e3b4770b75938f39a38"
        },
        "date": 1786006062327,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.893,
            "range": "±3.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.762,
            "range": "±8.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "469b3926c82f7418ebbeacd2babd05996dfb54c6",
          "message": "test(extras): extend the optional-package contract to run/attach paths (#206)\n\nThe contract checked only the CLI surface (overlay.app.cli). Parametrise it over\nthe run/attach runtime graph too — cli_run (the command impl), reader_deps (the\ndep builder), and controller (the Reader payload with the tooltip/render stack) —\nwhere a stray top-level `import taffylite` / `import saitenka_deinflect` in the\nrender or deinflect code would actually hide. All four entry points pull no extras.",
          "timestamp": "2026-08-06T12:00:56+03:00",
          "tree_id": "690ee335abbcba7f547405730b7f984fc33c4517",
          "url": "https://github.com/serjflint/saitenka/commit/469b3926c82f7418ebbeacd2babd05996dfb54c6"
        },
        "date": 1786006886033,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.806,
            "range": "±3.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.629,
            "range": "±14.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8eb7b8212e3e4e0e92239038812958ccad4af1b4",
          "message": "Merge pull request #207 from serjflint/feat/100-episode-identity\n\nfeat(continuity): durable episode identity + sibling resolver (#100)",
          "timestamp": "2026-08-06T12:07:41+03:00",
          "tree_id": "213726c466ea5b5b15c478b6ffe1e8bda90f30fd",
          "url": "https://github.com/serjflint/saitenka/commit/8eb7b8212e3e4e0e92239038812958ccad4af1b4"
        },
        "date": 1786007288105,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.828,
            "range": "±3.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.765,
            "range": "±8.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c0cff84c10638e2782e9219b65a05cbae3550100",
          "message": "Merge pull request #210 from serjflint/feat/harness-doc-checker\n\nfeat: harness-engineering enforcement — doc↔code gates, restraint hooks, cross-family review",
          "timestamp": "2026-08-07T11:14:51+03:00",
          "tree_id": "8ad4f6aff18580f87759615052e7374c7f0099c6",
          "url": "https://github.com/serjflint/saitenka/commit/c0cff84c10638e2782e9219b65a05cbae3550100"
        },
        "date": 1786090547552,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.822,
            "range": "±3.8%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.762,
            "range": "±7.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "7606bd38e1b97db213c78695fda15822784a84fe",
          "message": "feat(test): census-lock the vendored external-oracle corpora (#211)\n\n* feat(test): census-lock the vendored external-oracle corpora\n\nWe steal upstream conformance suites as oracles (external-oracle-corpus-series:\nUAX #14 linebreak, Yomitan deinflect, SubMiner subtitle), but the denominator was\nunlocked — a re-vendor / re-gen could silently drop or mutate cases and the suite\nstill greened. `poe corpus-lock` (tools/corpus_check.py, now in `all`) binds each\ncorpus's case census (count + a SHA-256 over the sorted key-list) to a committed\nmanifest; drift fails until deliberately re-blessed (`corpus_check.py show`), the\nsame discipline docs-consts applies to constants. Two-sided planted +/- controls\nin tests/test_corpus_check.py. Grounds pg83's census-lock; stdlib-only, sub-second.\n\n* test(linebreak): pin the UAX-14 render-scope split as a counted ledger\n\nReplace the `len(cases) > 10_000` floor and the `> 0.99` rate with exact in-scope /\nout-of-scope counts. The out-of-scope filter was silent — an upstream reclassification\ncould demote an in-scope regression out of scope and hide it. The exact total is now\nlocked by `poe corpus-lock`; this pins the render-scope split, re-blessed deliberately\non a UCD bump.\n\n* test(subtitle): input-equivalence metamorphic oracle + catalog it\n\nparse_srt is invariant to line-endings (CRLF/LF) and trailing blank lines — a\nno-answer-key relation that catches formatting bugs the finite transcribed golden\nmisses, with a negative control proving it has teeth. Add the input-equivalence\nfamily to the oracle catalog and a corpus-lock note to AGENTS.md's Testing section.\n\n* fix(test): census-lock subtitle on full case, not just name (review)\n\nAdversarial review caught that _subtitle_keys hashed only the unique `name`, so a\nre-gen that mutated a case's content/expect (name kept) left the census hash\nunchanged — silently violating the \"or mutates\" guarantee the other two corpora\nuphold. Key on the whole case (json.dumps sort_keys) like deinflect/uax14, re-bless\nthe hash, and add an anti-regression control that the keys encode more than the name.\nAlso keep break_opportunities running on out-of-scope UAX-14 lines (smoke that exotic\ninput never raises) and fix the now-stale \"overall rate\" docstring.\n\n* docs: drop private-notes references from corpus_check docstring",
          "timestamp": "2026-08-07T17:53:03+03:00",
          "tree_id": "bd5cd809c52a68bbeba673227121748a24bfb8dc",
          "url": "https://github.com/serjflint/saitenka/commit/7606bd38e1b97db213c78695fda15822784a84fe"
        },
        "date": 1786114436884,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.686,
            "range": "±3.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.551,
            "range": "±8.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3f3d31dce92d34ea1dce18577010d814d30b8ee0",
          "message": "feat(subtitle): provider-agnostic source picker + windowed-resync arc; auto-advance (#100) (#208)\n\n* feat(watch): opt-in post-playback auto-advance to the next episode (#100)\n\nOn finishing an episode, re-slot the SAME mpv onto the next sibling in the\nfolder — smooth continuity without a manual file pick. Opt-in and default-off\n(`[watch].auto_advance`).\n\nAlso drop mpv's `--loop-file=inf` from every path: `--keep-open=yes` (always\nset) already holds the last frame at EOF, so a finished file now freezes on its\nlast frame instead of silently replaying — and a real EOF is exactly what\nauto-advance needs to observe. Screenshot mode still pauses on the first frame.\n\nMechanism (in-process re-slot, not a relaunch/playlist):\n- `controller`: observe `eof-reached`; on its rising edge call an installed\n  `advance_hook` once (re-armed when a fresh file clears eof). No hook → no-op.\n- `cli_run.reslot_episode`: close the finished episode's stats row, rebind the\n  leak-free `EpisodeContext` (#30 seam), `loadfile` the next file, then re-drive\n  the per-file subtitle/index/recorder/prefetch setup — everything `run_impl`\n  does for one file minus the launch. Session-scoped state (deck-mined set,\n  backlog, render caches) is untouched by construction.\n- Next-sibling resolution reuses #100's pure `continuity.resolve_sibling`;\n  missing/ambiguous → hold the last frame.\n\nSyncPlay gate = the mode split: only `run` (which owns playback) installs the\nhook; `attach`/SyncPlay never reach it, so they never auto-advance (#62\nprecedent). The eof edge, the re-slot's state transitions, and the launch flags\nare unit/integration-tested with the IPC fake; the live mpv loadfile/sub-add\nreconciliation is the enabling-user's test-drive.\n\nRefs #100\n\n* fix(anki): no startup traceback when Anki is down; detect a failed auto-launch\n\nTwo defects in the Anki-optional path, both hit when starting `run` with Anki\nclosed (an expected steady state):\n\n- **Traceback on expected-down.** The cache-miss known-word load caught a\n  hardcoded tuple that omitted `AnkiError`, so the client's `_AnkiRetryable`\n  (raised when AnkiConnect is unreachable) escaped to the top-level dep loader\n  and was logged with a full stack. Catch the `ANKI_DOWN_ERRORS` SSOT (which\n  covers `_AnkiRetryable`) instead — degrade to freq+JLPT coloring quietly, as\n  the connection-error path already did.\n- **Silent auto-launch \"success\".** macOS `launch_anki()` fired `open -a Anki`\n  via fire-and-forget `Popen` and always returned True, so a missing/renamed app\n  looked launched. `open -a` hands off and exits, so wait on it (`subprocess.run`)\n  and check the return code — a real failure now warns with mpv's reason and\n  returns False. win/linux stay fire-and-forget (the launch IS the app process).\n\nTests: `_AnkiRetryable` (not just OSError) degrades to the fallback known set;\nmacOS launch returns False when `open` reports the app missing / raises.\n\n* feat(subtitle): provider-agnostic source picker + windowed-resync arc (#100)\n\nWindow 1 — an in-mpv subtitle-source picker across every enabled provider.\nThe auto-pick can't tell a WebRip source from a broadcast rip on identical\n1080p tags (their cue timing differs by seconds), so the panel lists every\ncandidate best-match-first, tagged by provider, and lets the user pick the\nco-timed source; download is un-resynced on purpose (Ctrl+Shift+T stays the\nper-file fallback).\n\n- sub_picker: provider-agnostic panel driven by a lister thunk (built from\n  enabled_providers, like the retry factory) — reuses the sidebar row/hit\n  substrate, the forced mouse section (occlusion + click capture), and the\n  subtitle-fetch pipeline (add/select/re-index for free, no off-thread IPC).\n- subselect.list_candidates aggregates jimaku + tsukihime; a dead provider\n  becomes a warning row, never a blank panel. jimaku.episode_files exposes\n  the ranked list fetch already computed.\n- tsukihime.episode_candidates lists every (release, attachment) with NO\n  uniqueness guard — that guard stays on fetch's unattended path (certainty\n  for the robot, choice for the human).\n\nAlso lands the subtitle-timing arc behind it: windowed \"re-time from here\"\nresync, embedded-ref (native ASS) alignment with an English reference,\nresync_split_penalty, a per-run session id (quoted once at launch + in the\nreport, kept off every console line), rich subtitle.* telemetry, and the\ntools/subtitle_report.py report distiller. z/Z/x pass through to mpv's native\nrepeatable sub-delay (osd-level stays default).\n\n* fix(test): platform-agnostic path assertion in retry-resync test\n\n`_start_resync_window` wraps the video in `Path`, so `str(video)` renders with\nbackslashes on Windows; compare against `str(Path(...))` instead of a\nforward-slash literal.",
          "timestamp": "2026-08-08T02:17:41+03:00",
          "tree_id": "bac5d9bf831121a7bdf863eb4b0d243ff712f280",
          "url": "https://github.com/serjflint/saitenka/commit/3f3d31dce92d34ea1dce18577010d814d30b8ee0"
        },
        "date": 1786144690576,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.836,
            "range": "±3.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.829,
            "range": "±14.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f35c867dc5e87b67a7724e0dd2d125246be365e2",
          "message": "refactor(overlay): ordered Surface registry for OSD input chains (#212)\n\nThe controller's four input chains — forced-mouse capture, hover suppression,\nwheel scroll, click routing — hand-listed each surface, so a new surface had to\nbe wired into all four by hand; missing one silently left it shown-but-click-\nthrough (the #100 sub-picker occlusion bug).\n\nIntroduce app/surfaces.py: one SurfaceSpec per OSD surface in an explicit\ntopmost-first tuple (help, sub_picker, sidebar, preview, tooltip). Each spec is\na state accessor plus the hover/scroll/click predicates it handles; the four\nchains collapse to iterations over SURFACES. Behaviour is preserved exactly —\none z-order satisfies all four (capture = any-open OR; scroll/click = first\nclaimant, tooltip terminal; preview capture-only, its click stays in the tooltip\nhandler with its diagnostic log).\n\nShown-ness is now the uniform SurfaceState.open: PreviewState/TooltipState gain\nan `open` property (rect/tip_rect is not None); Help/Picker/Sidebar already had\nthe field. An explicit tuple over __init_subclass__ auto-registration keeps\nz-order legible and reviewable — the implicitness is what hid the bug.",
          "timestamp": "2026-08-08T02:45:44+03:00",
          "tree_id": "a786a4c81e92303aabb8ea22d8b08837bfe3ffa8",
          "url": "https://github.com/serjflint/saitenka/commit/f35c867dc5e87b67a7724e0dd2d125246be365e2"
        },
        "date": 1786146372539,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.822,
            "range": "±3.0%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.54,
            "range": "±7.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5e72338035145b85cf073b9bfb94ee65e426c5cf",
          "message": "feat(research): gh_audit.py companion to ground repo maturity (#213)\n\nAdds a stdlib-only activity auditor (stars/archived/releases/commit\nfreshness/license -> MAINTAINED/STALE/ABANDONED/ARCHIVED/FABRICATED) that\nshells `gh` and 404-gates on fabricated repos. Wires it into the skill's\nverification loop alongside verify.py and rot-guards it in smoke.sh.",
          "timestamp": "2026-08-08T02:56:02+03:00",
          "tree_id": "cd357495064542d0a14eb00d3763ad8f70a2b26c",
          "url": "https://github.com/serjflint/saitenka/commit/5e72338035145b85cf073b9bfb94ee65e426c5cf"
        },
        "date": 1786146984352,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.749,
            "range": "±3.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.595,
            "range": "±9.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "cd74d415166c01c5571dbf4776c50dae29b20e5d",
          "message": "chore(lint): gate ruff PLR0913/PLR0917 (too-many-args) at max-args=10 (#217)\n\nArg count was ungated: PLR0913/PLR0917 were ignored 'deferring to complexipy',\nbut complexipy measures cognitive complexity, not parameter count — so data-\nclumps (the ProviderConfig case) crossed no tripwire. Enable both at a loose\nmax-args=10 (ruff default is 5; the geometry/render helpers legitimately take\n6-8 coords) to catch only egregious clumps, and ratchet down over time (#216).\n\nThe over-10 tail carries a scoped '# noqa: PLR0913' — arg-clumps reference #216\n(bundle into a config object); cli.run/run_impl are a permanent exception\n(cyclopts builds --help/parsing from the flat signature). Only PLR0913/0917\nleave the ignore list; the CCN-adjacent count cousins stay complexipy's job.",
          "timestamp": "2026-08-08T03:58:44+03:00",
          "tree_id": "2fa38c908acf6d09184b558b55170457bfdfd985",
          "url": "https://github.com/serjflint/saitenka/commit/cd74d415166c01c5571dbf4776c50dae29b20e5d"
        },
        "date": 1786151018823,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.766,
            "range": "±3.0%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.636,
            "range": "±7.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "46dede92c771735305ff49667714bd03af132c29",
          "message": "refactor: bundle arg-clumps into config objects; ratchet ruff max-args to 9 (#216) (#218)\n\n* refactor(mining): bundle note content into config objects (#216)\n\nbuild_markers now takes the MarkerContext it used to construct internally;\nbuild_note takes a CardContent bundle (sentence/media/frequency) instead of\nsix positional content args. Drops two arg-clump noqas.\n\n* refactor(subtitles): bundle draw style+geometry into _SubStyle/_Place (#216)\n\nThe two subtitle draw helpers shared a font/metric/geometry clump computed\nonce per raster; hoist it into a frozen _SubStyle and pass per-token position\nas a _Place. Drops two arg-clump noqas.\n\n* refactor(render): bundle render_document geometry into DocStyle (#216)\n\npadding/gap/background/indent_px/gutter_px/base flow together through\nlayout+composite; bundle into a frozen DocStyle and make the out-params\nkeyword-only. Drops the PLR0913+PLR0917 arg-clump noqa.\n\n* refactor(mpv): bundle launch flags into MpvLaunchOptions (#216)\n\nbuild_mpv_argv and cli_run._launch_mpv_and_connect shared a slang/start/\nscreenshot/use_config/fullscreen/extra_args flag clump; hoist it into a frozen\nMpvLaunchOptions built once in run_impl. Drops two arg-clump noqas.\n\n* refactor(run): bundle subtitle-sourcing clump into RunSubtitleOptions (#216)\n\nsub_file/jimaku/jimaku_key/slang/resync threaded identically through\n_resolve_subtitles, reslot_to_current, _install_watch_hooks and\n_start_run_provider_fetch; bundle into a frozen RunSubtitleOptions built once\nin run_impl (per-episode title/episode stay separate). Drops two arg-clump\nnoqas and pulls the two 9/10-arg neighbours under the threshold too.\n\n* refactor(run): bundle CLI reader-option overrides into RunFlags (#216)\n\n_build_run_options took 14 CLI flags; bundle the 13 overrides into a frozen\nRunFlags built in run_impl (config keys still win where unset). Drops an\narg-clump noqa; tests use dataclasses.replace for per-case overrides.\n\n* refactor(run): bundle dep-build inputs into RunDepsRequest (#216)\n\n_build_run_deps took 14 args (mining flags + raw [mine] + known-words + dict/\nfreq/pitch titles); bundle into a frozen RunDepsRequest built in run_impl.\nDrops the last cli_run arg-clump noqa.\n\n* refactor(run): bundle demo/screenshot actions into DemoSpec (#216)\n\n_execute_reader_session took 11 args; bundle the scripted demo params\n(demo_word/screenshot/scroll/translate/mine/bulk/seconds) into a frozen\nDemoSpec shared with _run_demo_actions. Drops the last cli_run arg-clump noqa.\n\n* refactor(prewarm): bundle _PrewarmJob options into PrewarmTuning (#216)\n\nThe nine keyword-only mode/progress/plateau args become a frozen PrewarmTuning\n(the *-block the ctor already separated). Drops an arg-clump noqa.\n\n* refactor(bench): bundle run_trace knobs into TraceParams (#216)\n\nThe nine argparse-derived --trace knobs become a frozen TraceParams, unpacked\nto locals at the top so the body is untouched. Drops the last arg-clump noqa.\n\n* chore(lint): ratchet ruff max-args 10→9; bundle attach subtitle clump (#216)\n\nAll over-10 data-clumps are now config objects, so drop the threshold to 9.\nNewly exposed at 10: prepare_attach_startup → AttachSubtitleOptions (the attach\nanalog of RunSubtitleOptions); document._render_blocks → threads the existing\nDocStyle. The remaining PLR0913 noqas are NOT data-clumps — cyclopts commands\n(run/run_impl/attach) and render-geometry primitives (flow/banded); next\nratchet to 8 tracked in #216.\n\nAlso fix the two bench-module test loaders to register in sys.modules before\nexec_module — a @dataclass under 'from __future__ import annotations' resolves\nfield types via sys.modules[cls.__module__], which the unregistered manual\nimport left as None.",
          "timestamp": "2026-08-08T09:06:02+03:00",
          "tree_id": "cba6500d09cf95643a795064ce88465666ef7c34",
          "url": "https://github.com/serjflint/saitenka/commit/46dede92c771735305ff49667714bd03af132c29"
        },
        "date": 1786169212023,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.316,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.004,
            "range": "±2.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "2b7418fe3ff65d0209b18a3957aba4d78bc06573",
          "message": "feat(lint): adopt ruff preview rules; document PL error/fixable audits (#216) (#219)\n\nEnable ruff `preview = true` and triage the resulting findings across every\nselected family (preview is global, not PL-scoped).\n\nAdopted (auto-fixed): PLR6201 literal-membership (tuple→set), PLW1514\nunspecified-encoding (adds encoding=\"utf-8\" on JP subtitle fixtures — guards the\nWindows CI leg), PLR6104 non-augmented-assignment, RUF031/RUF055/RUF056,\nFURB140 reimplemented-starmap.\n\nPruned with a reason (deliberate/noisy/stylistic): RUF105 (fights the documented\nper-site # noqa convention), RUF069 (deliberate exact float sentinels, e.g.\nscale == 1.0), RUF052, RUF067, PLR6301, PLC2701, PLC1901, PLW0717, S404\n(subprocess launch is intended), FURB113, FURB118, FURB154 (would squash\notel_metrics' 47-name global block into one unwrappable line). B006/B903 scoped\nto tests/**.\n\nAlso records the two audit outcomes from #216's comments: ruff's 38 PLE error\nrules stay fully on (never prune an error sub-rule); pylint's error rules ruff\nlacks are all astroid type-inference checks already owned by poe types, so no\npylint gate earns a place. PLW0108 stays ignored (unsafe-only fix + intentional).\n\npoe all green (1930 passed, 90.32% cov).",
          "timestamp": "2026-08-08T10:15:14+03:00",
          "tree_id": "1d9a053d9285b4b7ce55c8e63388222a798d6f9a",
          "url": "https://github.com/serjflint/saitenka/commit/2b7418fe3ff65d0209b18a3957aba4d78bc06573"
        },
        "date": 1786173360423,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.303,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.918,
            "range": "±2.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "9055fc06ad55fe7e136e34d4f273578ffd29c37c",
          "message": "feat(quality): one tool-config SSOT at the repo root, over all Python (#216) (#220)\n\n* feat(lint): hoist ruff config to repo root — one SSOT over all Python (#216)\n\nMove [tool.ruff*] from overlay/ to the workspace-root pyproject so a single\nconfig governs overlay + deinflect + taffylite + tools + install + .agents.\nlint/format now run repo-wide (`cd .. && uv run --project overlay ruff …`).\n\n- rule-codes-in-selectors (preview) normalises selectors to rule names.\n- TID251 GPL/layout chokepoint bans moved to per-LINE noqa at each sanctioned\n  import (more precise: a SECOND illicit import in the same file still fails).\n- deinflect/taffylite own self-import exempted (the ban guards OVERLAY).\n- Dev/demo/infra scripts (examples/tools/.agents/install/compare, vulture\n  whitelist) get script-appropriate relaxations (\"harden without fanaticism\");\n  genuine correctness issues FIXED at source: missing-comma implicit concat,\n  loop-variable capture (B023), unclosed file, bidi/zero-width strip-list noqa,\n  unspecified text encoding, deinflect Rule(...) → keyword args.\n\nruff check + format clean across the whole repo.\n\n* feat(quality): complexipy + type-checkers repo-wide; scope types to library (#216)\n\nStage 2+3 of the SSOT unification:\n- complexipy runs repo-wide (overlay + deinflect + taffylite + tools + install\n  + .agents); baseline snapshot moved to the repo root and regenerated. Gate green.\n- mypy/basedpyright/pyrefly cover LIBRARY code (overlay + deinflect) — imported,\n  shipped, contract-bearing. Dev scripts (tools/install/.agents) stay out of the\n  heavy type tier (\"harden without fanaticism\"); ruff + complexipy still cover them.\n  Extending mypy to deinflect caught a real set/tuple type mismatch (now fixed via\n  an explicit `seen` annotation).\n- basedpyright extraPaths + [tool.pyrefly] search-path so first-party imports\n  resolve from source when the checkers run from the repo root.\n- pyrefly ignore for the stubless fugashi.Tagger (matches the pyright/ty ignores).\n\npoe all green (1930 passed, 90.34% cov); deinflect suite green (1290).\n\n* fix(types): resolve taffylite via mypy_path stub, not the root namespace dir (#216)\n\nRunning mypy from the repo root, `import taffylite` resolved to the root\n`taffylite/` directory as an empty namespace package whenever the Rust ext\nwasn't installed (the CI gate env — the layout-engine extra is deliberately\nabsent), so `taffylite.column` errored `attr-defined`. It only passed locally\nbecause the editable ext happened to be installed. Put `taffylite/python` (and\ndeinflect/src) on mypy_path so mypy finds the committed __init__.pyi stub\n(which declares `column`) consistently.\n\n* feat(types): local fugashi stub — drop the triple type-ignore (#216)\n\nfugashi is a Cython MeCab wrapper that ships no py.typed, and an upstream one is\nunlikely, so `fugashi.Tagger()` carried a fragile three-checker ignore (pyright +\nty + pyrefly — whose codes for the same issue already diverged). Add a minimal\nlocal stub (stubs/fugashi.pyi: Tagger + node.surface/.feature) wired via mypy_path\n/ basedpyright stubPath / pyrefly search-path, and drop the ignores. Kept deliberately\nminimal — feature fields are read via getattr, so Any is correct. ty is best-effort\n(|| true) and reads its own config, so it still treats it as unresolved; harmless.\n\npoe all green.",
          "timestamp": "2026-08-08T11:16:17+03:00",
          "tree_id": "6c5e139f507b54b77fa0b35d4fb222bb6720c051",
          "url": "https://github.com/serjflint/saitenka/commit/9055fc06ad55fe7e136e34d4f273578ffd29c37c"
        },
        "date": 1786177022722,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.202,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.54,
            "range": "±3.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b7964c3cd023ff80d9586be35a413fb8d40fc8af",
          "message": "refactor(overlay): break all 6 module-import cycles; enforce no-cycles fully (#30) (#221)\n\nDrive .importlinter's no-cycles ignore list from 6 grandfathered entries to\nzero — the contract is now exhaustively enforced. Each cycle was severed behind\na stable seam, behaviour-preserving:\n\n- draw.chip -> render.layout: chip.py moved draw/ -> render/ (it consumes\n  render.layout, so it sits at/above it, not below). No golden text-primitive\n  movement.\n- dictdb <-> yomitan_import and dictdb <-> wordlists: the pure zip/bank-parse\n  helpers (classify_zip, read_json_bank, _title_of, _crc_lenient, _META_BANK)\n  extracted to a new leaf app/bankreader.py that imports nothing from app/, so\n  the DB, settings importer and list builder all sit above it.\n- doctor/report -> crashlog: crash_dir() moved to the leaf app/paths.py (its\n  only dep was cache_dir there anyway), so those modules locate reports without\n  importing the excepthook module (which reaches back to them for the log\n  tail / redaction). crashlog re-exports crash_dir for the monkeypatch seam.\n- controller <-> miner: miner imports SKIP_POS from its true home app/tokenize\n  (like nested_popup/prefetch already do), not from controller.\n\nTests point at the new canonical homes (bankreader). poe all green;\nimport-linter: 6 contracts kept, 0 broken.",
          "timestamp": "2026-08-08T12:42:03+03:00",
          "tree_id": "dbc2a1badf9513de92f8f7a19414e721fd8ddf0a",
          "url": "https://github.com/serjflint/saitenka/commit/b7964c3cd023ff80d9586be35a413fb8d40fc8af"
        },
        "date": 1786182145810,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.178,
            "range": "±3.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.252,
            "range": "±5.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d874d4c608e0d5710e3cd92efdec75388f991a57",
          "message": "feat(subtitles): picker choice force-selects the source, even from English (#100) (#226)\n\nOpening the source picker while English subtitles are on screen and choosing a\nJP candidate left English up: the pick routed through the non-disruptive\nbackground add, whose auto-select is gated on `not had_japanese and\ncurrent_sid == initial_sid` (a keep-current contract for UNATTENDED fetches). An\nexplicit pick is the opposite intent.\n\nAdd a `force_select` flag threaded picker → start_fetch → SubtitleFetchResult →\napply_fetch_results, which routes to `_replace_japanese_track` (drop stale\nexternal, sub-add \"select\", zero sub-delay, rebuild the index) from any current\nlanguage. The picker drops `replace`/`select_if_unchanged` for it. Toast is\n\"Japanese subtitles selected\".\n\nNote: the alass audio-VAD-from-English item stays open — it needs a real attach\nreport to diagnose the ffprobe/ffmpeg PATH resolution (#215 telemetry).",
          "timestamp": "2026-08-08T12:48:11+03:00",
          "tree_id": "d35ecfc65d87de6c5f420503ad4192903e12b606",
          "url": "https://github.com/serjflint/saitenka/commit/d874d4c608e0d5710e3cd92efdec75388f991a57"
        },
        "date": 1786182518426,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.181,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.593,
            "range": "±4.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "26fdec41b93ae133e23e62054235f54aee372847",
          "message": "refactor: ratchet ruff max-args 9→8; bundle the remaining arg-clumps (#216) (#224)\n\nComplete the 9→8 max-args ratchet. Lowering the threshold surfaces 11 functions\nat 9 args (not the two the issue predicted); each is bundled behind a cohesive\nfrozen config object or relaxed as a script:\n\nRender (the issue's named blockers):\n- banded.WindowedPanel.__init__ → BandedTuning (seed_height/max_cached_blocks/\n  compress/raw_band_ceiling).\n- flow._draw_flow_line → _LinePlacement + a shared render.layout.Sinks\n  (scan_out/link_out — the repo-wide \"geometry sink\").\n- document.py _render_block/_composite/_draw_window_block (+_render_blocks/\n  _composite_window) → the same shared Sinks; public render_document/\n  render_layout_window signatures unchanged.\n\nApp:\n- miner._markers_for → shares the CardContent the note is built from (drops a\n  7-field re-derivation).\n- prewarm.prewarm → PrewarmOptions (atlas_only/atlas_scale/plateau_stop).\n- subselect.ensure_jp_subs → the existing AttachSubtitleOptions (its sole caller\n  already unpacked it field-by-field).\n- subtitles.render_subtitle → SubtitleSpacing (pad_x/pad_y/line_gap, defaulted —\n  no call site passes them).\n- nested_popup.place_nested → Anchor (wx/wy/wh).\n\nexamples/ and tools/ relax too-many-arguments (run-once scripts, not shipped\nAPIs). docs_check gains _field_default so the seed_height const resolves from\nBandedTuning. poe all green (incl. test-ft + goldens); PLR0913/PLR0917 at 8 = 0\nviolations. Only the cyclopts CLI commands keep a justified noqa.",
          "timestamp": "2026-08-08T12:51:35+03:00",
          "tree_id": "5454ca74e4ae03d72eb0a525a76adf2a8dd7e2e4",
          "url": "https://github.com/serjflint/saitenka/commit/26fdec41b93ae133e23e62054235f54aee372847"
        },
        "date": 1786182725007,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.314,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.964,
            "range": "±6.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "6787c7d04c75ad5ffafc1495dbace4f906776595",
          "message": "Merge pull request #229 from serjflint/feat/223-episode-prefetch-cue-retention\n\nfeat(subtitle): full-episode token prefetch + cue retention on track switch (#223)",
          "timestamp": "2026-08-08T14:47:33+03:00",
          "tree_id": "816fc8c2db61af976a84ef38e0b2c4852b455b87",
          "url": "https://github.com/serjflint/saitenka/commit/6787c7d04c75ad5ffafc1495dbace4f906776595"
        },
        "date": 1786189721432,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.234,
            "range": "±0.1%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.646,
            "range": "±0.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a94675c095fa6bd558bdd85bc7fa5f2bf8c8aa4a",
          "message": "Merge pull request #230 from serjflint/chore/release-2.0.0\n\nchore(overlay): release 2.0.0",
          "timestamp": "2026-08-08T15:36:59+03:00",
          "tree_id": "37f68c1a8590820aaf6b0cc426ec1a48ffa3e579",
          "url": "https://github.com/serjflint/saitenka/commit/a94675c095fa6bd558bdd85bc7fa5f2bf8c8aa4a"
        },
        "date": 1786192676726,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.275,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.863,
            "range": "±1.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5b4a17e0f127728180a1cbd2bf86f5e71737a869",
          "message": "fix(subprocess): UTF-8 everywhere for spawned tools + content-free dict-DB stats (#234)\n\n* fix(subs): adopt an untagged newly-primary track as Japanese\n\nWhen mpv makes a subtitle track primary that saitenka doesn't already know (a manual\ntrack cycle, or a drag-'n'-dropped sub file), on_primary_changed ignored it — so an\nUNTAGGED Japanese track (mpv reports 'unknown language') stayed in the English fallback\nand every cue rendered plain (white, uncolored, left-flowed in an 86%-width box) instead\nof the colored, centered Japanese overlay.\n\nAdopt any newly-primary track that isn't tagged English as the Japanese target and index\nit from disk, mirroring the wildcard rule discover_tracks already uses at startup. English\nis gated on a real, non-empty tag — lang_matches(None, EN_LANGS) is a false wildcard match,\nso an untagged track is never misread as English.\n\n* feat(subs): keybind to force the current track as Japanese (Alt+j)\n\nThe manual override paired with auto-adoption: when the active track is untagged and\nauto-adoption guessed wrong (an untagged track that is really English), or the user just\nwants to assert it, Alt+j forces mpv's current primary track as the Japanese target,\nre-indexes it, and recolors the on-screen cue. Acts from within mpv, no CLI flag.\n\n* feat(subs): identify Japanese by content (kana/kanji), not just language tags\n\nRefine untagged-track adoption: instead of blindly assuming an untagged newly-primary\ntrack is Japanese, classify it by its actual cue text. looks_japanese() checks for\nJapanese script (hiragana/katakana/half-width kana/kanji Unicode blocks); the track is\nindexed from disk first so the cues can be sampled. A dropped untagged Japanese sub\ncolors; a dropped untagged English sub now correctly stays the plain secondary instead of\nbeing miscolored as Japanese — the case a language tag can't decide.\n\n* feat(mpv): auto-reconnect a dropped IPC pipe (mpv.net) + label mpv.net in doctor\n\nmpv.net drops its IPC named pipe mid-session (a transient WinError 109 that vanilla mpv\ndoesn't emit), which killed the overlay until the user relaunched. On a dropped pipe,\npump() now re-dials the SAME endpoint on the IPC thread and replays observers (the\ncontroller re-issues observe_property, which a fresh mpv connection has forgotten).\nBounded by a per-process budget so a genuinely-quit mpv still exits after failed dials;\nclose() marks an intentional shutdown so it never reconnects then. Each attempt/outcome is\nlogged so a report shows whether recovery worked.\n\ndoctor now labels a resolved mpv.net binary as 'mpv.net' even when it reports a parseable\nmpv version, so the report names which player is active.\n\n* fix(subprocess): force encoding=\"utf-8\" on every text-mode subprocess call\n\ntext=True / universal_newlines=True decodes child output with the OS locale\ncodec (cp932/cp1252 on non-UTF-8 Windows). ffprobe stream tags and filenames\nare UTF-8, so on a Japanese-locale box the embedded-sub reference probe and the\nversion/duration/lang probes mojibake or raise — a friend's report showed the\ncp932 bug class (the tag-sidecar write, since removed, hit the same wall).\n\nFix all 12 text-mode sites (anki/conflicts/doctor/media/report/resync/version),\nand enforce it: a new ast-grep rule (subprocess-utf8-encoding) flags any\nsubprocess.*(text=True) without encoding=, run by `poe invariants` (in `poe\nall`). ruff's PLW1514 covers files only — verified no ruff rule catches\nsubprocess. The rule immediately caught two sites a manual sweep missed.\n\n* feat(doctor): content-free dictionary-DB stats in report + doctor\n\nThe report bundle deliberately excludes dictionaries, which left the tags\nquestion (is a dict's `tags` table populated, or is it sidecar-era residue?)\nunanswerable from a bundle. Add `DictionaryDb.stats()` — a content-free\nsnapshot (schema, file size, per-dictionary row counts for\nentries/keys/kanji/term_meta/tags) that names no term, reading, or gloss, so\nit's safe to ship in diagnostics.\n\n`dicts.listing.txt` now carries the schema/size header and per-dict counts;\ndoctor's check_dict_db emits the same counts as info-tier lines (--verbose/--json\nonly). A dict-kind dictionary with entries but no tags is the sidecar-era tell —\nnow visible without guessing, so \"re-import to fix tag pills\" is a fact, not a\nhunch.",
          "timestamp": "2026-08-08T18:12:30+03:00",
          "tree_id": "4a9d7c067c7d166143604613d279fbe4dbb6cb04",
          "url": "https://github.com/serjflint/saitenka/commit/5b4a17e0f127728180a1cbd2bf86f5e71737a869"
        },
        "date": 1786201974455,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.314,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.657,
            "range": "±4.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5e826ae9473a665fee6898713a643cdc3011a01c",
          "message": "test(layout): strengthen two-engine parity — negative controls, boundary examples, config-seam matrix (#232)\n\nUse the taffy Rust engine (dev-only layout-engine extra) as a stronger cross-validation\noracle for the pure-Python layout:\n- pin boundary @examples on the taffy↔default parity property (empty / single / zero-height\n  / zero-gap / large-value float-cast boundary), so edges run every time, not just by chance;\n- add two negative controls proving the parity gates are non-vacuous (a 1px-shifted backend\n  fails the cumulative/solve equality; two content windows fail the pixel diff);\n- differential-test the production seam resolve_backend(\"taffy\") → WindowedPanel across a\n  matrix of entry shapes × widths, not just one canonical panel.",
          "timestamp": "2026-08-08T18:21:40+03:00",
          "tree_id": "7ddbe9c44aa05a1a679fbf7755c48037fc16e288",
          "url": "https://github.com/serjflint/saitenka/commit/5e826ae9473a665fee6898713a643cdc3011a01c"
        },
        "date": 1786202529787,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.348,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.078,
            "range": "±12.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0aba8e5ffa3d1a13e9b3390c5ddde54c91798546",
          "message": "chore(overlay): release 2.0.1 (#235)\n\n* fix(mpv): don't hang on quit — probe liveness before keeping a reconnect\n\nThe mpv.net auto-reconnect (for transient IPC pipe drops) also fired on a normal mpv quit: a self-launched run-mode mpv exiting is EOF (not our close()), so pump() re-dialed a socket that can accept a connect yet never reply, hanging the poll loop for command()'s full timeout up to _MAX_RECONNECTS times (a ~10s×30 zombie on quit, seen live in a macOS run-mode report). After a re-dial, probe once with a short-timeout get_property: a transient drop answers immediately; a quit yields our disconnected/timeout sentinel, so we bail and let pump() raise → the overlay exits promptly. Any real mpv reply keeps the reconnection. Regression guard in test_ipc_chaos.\n\n* chore(overlay): release 2.0.1",
          "timestamp": "2026-08-08T19:05:54+03:00",
          "tree_id": "f7b3860dcc039b2c269251bd5eb41f80357171b0",
          "url": "https://github.com/serjflint/saitenka/commit/0aba8e5ffa3d1a13e9b3390c5ddde54c91798546"
        },
        "date": 1786205207455,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.327,
            "range": "±0.8%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.762,
            "range": "±2.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a84d744bbe80a2fb2d4eebc565170649f95ad101",
          "message": "Merge pull request #236 from serjflint/fix/subtitle-resync-reliability-100\n\nfix(resync): reliable manual subtitle re-sync (part of #100)",
          "timestamp": "2026-08-08T22:35:26+03:00",
          "tree_id": "2e7aad47e82b60d0c32559281bae3836a4359aaf",
          "url": "https://github.com/serjflint/saitenka/commit/a84d744bbe80a2fb2d4eebc565170649f95ad101"
        },
        "date": 1786217752594,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.375,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.173,
            "range": "±2.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1fecb46ac04519851736b92cccfaea3daddedee7",
          "message": "Merge pull request #239 from serjflint/feat/subtitle-cache-realname\n\nSubtitle cache real-extension storage + doctor resync-toolchain check (#237, #238)",
          "timestamp": "2026-08-08T23:28:50+03:00",
          "tree_id": "f2151b041f6f7ab2282b6380a300a6d530305215",
          "url": "https://github.com/serjflint/saitenka/commit/1fecb46ac04519851736b92cccfaea3daddedee7"
        },
        "date": 1786220954635,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.334,
            "range": "±0.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.386,
            "range": "±6.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d655e0ebf2e44112d771c60ef0fef2e997098e43",
          "message": "Merge pull request #240 from serjflint/chore/release-2.1.0\n\nchore(overlay): release 2.1.0",
          "timestamp": "2026-08-08T23:42:29+03:00",
          "tree_id": "f7fcb5a59479f555268d673022e91e872cd3c1cf",
          "url": "https://github.com/serjflint/saitenka/commit/d655e0ebf2e44112d771c60ef0fef2e997098e43"
        },
        "date": 1786221799822,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.387,
            "range": "±0.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.857,
            "range": "±0.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c52136d3a87ac1aa4d9658a5d46768280363494a",
          "message": "Merge pull request #241 from serjflint/feat/jimaku-verify-and-keyring-optout\n\nfeat(jimaku): verify key after save + keyring opt-out for Windows AV",
          "timestamp": "2026-08-09T01:22:57+03:00",
          "tree_id": "357b1d32f2d9c6d45aa2e30338d2b8891ffe9b88",
          "url": "https://github.com/serjflint/saitenka/commit/c52136d3a87ac1aa4d9658a5d46768280363494a"
        },
        "date": 1786227804867,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.921,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.049,
            "range": "±3.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "9d12457297e2f3b313121f603c26b88e826384e8",
          "message": "Merge pull request #244 from serjflint/feat/mining-keybind-observability\n\nfix(mining): mine keybinds silently unbound in attach mode (async Anki)",
          "timestamp": "2026-08-09T01:23:41+03:00",
          "tree_id": "d232ed295e4ac296c033e8de9642401628cf410c",
          "url": "https://github.com/serjflint/saitenka/commit/9d12457297e2f3b313121f603c26b88e826384e8"
        },
        "date": 1786227855799,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.291,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.708,
            "range": "±5.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "92a5cc8f7692cf74fda1adef9ca137aac0b6d95e",
          "message": "Merge pull request #246 from serjflint/feat/session-mode-logging\n\nfeat(telemetry): log session mode (run vs attach) at startup",
          "timestamp": "2026-08-09T01:24:25+03:00",
          "tree_id": "77ddbe0c0a8020911afddaff074b3d3a50f570b8",
          "url": "https://github.com/serjflint/saitenka/commit/92a5cc8f7692cf74fda1adef9ca137aac0b6d95e"
        },
        "date": 1786227892983,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.284,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.244,
            "range": "±8.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e306e331fa9f3cc791f3b55f0118a2828ee78240",
          "message": "Merge pull request #247 from serjflint/chore/release-2.2.0\n\nchore(overlay): release 2.2.0",
          "timestamp": "2026-08-09T01:45:58+03:00",
          "tree_id": "d29a16bf7ffa7602a6db502addbc8e43e95fed6a",
          "url": "https://github.com/serjflint/saitenka/commit/e306e331fa9f3cc791f3b55f0118a2828ee78240"
        },
        "date": 1786229202007,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.808,
            "range": "±0.1%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.763,
            "range": "±0.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0d80b9166c1c6509648a9e78b00db72f11a85f7a",
          "message": "Merge pull request #248 from serjflint/fix/e2e-windows-green\n\nfix: green the Windows e2e leg (UTF-8 reads, path-sep test, artifact dirtying)",
          "timestamp": "2026-08-09T02:38:11+03:00",
          "tree_id": "a8f93e655fa6fe330753375e011452b640ce2e0d",
          "url": "https://github.com/serjflint/saitenka/commit/0d80b9166c1c6509648a9e78b00db72f11a85f7a"
        },
        "date": 1786232314635,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.349,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.846,
            "range": "±0.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "fff58ede191ca20e359b347ba54cb0ea7622a813",
          "message": "Merge pull request #250 from serjflint/test/hermetic-config\n\ntest(conftest): isolate SAITENKA_CONFIG (hermetic against the dev's real overlay.toml)",
          "timestamp": "2026-08-09T02:46:00+03:00",
          "tree_id": "ee9a75045c467cb898439fecda083ddf084e399b",
          "url": "https://github.com/serjflint/saitenka/commit/fff58ede191ca20e359b347ba54cb0ea7622a813"
        },
        "date": 1786232788351,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.32,
            "range": "±0.1%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.774,
            "range": "±4.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ee80b76085f987206db4c98176715368880f0c7c",
          "message": "Merge pull request #256 from serjflint/test/hardening-observability-registry\n\ntest: observability + registry hardening (otel log-spam, session-mode, doctor status set, mine addability)",
          "timestamp": "2026-08-09T11:08:24+03:00",
          "tree_id": "57fe6b8c15d4859dad3ca1eff6952f3e708628ad",
          "url": "https://github.com/serjflint/saitenka/commit/ee80b76085f987206db4c98176715368880f0c7c"
        },
        "date": 1786262938537,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.079,
            "range": "±1.0%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.816,
            "range": "±10.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e459edb6de794a1eeb08bd9d4ad25e6a4f3cc8ca",
          "message": "Merge pull request #260 from serjflint/feat/deeplink-id-doctor-255\n\nfeat(doctor): warn when the deep-link ID field has no id source (#255, slice 1)",
          "timestamp": "2026-08-09T11:11:24+03:00",
          "tree_id": "38638af1846c925654d817c9df4ef80b1ab31e98",
          "url": "https://github.com/serjflint/saitenka/commit/e459edb6de794a1eeb08bd9d4ad25e6a4f3cc8ca"
        },
        "date": 1786263113524,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.273,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.051,
            "range": "±4.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "833c96db427aba538fc526ac27f128a8a0c7b7aa",
          "message": "Merge pull request #261 from serjflint/feat/tokenizer-strategy-spike\n\nrefactor(tokenize): Reader-owned Tokenizer strategy seam (#254 spike)",
          "timestamp": "2026-08-09T12:40:18+03:00",
          "tree_id": "f212235b7630b8db97ae625c5f6eb374ea415863",
          "url": "https://github.com/serjflint/saitenka/commit/833c96db427aba538fc526ac27f128a8a0c7b7aa"
        },
        "date": 1786268446146,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.255,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.57,
            "range": "±1.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "48f0d855b5709096f372366cd07e8359ebe6b5bd",
          "message": "Merge pull request #263 from serjflint/feat/253-mine-tab\n\nfeat(sidebar): per-episode mined-card store + Mine tab (#253)",
          "timestamp": "2026-08-09T12:46:23+03:00",
          "tree_id": "c0744a9e39047b94c90844aea02690946e3c5174",
          "url": "https://github.com/serjflint/saitenka/commit/48f0d855b5709096f372366cd07e8359ebe6b5bd"
        },
        "date": 1786268817943,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.266,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.804,
            "range": "±2.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d9247e9778f24d4849313cdb4771b7c21c5f83af",
          "message": "Merge pull request #262 from serjflint/feat/231-225-infra\n\ninfra: taffylite PyPI release workflow (#231) + ast-grep named-args advisory (#225)",
          "timestamp": "2026-08-09T12:50:13+03:00",
          "tree_id": "2610d4a4c4f13f6c0d51ecafb16249ac79c9eae8",
          "url": "https://github.com/serjflint/saitenka/commit/d9247e9778f24d4849313cdb4771b7c21c5f83af"
        },
        "date": 1786269059997,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.857,
            "range": "±0.6%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.832,
            "range": "±1.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "47d394117c8fda0e1dfc31ef97f790e0805067e5",
          "message": "Merge pull request #265 from serjflint/feat/257-config-editor\n\nfeat(cli): interactive `saitenka config` editor + typed schema catalog (#257)",
          "timestamp": "2026-08-09T13:08:39+03:00",
          "tree_id": "879632fe2abb283a4753bf951908ad702656b577",
          "url": "https://github.com/serjflint/saitenka/commit/47d394117c8fda0e1dfc31ef97f790e0805067e5"
        },
        "date": 1786270142648,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.235,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.02,
            "range": "±7.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f8ac0b82462f6b1be10211936b6a0bf3c0c8719f",
          "message": "Merge pull request #266 from serjflint/feat/254-t2-route-callers\n\nrefactor(tokenize): route remaining callers through reader.tokenizer (#254 3a.2)",
          "timestamp": "2026-08-09T13:12:47+03:00",
          "tree_id": "0e73533b413a9a2fead8aee7c1e2c164110b33ca",
          "url": "https://github.com/serjflint/saitenka/commit/f8ac0b82462f6b1be10211936b6a0bf3c0c8719f"
        },
        "date": 1786270395775,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.256,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.838,
            "range": "±1.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a3b086033d5b1342114b0e55bf8fe7143995295c",
          "message": "Merge pull request #264 from serjflint/feat/255-deeplink-id-remainder\n\nfeat(dictdb): offline deep-link ID via entries.seq + doctor data-check (#255)",
          "timestamp": "2026-08-09T13:31:31+03:00",
          "tree_id": "6b31accdf736cb3efda3dbb86f8ee4ca636fdb9b",
          "url": "https://github.com/serjflint/saitenka/commit/a3b086033d5b1342114b0e55bf8fe7143995295c"
        },
        "date": 1786271515064,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.305,
            "range": "±0.6%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.849,
            "range": "±0.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "24f9480a59d98fac23d8266f561254962ef883f6",
          "message": "Merge pull request #267 from serjflint/feat/254-t3-decouple-pos\n\nrefactor(tokenize): move content/skippable classification onto the Tokenizer (#254 3a.3)",
          "timestamp": "2026-08-09T13:39:35+03:00",
          "tree_id": "1728f2bfb11f92ce8d552ff0cea806fdb33e0067",
          "url": "https://github.com/serjflint/saitenka/commit/24f9480a59d98fac23d8266f561254962ef883f6"
        },
        "date": 1786272005371,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.232,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.061,
            "range": "±4.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "454da6804363cf1966301a1b3cfb6637e4569ff1",
          "message": "Merge pull request #268 from serjflint/feat/254-p1-provider-gating\n\nfeat(subtitles): data-driven provider capability registry by language (#254 p1)",
          "timestamp": "2026-08-09T13:45:45+03:00",
          "tree_id": "f61344f9450f104fd9554361acb16c26ca4ae861",
          "url": "https://github.com/serjflint/saitenka/commit/454da6804363cf1966301a1b3cfb6637e4569ff1"
        },
        "date": 1786272368590,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.852,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.633,
            "range": "±1.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e12cd845c2c96f829d2e90961d809839cdf9797a",
          "message": "Merge pull request #269 from serjflint/chore/ci-uv-cache-suffix\n\nci: per-interpreter uv cache-suffix so 3.15/3.15t numpy build persists",
          "timestamp": "2026-08-09T14:06:51+03:00",
          "tree_id": "29b9efac05471f8078092eafd1f8fe67ef1885ce",
          "url": "https://github.com/serjflint/saitenka/commit/e12cd845c2c96f829d2e90961d809839cdf9797a"
        },
        "date": 1786273633805,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.241,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.621,
            "range": "±4.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e251760043caa8e39393fa211b36367ddadbae45",
          "message": "Merge pull request #270 from serjflint/feat/254-p2-profile-config\n\nfeat(profiles): [profiles.*] config surface + active-profile routing (#254 p2)",
          "timestamp": "2026-08-09T14:32:39+03:00",
          "tree_id": "07e9a5fc348a20a168294f189d1f2b1d91566c8a",
          "url": "https://github.com/serjflint/saitenka/commit/e251760043caa8e39393fa211b36367ddadbae45"
        },
        "date": 1786275181853,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.311,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.78,
            "range": "±2.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0c2c231869a26c7a68aab017ce4d24579878cca9",
          "message": "Merge pull request #271 from serjflint/feat/93-word-audio\n\nfeat(mine): offline word-pronunciation audio from local yomichan packs (#93)",
          "timestamp": "2026-08-09T15:02:33+03:00",
          "tree_id": "9f55671df247b551946f04b257823952beb2a10e",
          "url": "https://github.com/serjflint/saitenka/commit/0c2c231869a26c7a68aab017ce4d24579878cca9"
        },
        "date": 1786276980448,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.396,
            "range": "±0.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.147,
            "range": "±2.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "902c7628ce8464e9dfe55d5548fcfd4c4b9b151e",
          "message": "Merge pull request #272 from serjflint/feat/254-p5-live-switcher\n\nfeat(profiles): live in-overlay profile switcher + cache-swap race guard (#254 p5)",
          "timestamp": "2026-08-09T15:09:57+03:00",
          "tree_id": "c5b98574ad87dd4819ae7e3a97649224166929a2",
          "url": "https://github.com/serjflint/saitenka/commit/902c7628ce8464e9dfe55d5548fcfd4c4b9b151e"
        },
        "date": 1786277424708,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.822,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.099,
            "range": "±7.2%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5a5ec4cd04a443d440f415b6bd2c25deb4e3bb1e",
          "message": "Merge pull request #273 from serjflint/chore/ci-split-ft-tests\n\nci: run the free-threaded suite as a parallel job + GIL-on control on t-legs",
          "timestamp": "2026-08-09T15:27:53+03:00",
          "tree_id": "153f0d5a75b049c3b67f492bb94c078e006df860",
          "url": "https://github.com/serjflint/saitenka/commit/5a5ec4cd04a443d440f415b6bd2c25deb4e3bb1e"
        },
        "date": 1786278499427,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 7.054,
            "range": "±0.9%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 10.232,
            "range": "±6.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8e29b85680aea0045fe4437a1f9307cef78a530a",
          "message": "Merge pull request #274 from serjflint/feat/254-p4-dict-anki-scoping\n\nfeat(profiles): per-profile dictionaries + Anki mining config (#254 p4)",
          "timestamp": "2026-08-09T15:46:10+03:00",
          "tree_id": "4eea4d67376ab13b85fe4cb14574dad402f2a541",
          "url": "https://github.com/serjflint/saitenka/commit/8e29b85680aea0045fe4437a1f9307cef78a530a"
        },
        "date": 1786279593607,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.353,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.059,
            "range": "±3.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f213e9de7f01b0dc08597c3972a53c1474cec16e",
          "message": "Merge pull request #275 from serjflint/chore/gitignore-claude-md\n\nchore: untrack CLAUDE.md (keep repo agent-agnostic; local git-ignored shim)",
          "timestamp": "2026-08-09T16:10:46+03:00",
          "tree_id": "fe7e1c70720876d8b4c918c34f296ddfbedeffc8",
          "url": "https://github.com/serjflint/saitenka/commit/f213e9de7f01b0dc08597c3972a53c1474cec16e"
        },
        "date": 1786281071350,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.986,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.373,
            "range": "±3.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "6493dc562911887da2119d4cd63c38732ad1b274",
          "message": "Merge pull request #276 from serjflint/chore/promote-memory-to-rules\n\nchore(agents): promote repo-specific durable constraints to .agents/rules + AGENTS.md",
          "timestamp": "2026-08-09T16:34:25+03:00",
          "tree_id": "a8663cfc22786c87413b1f6fa114bdf871f1990a",
          "url": "https://github.com/serjflint/saitenka/commit/6493dc562911887da2119d4cd63c38732ad1b274"
        },
        "date": 1786282489580,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.881,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.846,
            "range": "±0.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "32208e97304769bd05a9175f0350319308924625",
          "message": "Merge pull request #277 from serjflint/chore/scrub-private-refs\n\nchore(docs): remove references to an external private location; ground guidance in public sources",
          "timestamp": "2026-08-09T16:41:14+03:00",
          "tree_id": "95783bb866703506c4bd089420a609f622179a53",
          "url": "https://github.com/serjflint/saitenka/commit/32208e97304769bd05a9175f0350319308924625"
        },
        "date": 1786282896942,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.353,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.947,
            "range": "±2.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "7150e5cc25a98379fae6ffe8d3e096f60f99e2d3",
          "message": "Merge pull request #278 from serjflint/chore/bench-ci-tooling-rot\n\nchore(tooling): fix bench/CI rot + expand type-check scope to examples/tools/install/.agents",
          "timestamp": "2026-08-09T16:54:32+03:00",
          "tree_id": "19d54a80154c00060eb08014358291fe57595fba",
          "url": "https://github.com/serjflint/saitenka/commit/7150e5cc25a98379fae6ffe8d3e096f60f99e2d3"
        },
        "date": 1786283722387,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.285,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.751,
            "range": "±1.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ef4693b931857d236878233e574a7baa6bcddb6e",
          "message": "Merge pull request #279 from serjflint/chore/release-2.3.0\n\nchore(overlay): release 2.3.0",
          "timestamp": "2026-08-09T17:16:52+03:00",
          "tree_id": "07e93d4f42aa7edb32be7ded9eb126fa1a5e63b4",
          "url": "https://github.com/serjflint/saitenka/commit/ef4693b931857d236878233e574a7baa6bcddb6e"
        },
        "date": 1786285061470,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.262,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.835,
            "range": "±1.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d63a0428092c4e37700fbee088280958c5ef221c",
          "message": "Merge pull request #280 from serjflint/test/install-bootstrap-smoke\n\ntest: cross-OS install-bootstrap smoke (e2e install-smoke leg)",
          "timestamp": "2026-08-09T19:13:22+03:00",
          "tree_id": "88d6172f16e31cf224baac7096bff7a61723106b",
          "url": "https://github.com/serjflint/saitenka/commit/d63a0428092c4e37700fbee088280958c5ef221c"
        },
        "date": 1786292048616,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.298,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.644,
            "range": "±0.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5038058866a0e19ab74b4483452566bc2c6d33bf",
          "message": "Merge pull request #281 from serjflint/ci/taffylite-drop-intel-macos\n\nfix(taffylite): make the PyPI release workflow publishable",
          "timestamp": "2026-08-09T20:37:51+03:00",
          "tree_id": "718be8a536788f2bcb2ca8cd8b50ec9036034c9d",
          "url": "https://github.com/serjflint/saitenka/commit/5038058866a0e19ab74b4483452566bc2c6d33bf"
        },
        "date": 1786297101012,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.264,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.869,
            "range": "±10.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3222e67e1fcf96405b033e6cfaec390134b32dd4",
          "message": "Merge pull request #284 from serjflint/feat/two-engine-phase-a\n\nTwo-engine layout: harden the differential, extend the taffy seam to 2-D",
          "timestamp": "2026-08-09T23:39:58+03:00",
          "tree_id": "ecf2be0ddb99a49f3533bb1c16aec60a9c2a4f0d",
          "url": "https://github.com/serjflint/saitenka/commit/3222e67e1fcf96405b033e6cfaec390134b32dd4"
        },
        "date": 1786308028310,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.285,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.823,
            "range": "±6.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e86d1525b0886ccf2c1a7e88feebd83e8819c911",
          "message": "Merge pull request #286 from serjflint/feat/french-profile-engine\n\nfeat: second-language (French) profile engine",
          "timestamp": "2026-08-10T01:41:41+03:00",
          "tree_id": "c786e360ae9e1de3c988cabe4bd094ab4e447289",
          "url": "https://github.com/serjflint/saitenka/commit/e86d1525b0886ccf2c1a7e88feebd83e8819c911"
        },
        "date": 1786315352786,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.304,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.616,
            "range": "±1.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "bc7a11cbae0cecaa4830c299f051948164c4cceb",
          "message": "Merge pull request #287 from serjflint/fix/deinflect-lockfile\n\nfix(deinflect): relock uv.lock for the 0.2.0 bump",
          "timestamp": "2026-08-10T01:51:25+03:00",
          "tree_id": "9aa8575453cb3c14053a6b0418517b8228b3c6a2",
          "url": "https://github.com/serjflint/saitenka/commit/bc7a11cbae0cecaa4830c299f051948164c4cceb"
        },
        "date": 1786315909372,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.282,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.653,
            "range": "±1.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1171d922b7f4fa465dfe5717961486849b97cdc5",
          "message": "Merge pull request #289 from serjflint/release/2.4.0\n\nchore(overlay): release 2.4.0",
          "timestamp": "2026-08-10T02:09:37+03:00",
          "tree_id": "38a22b14cc5c6d51314b721f5d8258b0583100e4",
          "url": "https://github.com/serjflint/saitenka/commit/1171d922b7f4fa465dfe5717961486849b97cdc5"
        },
        "date": 1786317027109,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.295,
            "range": "±1.0%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.874,
            "range": "±18.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1fca115e107195d7253ecef4130360077ba08f32",
          "message": "Merge pull request #290 from serjflint/test/pipeline-oracle\n\ntest(pipeline): (cue, hover position) → Entry oracle on the real build assembly",
          "timestamp": "2026-08-10T10:34:56+03:00",
          "tree_id": "797f2194b9d8406d3c06b44a2f84c443e6a538b4",
          "url": "https://github.com/serjflint/saitenka/commit/1fca115e107195d7253ecef4130360077ba08f32"
        },
        "date": 1786347323623,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.367,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.073,
            "range": "±16.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d63f89fb5d0057963cd2db59d2456df51bcf5dcb",
          "message": "Merge pull request #291 from serjflint/test/transform-differential\n\ntest(conformance): French transform differential + external-oracle catalog + drift guard",
          "timestamp": "2026-08-10T10:35:39+03:00",
          "tree_id": "7ebe833a39484f0b8605ae25ee1729b50ddf1b33",
          "url": "https://github.com/serjflint/saitenka/commit/d63f89fb5d0057963cd2db59d2456df51bcf5dcb"
        },
        "date": 1786347381838,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.807,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.733,
            "range": "±1.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e93f3ba314d63af1beb7936fb797f8bb0e65ef72",
          "message": "Merge pull request #292 from serjflint/feat/test-kinds-advisory\n\nfeat(hooks): commit-time test-kinds advisory + \"test-kind is a decision\" discipline",
          "timestamp": "2026-08-10T11:19:44+03:00",
          "tree_id": "7289373284cec822ee816462c0dbaee658d239cb",
          "url": "https://github.com/serjflint/saitenka/commit/e93f3ba314d63af1beb7936fb797f8bb0e65ef72"
        },
        "date": 1786350026950,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.893,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.93,
            "range": "±2.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "20be0a078bdc62449cef95acff85b1aa9d96cdbe",
          "message": "Merge pull request #293 from serjflint/perf/render-cache-offthread-reads\n\nperf(render-cache): move cache IO off the main thread + defer cold-miss render",
          "timestamp": "2026-08-10T14:00:57+03:00",
          "tree_id": "66e4277bc859bc80470fffa9f4399bcf6ba5609d",
          "url": "https://github.com/serjflint/saitenka/commit/20be0a078bdc62449cef95acff85b1aa9d96cdbe"
        },
        "date": 1786359709413,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.227,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.628,
            "range": "±6.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "6d88751fe754a8a5461c4fee3d4d6a7775464433",
          "message": "Merge pull request #294 from serjflint/perf/nested-offthread-render\n\nperf: nested popup off-thread + bench/click telemetry (follow-up to #293)",
          "timestamp": "2026-08-10T15:35:33+03:00",
          "tree_id": "d98c566cceae4256ef307eba9f4913c7e1533d44",
          "url": "https://github.com/serjflint/saitenka/commit/6d88751fe754a8a5461c4fee3d4d6a7775464433"
        },
        "date": 1786365364191,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.28,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.783,
            "range": "±2.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "107981f865e6659f7b90df610e725ccea7ac9b33",
          "message": "Merge pull request #295 from serjflint/perf/clicks-bench\n\nfeat(bench): --clicks mode for the sidebar/bookmark/mine click surfaces",
          "timestamp": "2026-08-10T15:52:15+03:00",
          "tree_id": "0b942f000f2294e2674a5b9883d36b4994e75d03",
          "url": "https://github.com/serjflint/saitenka/commit/107981f865e6659f7b90df610e725ccea7ac9b33"
        },
        "date": 1786366365250,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.513,
            "range": "±0.6%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.224,
            "range": "±1.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "afa83fd92639e1b34455856d3984d815b1e9b4bb",
          "message": "Merge pull request #296 from serjflint/perf/clicked-nav-offthread\n\nperf(tooltip): defer clicked cross-ref navigation off the main thread",
          "timestamp": "2026-08-10T16:22:47+03:00",
          "tree_id": "5ca327bbb0c77ac4329ed5041c5f78335c19a001",
          "url": "https://github.com/serjflint/saitenka/commit/afa83fd92639e1b34455856d3984d815b1e9b4bb"
        },
        "date": 1786368217209,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.273,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.592,
            "range": "±1.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "9ef3461c5f112d9ad6664969dfdb60a0ed151a19",
          "message": "Merge pull request #299 from serjflint/perf/unify-popup-view\n\nrefactor(tooltip): unify base+nested popup view; per-view crisp flags; nested render-ahead",
          "timestamp": "2026-08-10T18:51:40+03:00",
          "tree_id": "39d9cfcd784a17fc8add77c6d079a2b7173fcaaf",
          "url": "https://github.com/serjflint/saitenka/commit/9ef3461c5f112d9ad6664969dfdb60a0ed151a19"
        },
        "date": 1786377136451,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.293,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.762,
            "range": "±7.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8f71b4bd0d0fbf19651519a5bf1de4b20c89170b",
          "message": "Merge pull request #300 from serjflint/feat/headword-kanji-links\n\nfeat(tooltip): make each headword kanji a click-to-open link (Yomitan parity)",
          "timestamp": "2026-08-10T19:01:15+03:00",
          "tree_id": "c37adb88ef5c6f5b6546ffa7cbd85f0e7f8cfd94",
          "url": "https://github.com/serjflint/saitenka/commit/8f71b4bd0d0fbf19651519a5bf1de4b20c89170b"
        },
        "date": 1786377706279,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.935,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.455,
            "range": "±8.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "9f327f78597baeb5c6da82d86cdd0a2aa07c43f1",
          "message": "Merge pull request #301 from serjflint/perf/offthread-nested-opens\n\nperf(tooltip): defer clicked/keyed nested opens off the main thread (tier-3)",
          "timestamp": "2026-08-10T19:37:30+03:00",
          "tree_id": "bf279bf81d5fcbf9b36d9d0190e5290bdd2cf088",
          "url": "https://github.com/serjflint/saitenka/commit/9f327f78597baeb5c6da82d86cdd0a2aa07c43f1"
        },
        "date": 1786379899945,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.387,
            "range": "±0.9%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.915,
            "range": "±4.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e49babd0a8d780a2ef75144179db1c5a4d2824c9",
          "message": "Merge pull request #282 from serjflint/chore/taffylite-use-pypi\n\nchore(taffylite): resolve layout-engine extra from PyPI",
          "timestamp": "2026-08-10T19:44:23+03:00",
          "tree_id": "b727e92287f6eb86d9a9cd73497da34f52442d44",
          "url": "https://github.com/serjflint/saitenka/commit/e49babd0a8d780a2ef75144179db1c5a4d2824c9"
        },
        "date": 1786380320090,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.358,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.242,
            "range": "±2.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "788e7b61f9aef9a0b24c57e5bbd3b5f2c52c393d",
          "message": "Merge pull request #302 from serjflint/perf/bench-engaged-open\n\nperf(bench): pump deferred nav/open + nested scroll in --timeline; report engaged_open",
          "timestamp": "2026-08-10T19:45:45+03:00",
          "tree_id": "a6f9851d2cceb9ced549aecea67599f7575472e8",
          "url": "https://github.com/serjflint/saitenka/commit/788e7b61f9aef9a0b24c57e5bbd3b5f2c52c393d"
        },
        "date": 1786380375570,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.471,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.525,
            "range": "±6.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4e4be783aab1cff2e157d33c10862ce186e1297b",
          "message": "feat(sc): render sub/sup as small raised/lowered annotations, honor line-through (#303)\n\nSuperscript reading annotations (新明解 91%, 大辞林 76%, 三省堂 43% of entries) were\nrendering full-size inline. Carry a signed baseline-shift on the shaped token, shrink\nsub/sup to ~0.72em, and grow the line box by the shift so the raised/lowered glyph is\nnever clipped. Trigger on the sub/sup tag OR style.verticalAlign. Fold in\ntextDecorationLine: line-through on the same styling seam.",
          "timestamp": "2026-08-10T21:46:37+03:00",
          "tree_id": "44528ce26851c123034ac8d46f7b1b62caa239ef",
          "url": "https://github.com/serjflint/saitenka/commit/4e4be783aab1cff2e157d33c10862ce186e1297b"
        },
        "date": 1786387626507,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.428,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.454,
            "range": "±13.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "cc1b460f14d886282cba63a594aca1f53ad0ce86",
          "message": "perf(tooltip): warm raw 1× bands ahead on a hi-dpi flick (#304)\n\nOn a hi-dpi display the scroll-ahead worker warmed only NATIVE bands; a flick that\noutran them left the soft-first blit to raster the RAW 1× bands it reads\n(warm_only=False) synchronously on the scroll tick — invisible on top-tier hardware,\nreal jank on slower boxes. Warm the raw bands ahead too when scale>1 (at scale==1 the\nnative path already IS the raw path, so no redundant second warm).",
          "timestamp": "2026-08-10T21:47:44+03:00",
          "tree_id": "91fe3178e127a8df237428014765310519920219",
          "url": "https://github.com/serjflint/saitenka/commit/cc1b460f14d886282cba63a594aca1f53ad0ce86"
        },
        "date": 1786387700488,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.344,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.482,
            "range": "±10.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1ee8cb5516ea481cac5f17bf4a60c19864709b9d",
          "message": "feat(pitch): render devoiced (○) / nasal (゜) mora markers from NHK/Kanjium data (#305)\n\nIngest dropped every per-mora annotation, keeping only downstep positions, so the\ngraph could never show devoiced/nasalised morae Yomitan draws. Carry devoice/nasal\nthrough import → query → render as a PitchAccent(position, devoice, nasal): a devoiced\nmora's dot is drawn hollow (○), a nasalised mora gains a ゜ ring above it. Grounded —\nstraight from the pitch dictionary, per the \"readings/pitch from dictionaries\" rule.\n\nA plain accent dict (no annotations) stores the bare [int] list it always did and its\ngraph is byte-identical (no golden re-bless); only NHK/Kanjium data with the markers\ngrows the payload and the graph.",
          "timestamp": "2026-08-10T21:48:42+03:00",
          "tree_id": "d4f45a07c5c0b72fd0e62c49180b4c383aea910b",
          "url": "https://github.com/serjflint/saitenka/commit/1ee8cb5516ea481cac5f17bf4a60c19864709b9d"
        },
        "date": 1786387760824,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.352,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 10.221,
            "range": "±14.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a77017f43d70c83b88cae4c65b8607218ecd05b9",
          "message": "feat(taffylite): pyo3 0.29 abi3+abi3t wheel matrix (PEP 803), bump 0.2.0 (#307)\n\n* feat(taffylite): pyo3 0.29 abi3+abi3t wheels (PEP 803), bump 0.2.0\n\nOne Cargo config, three wheels per platform via three build interpreters:\nabi3 (GIL 3.13+), version-specific cp314t (free-threaded 3.14), abi3t\n(FT+GIL 3.15+). Replaces the per-version cp313/cp314/cp314t set — the abi3\nwheel now covers every GIL build, so 3.15+ users get a forward-compatible\nwheel instead of building from sdist. All three verified loading and\ncomputing on 3.13/3.14/3.14t/3.15t locally; taffylite's 16 tests pass on\nboth the abi3 and cp314t wheels. overlay's >=0.1.0 floor already accepts\n0.2.0 — a relock after publish adopts the abi3t wheels.\n\n* ci(taffylite): 3-build abi3t release matrix, de-scar the header\n\nCollapse the per-version wheel matrix to the three abi3/abi3t builds; the\n3.15t leg is non-fatal until the interpreter is installable. Trim the\nheader essay to what the diff can't show: the one-interpreter-per-job rule\n(unique filenames dodge the merge-concat / CVE-2025-54368 strict-ZIP 400).",
          "timestamp": "2026-08-10T21:50:14+03:00",
          "tree_id": "ec008b5cb07b6228e3ea3e3000c1141e132eff4b",
          "url": "https://github.com/serjflint/saitenka/commit/a77017f43d70c83b88cae4c65b8607218ecd05b9"
        },
        "date": 1786387864766,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.18,
            "range": "±1.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.615,
            "range": "±3.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f612dde808b1504c84fb2c5c5e8adcaa8acb1efb",
          "message": "feat(resvglite): pure-Rust resvg PyO3 binding for inline SVG glyphs (part 1 of #283) (#306)\n\n* feat(resvglite): vendor a pure-Rust resvg PyO3 binding for inline SVG glyphs\n\nNew optional maturin crate (mirrors taffylite) that rasterizes SVG → PNG at a base\nresolution — the self-contained, system-library-free rasterizer #283 needs so Yomitan\ninline gaiji (参照 / 表記 / 一, 91% of 三省堂 entries) can render instead of ▢. pyo3\nwithout abi3 → real free-threaded cp314t wheels (gil_used=false); resvg is pure Rust\n(usvg + tiny-skia), no system deps.\n\nShips the crate + smoke tests, a CI wheel-build job (Linux 3.14t), and a\nresvglite-release.yml wheel matrix (cp313/314/314t × Linux/macOS-arm64/Windows) with\nTrusted Publishing under its own resvglite-v* tag. resvg is MPL-2.0 (file-level\ncopyleft): contained to this separately-published optional wheel — the core saitenka\ngraph stays permissive. DB media table + import extraction + render compositing land in\na follow-up PR.\n\n* feat(resvglite): pyo3 0.29 abi3+abi3t wheels (PEP 803)\n\nSame three-wheels-per-platform matrix as taffylite: abi3 (GIL 3.13+),\ncp314t (free-threaded 3.14), abi3t (FT+GIL 3.15+). Crate API unchanged; the\n5 smoke tests pass on both the abi3 and cp314t wheels, and the abi3t wheel\nrasterizes on 3.15t free-threaded. Release YAML gets the 3-build matrix and\nloses the header essay (kept: one-interpreter-per-job → CVE-2025-54368).",
          "timestamp": "2026-08-10T21:52:17+03:00",
          "tree_id": "44ca98971bfaccbc4e8c25d33d414f8501625af0",
          "url": "https://github.com/serjflint/saitenka/commit/f612dde808b1504c84fb2c5c5e8adcaa8acb1efb"
        },
        "date": 1786387967833,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.374,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.933,
            "range": "±3.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b54d29c6b33ab3aabdee89498c0ad863b65c31d4",
          "message": "feat(images): render inline SVG gaiji via resvglite, ▢ fallback (closes #283) (#308)\n\nYomitan structured-content img nodes (SVG gaiji: 参照/表記 labels, reference\nglyphs) drew as a ▢ box because Pillow can't rasterize SVG. The optional\nresvglite extra now rasterizes them once at import into a new additive\nmedia(dict_id, path, png) table; the renderer composites the real glyph.\n\nThreading keeps SQLite off the render thread: media is preloaded onto each\nDefinition at Entry-build (lookup thread) via DictionaryDb.media_for, carried\nthrough BodyRenderArgs (raw bytes, picklable for the process pool) into walk.\nMonochrome gaiji are tinted to the text colour; appearance:\"auto\" keeps the\nSVG's own colours. All PIL decode/tint lives in render.flow.img_box, so sc.walk\nstays PIL-agnostic (the sc/-layering contract).\n\nFully additive: without the extra, _load_media no-ops, the table stays empty,\nand every img falls back to ▢ — the default install is byte-identical. Import\nis gated to app.dictdb (TID251 ban + import-linter svg-images-chokepoint).",
          "timestamp": "2026-08-10T22:39:57+03:00",
          "tree_id": "cb5488f1dea288eceff068db96cef3d6822802bf",
          "url": "https://github.com/serjflint/saitenka/commit/b54d29c6b33ab3aabdee89498c0ad863b65c31d4"
        },
        "date": 1786390849129,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.948,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.842,
            "range": "±0.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "759fb38f1f6d0734ed0d80a497555db11c6bf0be",
          "message": "Merge pull request #309 from serjflint/fix/import-nested-profiles\n\nfix(import): serialise nested [profiles.*] tables; relock crates from PyPI",
          "timestamp": "2026-08-10T23:26:57+03:00",
          "tree_id": "8eea2ab411666655a98ac25a1a74154d76b1d81b",
          "url": "https://github.com/serjflint/saitenka/commit/759fb38f1f6d0734ed0d80a497555db11c6bf0be"
        },
        "date": 1786393703326,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.36,
            "range": "±5.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.537,
            "range": "±9.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "55aec5e535f5b5702288c9e56dbc654e5dab7e96",
          "message": "Merge pull request #310 from serjflint/fix/gaiji-tofu-kanji-nav\n\nfix(images): render <text> gaiji glyphs and preload media in stacked entries",
          "timestamp": "2026-08-11T03:56:39+03:00",
          "tree_id": "31f4b84d42d2b4dd809ac5689a2e0c41b14f6288",
          "url": "https://github.com/serjflint/saitenka/commit/55aec5e535f5b5702288c9e56dbc654e5dab7e96"
        },
        "date": 1786409823108,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.382,
            "range": "±0.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.373,
            "range": "±15.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8677805805aded25eb57ff25ecedac82bc76684a",
          "message": "Merge pull request #311 from serjflint/feat/kanji-panel-sectioned-stats\n\nfeat(kanji): label + section the kanji panel's stats like Yomitan",
          "timestamp": "2026-08-11T11:01:23+03:00",
          "tree_id": "f08cd7da7da02653ab4b6263e3d49d0dc0552a9c",
          "url": "https://github.com/serjflint/saitenka/commit/8677805805aded25eb57ff25ecedac82bc76684a"
        },
        "date": 1786435307798,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.323,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.836,
            "range": "±5.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ed6799aa853ef7ae725a815132fa8b51015443fe",
          "message": "Merge pull request #313 from serjflint/feat/kanji-stroke-order-font\n\nfeat(kanji): stroke-order font for the panel headword",
          "timestamp": "2026-08-11T11:27:19+03:00",
          "tree_id": "c416599ccde299868258669d7c9e16723870ce28",
          "url": "https://github.com/serjflint/saitenka/commit/ed6799aa853ef7ae725a815132fa8b51015443fe"
        },
        "date": 1786436863790,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.428,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.121,
            "range": "±14.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "458cff989ca77abc254b0e6e6f79b90c2a64bfb2",
          "message": "Merge pull request #314 from serjflint/release/3.0.0\n\nchore(overlay): release 3.0.0",
          "timestamp": "2026-08-11T11:38:12+03:00",
          "tree_id": "4d5b841334b058eb4131cda9ac44bab317d410f3",
          "url": "https://github.com/serjflint/saitenka/commit/458cff989ca77abc254b0e6e6f79b90c2a64bfb2"
        },
        "date": 1786437547841,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.545,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.222,
            "range": "±2.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5122ff6ac80352c29cc278928c50692e90926de4",
          "message": "Merge pull request #315 from serjflint/fix/pypi-publish-metadata-2.5\n\nci(release): bump gh-action-pypi-publish to v1.14.2 (metadata 2.5 upload fix)",
          "timestamp": "2026-08-11T11:54:41+03:00",
          "tree_id": "42bedf2d4909a3198cfd5e6733d8262af76e3302",
          "url": "https://github.com/serjflint/saitenka/commit/5122ff6ac80352c29cc278928c50692e90926de4"
        },
        "date": 1786438510090,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.373,
            "range": "±0.1%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.462,
            "range": "±7.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e07dbfa0b9e1e148aa7eab62edce771f764d4395",
          "message": "Merge pull request #316 from serjflint/fix/pypi-publish-correct-sha\n\nci(release): pin gh-action-pypi-publish v1.14.2 by commit SHA (fix manifest unknown)",
          "timestamp": "2026-08-11T12:09:33+03:00",
          "tree_id": "70670e99f855d7900f8ef1f5e612996fc41cb349",
          "url": "https://github.com/serjflint/saitenka/commit/e07dbfa0b9e1e148aa7eab62edce771f764d4395"
        },
        "date": 1786439405202,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.342,
            "range": "±0.9%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.708,
            "range": "±1.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "35c4886b8adeaa68d3f3f73ac914d1f714a7ffd5",
          "message": "Merge pull request #317 from serjflint/ci/node24-action-bumps\n\nci: bump GitHub Actions off deprecated Node.js 20",
          "timestamp": "2026-08-11T13:04:22+03:00",
          "tree_id": "8b13f18cc696d06b221406de97e19446f65be9d0",
          "url": "https://github.com/serjflint/saitenka/commit/35c4886b8adeaa68d3f3f73ac914d1f714a7ffd5"
        },
        "date": 1786442770307,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.377,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.784,
            "range": "±1.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "6ecae9b5c33c384d5c382ec744570157aeae026e",
          "message": "Merge pull request #318 from serjflint/fix/session-wip-2026-08-11\n\nfix: land session WIP — import merge-append, shutdown watchdog, type config, span marks",
          "timestamp": "2026-08-11T13:21:19+03:00",
          "tree_id": "667801d5f8c766344d9a36af1fe50316bb23d6b1",
          "url": "https://github.com/serjflint/saitenka/commit/6ecae9b5c33c384d5c382ec744570157aeae026e"
        },
        "date": 1786443730342,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.471,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.966,
            "range": "±3.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3fc14d886dac8397c0c3b8d5ea0ce2e9ad9b0c29",
          "message": "Merge pull request #319 from serjflint/docs/actualize-since-1.0\n\ndocs: actualize the docs against the code (post-1.0 features + drift fixes)",
          "timestamp": "2026-08-11T14:02:22+03:00",
          "tree_id": "061fe8bbb9d58a25d5b353c7b8a56b8da4cfe2ef",
          "url": "https://github.com/serjflint/saitenka/commit/3fc14d886dac8397c0c3b8d5ea0ce2e9ad9b0c29"
        },
        "date": 1786446170120,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.453,
            "range": "±0.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.418,
            "range": "±13.1%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d4ac79624432cc0514f54584d36bcd80f7e1e4d7",
          "message": "Merge pull request #320 from serjflint/feat/runtime-profile-cycle-subtitle-track\n\nfix: full runtime profile cycle (subtitle track) + always-centered native subs + configurable sub-bg opacity",
          "timestamp": "2026-08-11T15:00:41+03:00",
          "tree_id": "263217c4c6635d1e5a7bbca6d21b3ce1155d8117",
          "url": "https://github.com/serjflint/saitenka/commit/d4ac79624432cc0514f54584d36bcd80f7e1e4d7"
        },
        "date": 1786449672908,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.544,
            "range": "±1.9%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.769,
            "range": "±15.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8047cf0ebac27a6a4afb33d619613ca996cd0512",
          "message": "Merge pull request #321 from serjflint/release/3.1.0\n\nchore(overlay): release 3.1.0",
          "timestamp": "2026-08-11T15:16:10+03:00",
          "tree_id": "d763e81fa334fb573a307756b6be283f96953167",
          "url": "https://github.com/serjflint/saitenka/commit/8047cf0ebac27a6a4afb33d619613ca996cd0512"
        },
        "date": 1786450621419,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.597,
            "range": "±0.4%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.311,
            "range": "±1.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "725a6c4bc41c9f3f76284d92a1928cc3e8ff8e64",
          "message": "Merge pull request #322 from serjflint/chore/setup-uv-node24\n\nci: bump setup-uv to Node 24 (v8.3.2) — last Node 20 action",
          "timestamp": "2026-08-11T15:32:02+03:00",
          "tree_id": "03eeb23e9bf48fa8b2b3131ddd832efa788d4203",
          "url": "https://github.com/serjflint/saitenka/commit/725a6c4bc41c9f3f76284d92a1928cc3e8ff8e64"
        },
        "date": 1786451579716,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.532,
            "range": "±1.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.327,
            "range": "±2.7%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3045772bbeead653860ee80308ce26a8eadda3c5",
          "message": "Merge pull request #323 from serjflint/fix/grow-sharpen-dogfood\n\nfix: harden Grow and Sharpen dogfood loops",
          "timestamp": "2026-08-11T22:34:35+03:00",
          "tree_id": "78ece1b6d5c65cf013a7d5df8853a5b3ecd0939c",
          "url": "https://github.com/serjflint/saitenka/commit/3045772bbeead653860ee80308ce26a8eadda3c5"
        },
        "date": 1786476927800,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.494,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.145,
            "range": "±7.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f575aa66be51a0167168c2cabed81ecf1653b5c7",
          "message": "Merge pull request #324 from serjflint/chore/loop-skill-routing\n\nchore(agents): route skills through test loops",
          "timestamp": "2026-08-11T23:17:18+03:00",
          "tree_id": "8a56f1e2232469365e775020bc9397badfd8d287",
          "url": "https://github.com/serjflint/saitenka/commit/f575aa66be51a0167168c2cabed81ecf1653b5c7"
        },
        "date": 1786479476448,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.325,
            "range": "±0.6%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.389,
            "range": "±9.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d50d9b8c4d9f415c953ff125a0ee0b99dea87c08",
          "message": "Merge pull request #325 from serjflint/chore/root-level-repo-wide-tasks\n\nchore(tasks): define repo-wide tasks at the repo root",
          "timestamp": "2026-08-12T01:27:01+03:00",
          "tree_id": "193370c59cac894b7805b6ff70334af746883ffb",
          "url": "https://github.com/serjflint/saitenka/commit/d50d9b8c4d9f415c953ff125a0ee0b99dea87c08"
        },
        "date": 1786487272128,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.482,
            "range": "±0.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.161,
            "range": "±0.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f99cd019e7c6f9053acea118d81cdd66b0042975",
          "message": "fix: harden grow and sharpen audit tooling (#326)",
          "timestamp": "2026-08-12T02:33:35+03:00",
          "tree_id": "8fdbcf6889da368f43cdaa5ee78c081b66581895",
          "url": "https://github.com/serjflint/saitenka/commit/f99cd019e7c6f9053acea118d81cdd66b0042975"
        },
        "date": 1786491252559,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.368,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.829,
            "range": "±4.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0e2215c378ec657d35739659492dd157b54463d9",
          "message": "refactor: extract saitenka-dict dictionary engine (#327)\n\n* feat(anki): extract AnkiConnect client\n\n* feat(yomitanlite): add dictionary core and oracle\n\n* refactor(overlay): add swappable lookup sources\n\n* build(release): publish extracted packages independently\n\n* docs: document extracted runtime boundaries\n\n* fix(yomitanlite): preserve configured source priority\n\n* chore: normalize extracted package licenses\n\n* build: mirror extracted package tasks at repo root\n\n* docs: clarify extracted package release ordering\n\n* ci: split extracted package releases\n\n* refactor(dictionary): rename package and isolate oracle\n\n* fix(extraction): preserve runtime contracts",
          "timestamp": "2026-08-12T08:17:57+03:00",
          "tree_id": "27b1aa24c55abefee9fd02a5ca6a81df780c14eb",
          "url": "https://github.com/serjflint/saitenka/commit/0e2215c378ec657d35739659492dd157b54463d9"
        },
        "date": 1786511929212,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.627,
            "range": "±0.9%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.572,
            "range": "±2.6%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "e15bb3a1a49f0ae4983e7fd77af698719f823375",
          "message": "fix: preserve nested dictionary structure (#328)",
          "timestamp": "2026-08-12T20:36:12+03:00",
          "tree_id": "23d345e8f0a93b47899e79e9904a444f23076054",
          "url": "https://github.com/serjflint/saitenka/commit/e15bb3a1a49f0ae4983e7fd77af698719f823375"
        },
        "date": 1786556210075,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.656,
            "range": "±0.8%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 10.043,
            "range": "±1.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4cecefac4c5020bb85e154563199c3c3f753a103",
          "message": "refactor: establish modular Saitenka package boundaries (#329)\n\n* refactor: consolidate the saitenka source layout\n\n* refactor: remove legacy overlay project directory",
          "timestamp": "2026-08-12T21:48:40+03:00",
          "tree_id": "6224c751a75c64a4e9514556b7c86f065b949edf",
          "url": "https://github.com/serjflint/saitenka/commit/4cecefac4c5020bb85e154563199c3c3f753a103"
        },
        "date": 1786560569595,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.436,
            "range": "±0.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.787,
            "range": "±0.8%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "213f0dffab19615787018b7f6398ab69b2dc7c1c",
          "message": "fix: preserve source-backed dictionary structure (#330)\n\n* fix: unwrap semantic dictionary glossaries\n\n* test: add dictionary structure oracle",
          "timestamp": "2026-08-12T22:05:18+03:00",
          "tree_id": "95b4d61cfcfca00b6c3929f6e8b0a434654abd18",
          "url": "https://github.com/serjflint/saitenka/commit/213f0dffab19615787018b7f6398ab69b2dc7c1c"
        },
        "date": 1786561626378,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.524,
            "range": "±0.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.127,
            "range": "±7.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "97dd93239fa55b6d4fbad8b6213412f5b6045911",
          "message": "docs: document dictionary structure oracle (#331)",
          "timestamp": "2026-08-12T22:37:42+03:00",
          "tree_id": "613c58b24f282bb517917339440c027f193e182e",
          "url": "https://github.com/serjflint/saitenka/commit/97dd93239fa55b6d4fbad8b6213412f5b6045911"
        },
        "date": 1786563506998,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.97,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.766,
            "range": "±11.9%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "6c0052f37f373ccdfc5d8eab70573c4d24acf581",
          "message": "refactor: modularize reader runtime and CLI (#332)\n\n* refactor(render): remove remaining layer backedges\n\n* refactor(reader): introduce explicit runtime seams\n\n* refactor(cli): split command domains\n\n* refactor(launch): isolate run orchestration\n\n* refactor(reader): centralize production composition\n\n* chore(launch): remove obsolete registry import\n\n* docs(launch): update provider registration boundary\n\n* refactor(cli): narrow command module imports\n\n* docs(render): update structured-content test reference\n\n* fix(launch): register subtitle providers explicitly\n\n* docs(architecture): align advisory and render boundaries\n\n* docs(render): fix worker-boundary wording",
          "timestamp": "2026-08-13T08:15:18+03:00",
          "tree_id": "f269c9b94cebb296926de344a12720e8a5806c72",
          "url": "https://github.com/serjflint/saitenka/commit/6c0052f37f373ccdfc5d8eab70573c4d24acf581"
        },
        "date": 1786598167601,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.772,
            "range": "±0.7%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.485,
            "range": "±1.3%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "41f3be4152d9462578962dc8f228a394de404693",
          "message": "docs: compare Saitenka with Migaku (#333)\n\n* docs: compare Saitenka with Migaku\n\n* docs: correct Migaku source comparison\n\n* docs: reflect implemented Saitenka features",
          "timestamp": "2026-08-13T08:55:23+03:00",
          "tree_id": "be668bf03adc7619c9d185cd5d34ace323e5031a",
          "url": "https://github.com/serjflint/saitenka/commit/41f3be4152d9462578962dc8f228a394de404693"
        },
        "date": 1786600571016,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.401,
            "range": "±0.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.952,
            "range": "±9.4%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6",
          "message": "refactor: organize panel subsystem package (#334)",
          "timestamp": "2026-08-13T08:57:24+03:00",
          "tree_id": "2763328ef8332c20d511fc6de372e9f548ec88d0",
          "url": "https://github.com/serjflint/saitenka/commit/39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6"
        },
        "date": 1786600699584,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.163,
            "range": "±1.5%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.646,
            "range": "±9.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5f1d3adab0a48ae9600b7232e847e701fb810c4a",
          "message": "docs: actualize runtime architecture (#340)",
          "timestamp": "2026-08-13T09:54:15+03:00",
          "tree_id": "37a89d0c2e7f07f324fa44e1eb86231a237a9d60",
          "url": "https://github.com/serjflint/saitenka/commit/5f1d3adab0a48ae9600b7232e847e701fb810c4a"
        },
        "date": 1786604105717,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.324,
            "range": "±1.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.824,
            "range": "±3.5%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4bbe1c5b10f1b9a62f02a91b5c298ad627ed9897",
          "message": "docs: compare adjacent mobile immersion tools (#341)",
          "timestamp": "2026-08-13T10:12:08+03:00",
          "tree_id": "65929d2348376b01aa20982bc9e3f7992f4f341d",
          "url": "https://github.com/serjflint/saitenka/commit/4bbe1c5b10f1b9a62f02a91b5c298ad627ed9897"
        },
        "date": 1786605183741,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.22,
            "range": "±1.3%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.661,
            "range": "±4.0%",
            "unit": "ms"
          }
        ]
      },
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
          "id": "48ddfa4d584766b930643ca98a2c8c2094fe9723",
          "message": "chore(tools): make repowise runs observable and route them through guards (#342)\n\nA run whose requests are dropped server-side is indistinguishable from a\nhealthy one: the progress bar sits still, the GPU pins at 100%, and\nnothing below ERROR is logged. Three faults hid in that silence — a\nserver-side per-request timeout killing generation, the embedder's\nhardcoded 10s expiring every batch, and a killed job record that reports\n'running' forever.\n\nEvery command that spends GPU time now streams a line while it runs\n(settled/started calls, pages landing, phase), so a live run cannot be\nmistaken for a wedged one, and 'started but never settled' names the drop\nsignature directly. watch treats an untouched job record as stale rather\nthan live, and the runs export REPOWISE_EMBEDDING_TIMEOUT.\n\nAdds update (the everyday incremental path) and vectors (embedding-only\nrepair); reindex is now documented as what a mass rename requires, since\nupdate moves the file layer but never re-partitions the concept layer.",
          "timestamp": "2026-08-13T11:06:04+03:00",
          "tree_id": "84ea4d0230d422155ff1fc3e73b43524af9cb594",
          "url": "https://github.com/serjflint/saitenka/commit/48ddfa4d584766b930643ca98a2c8c2094fe9723"
        },
        "date": 1786608415407,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.245,
            "range": "±1.2%",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.702,
            "range": "±5.4%",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "id": "c58e0eed36bcbd801e3164cd8e3f5817982183f5",
          "message": "test(perf): pin benchmark publish safety",
          "timestamp": "2026-08-13T09:47:51Z",
          "url": "https://github.com/serjflint/saitenka/commit/c58e0eed36bcbd801e3164cd8e3f5817982183f5"
        },
        "date": 1786614882631,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.961357,
            "range": "3 replicas; min 5.0213; max 6.50581; MAD 0.544454",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.22137,
            "range": "3 replicas; min 7.30163; max 9.57536; MAD 0.919743; worst 9.57536",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 16.967584,
            "range": "3 replicas; min 14.3685; max 19.4574; MAD 2.48977",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.249136,
            "range": "3 replicas; min 14.6547; max 19.7554; MAD 2.50626; worst 19.7554",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 36.322486,
            "range": "3 replicas; min 24.1165; max 244.592; MAD 12.206",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.11402,
            "range": "3 replicas; min 0.094774; max 0.168855; MAD 0.019246; worst 0.168855",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 46.295202,
            "range": "3 replicas; min 43.439; max 79.879; MAD 2.85617; worst 79.879",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.269842,
            "range": "3 replicas; min 3.25; max 78.8154; MAD 0.019842; worst 78.8154",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 4.043439,
            "range": "3 replicas; min 1.88946; max 16.1663; MAD 2.15398; worst 16.1663",
            "unit": "ms"
          }
        ]
      },
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
          "id": "382df36e01d30fb90d39a96c5bba4bc4dacb7b0e",
          "message": "Merge pull request #343 from serjflint/perf/continuous-benchmark-portfolio\n\nfeat(perf): add continuous benchmark portfolio",
          "timestamp": "2026-08-13T13:11:56+03:00",
          "tree_id": "c2f71e7eb44c205e2a5137b3840250e844acc2ac",
          "url": "https://github.com/serjflint/saitenka/commit/382df36e01d30fb90d39a96c5bba4bc4dacb7b0e"
        },
        "date": 1786616022297,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.937939,
            "range": "3 replicas; min 4.93042; max 6.56662; MAD 0.007521",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.91935,
            "range": "3 replicas; min 6.88293; max 9.91443; MAD 0.036415; worst 9.91443",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 13.855715,
            "range": "3 replicas; min 13.8495; max 19.6482; MAD 0.006219",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 14.004039,
            "range": "3 replicas; min 13.9903; max 20.1682; MAD 0.013753; worst 20.1682",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.346746,
            "range": "3 replicas; min 17.0966; max 35.3544; MAD 3.25017",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.102494,
            "range": "3 replicas; min 0.100781; max 0.17135; MAD 0.001713; worst 0.17135",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 29.93498,
            "range": "3 replicas; min 29.806; max 46.6814; MAD 0.129021; worst 46.6814",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.769077,
            "range": "3 replicas; min 2.20948; max 33.6868; MAD 0.559596; worst 33.6868",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.160566,
            "range": "3 replicas; min 1.02806; max 2.15474; MAD 0.132509; worst 2.15474",
            "unit": "ms"
          }
        ]
      },
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
          "id": "948872c35e736621c514b9840619226c569087f7",
          "message": "Merge pull request #344 from serjflint/fix/windows-e2e-portability\n\nfix: make E2E regressions portable on Windows",
          "timestamp": "2026-08-13T13:36:40+03:00",
          "tree_id": "d4fed6ae3f5809acae19cfb895dfa3693bc02ffe",
          "url": "https://github.com/serjflint/saitenka/commit/948872c35e736621c514b9840619226c569087f7"
        },
        "date": 1786617458960,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.353009,
            "range": "3 replicas; min 5.12863; max 6.73297; MAD 0.379963",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.665015,
            "range": "3 replicas; min 7.61693; max 9.92405; MAD 1.04808; worst 9.92405",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.493983,
            "range": "3 replicas; min 14.2321; max 19.8059; MAD 0.311912",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.777845,
            "range": "3 replicas; min 14.3191; max 20.1986; MAD 0.420733; worst 20.1986",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.121921,
            "range": "3 replicas; min 20.5028; max 34.8374; MAD 1.61908",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.168025,
            "range": "3 replicas; min 0.106929; max 0.171932; MAD 0.003907; worst 0.171932",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 52.234979,
            "range": "3 replicas; min 44.7634; max 119.572; MAD 7.4716; worst 119.572",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.229132,
            "range": "3 replicas; min 2.91554; max 4.45682; MAD 0.313597; worst 4.45682",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.439769,
            "range": "3 replicas; min 1.33329; max 3.65399; MAD 0.10648; worst 3.65399",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "id": "22c89758de25a68756e478c458f601141d8323a2",
          "message": "fix(perf): exercise live scroll rendering",
          "timestamp": "2026-08-13T09:58:05Z",
          "url": "https://github.com/serjflint/saitenka/commit/22c89758de25a68756e478c458f601141d8323a2"
        },
        "date": 1786618183719,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.986513,
            "range": "3 replicas; min 4.96666; max 6.33523; MAD 0.019852",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.552079,
            "range": "3 replicas; min 7.04253; max 8.84549; MAD 0.509545; worst 8.84549",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 14.438495,
            "range": "3 replicas; min 13.9005; max 19.2563; MAD 0.537993",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 14.64554,
            "range": "3 replicas; min 14.1046; max 19.4785; MAD 0.540961; worst 19.4785",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.230854,
            "range": "3 replicas; min 16.5635; max 269.064; MAD 2.66737",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.101473,
            "range": "3 replicas; min 0.097854; max 0.163326; MAD 0.003619; worst 0.163326",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.921037,
            "range": "3 replicas; min 31.1857; max 279.386; MAD 12.7353; worst 279.386",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.027654,
            "range": "3 replicas; min 2.38578; max 16.1558; MAD 0.641871; worst 16.1558",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.339415,
            "range": "3 replicas; min 1.19375; max 66.9796; MAD 0.145667; worst 66.9796",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d7994c8252bce53d1ad0747a01889e3264cade5c",
          "message": "Merge pull request #345 from serjflint/fix/benchmark-historical-backfill\n\nfix(ci): support comparable historical benchmark backfills",
          "timestamp": "2026-08-13T14:49:13+03:00",
          "tree_id": "5600ebd6cff60c9837f90a8577efd978ae0d1b2f",
          "url": "https://github.com/serjflint/saitenka/commit/d7994c8252bce53d1ad0747a01889e3264cade5c"
        },
        "date": 1786621813898,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.825749,
            "range": "3 replicas; min 3.39253; max 6.39133; MAD 0.565582",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.863878,
            "range": "3 replicas; min 4.94086; max 9.03975; MAD 0.175877; worst 9.03975",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 16.773132,
            "range": "3 replicas; min 9.62099; max 18.4382; MAD 1.6651",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.387382,
            "range": "3 replicas; min 9.756; max 18.7343; MAD 1.34691; worst 18.7343",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.990231,
            "range": "3 replicas; min 17.6664; max 613.063; MAD 0.323834",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.112105,
            "range": "3 replicas; min 0.075033; max 0.133587; MAD 0.021482; worst 0.133587",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.036298,
            "range": "3 replicas; min 19.4112; max 186.169; MAD 18.6251; worst 186.169",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.416401,
            "range": "3 replicas; min 1.99255; max 68.3402; MAD 0.423851; worst 68.3402",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.17352,
            "range": "3 replicas; min 1.09385; max 42.4499; MAD 0.079674; worst 42.4499",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "4cecefac4c5020bb85e154563199c3c3f753a103",
          "message": "refactor: establish modular Saitenka package boundaries (#329)\n\n* refactor: consolidate the saitenka source layout\n\n* refactor: remove legacy overlay project directory",
          "timestamp": "2026-08-12T18:48:40Z",
          "url": "https://github.com/serjflint/saitenka/commit/4cecefac4c5020bb85e154563199c3c3f753a103"
        },
        "date": 1786621891298,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.454847,
            "range": "3 replicas; min 6.33599; max 6.4596; MAD 0.004752",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.260101,
            "range": "3 replicas; min 8.83624; max 9.6468; MAD 0.386696; worst 9.6468",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.493964,
            "range": "3 replicas; min 18.2297; max 19.6029; MAD 0.108925",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.713595,
            "range": "3 replicas; min 18.3376; max 19.8791; MAD 0.16555; worst 19.8791",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.51278,
            "range": "3 replicas; min 15.0827; max 23.4393; MAD 3.43006",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.162003,
            "range": "3 replicas; min 0.130914; max 0.172613; MAD 0.01061; worst 0.172613",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.984184,
            "range": "3 replicas; min 38.1442; max 43.366; MAD 0.381816; worst 43.366",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.067973,
            "range": "3 replicas; min 2.45326; max 3.21396; MAD 0.145988; worst 3.21396",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.203935,
            "range": "3 replicas; min 1.03791; max 1.60425; MAD 0.166029; worst 1.60425",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "213f0dffab19615787018b7f6398ab69b2dc7c1c",
          "message": "fix: preserve source-backed dictionary structure (#330)\n\n* fix: unwrap semantic dictionary glossaries\n\n* test: add dictionary structure oracle",
          "timestamp": "2026-08-12T19:05:18Z",
          "url": "https://github.com/serjflint/saitenka/commit/213f0dffab19615787018b7f6398ab69b2dc7c1c"
        },
        "date": 1786622013044,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.382327,
            "range": "3 replicas; min 6.37752; max 6.43441; MAD 0.004806",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.129761,
            "range": "3 replicas; min 8.98708; max 9.15236; MAD 0.0226; worst 9.15236",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.988355,
            "range": "3 replicas; min 17.9217; max 19.5348; MAD 0.066673",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.205201,
            "range": "3 replicas; min 18.1233; max 20.0038; MAD 0.081904; worst 20.0038",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.608824,
            "range": "3 replicas; min 16.5574; max 22.3193; MAD 1.05144",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.137197,
            "range": "3 replicas; min 0.131568; max 0.168867; MAD 0.005629; worst 0.168867",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.838727,
            "range": "3 replicas; min 38.6767; max 52.2941; MAD 4.16202; worst 52.2941",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.509133,
            "range": "3 replicas; min 2.3559; max 2.76386; MAD 0.153229; worst 2.76386",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.199364,
            "range": "3 replicas; min 0.975051; max 1.40331; MAD 0.203942; worst 1.40331",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "6c0052f37f373ccdfc5d8eab70573c4d24acf581",
          "message": "refactor: modularize reader runtime and CLI (#332)\n\n* refactor(render): remove remaining layer backedges\n\n* refactor(reader): introduce explicit runtime seams\n\n* refactor(cli): split command domains\n\n* refactor(launch): isolate run orchestration\n\n* refactor(reader): centralize production composition\n\n* chore(launch): remove obsolete registry import\n\n* docs(launch): update provider registration boundary\n\n* refactor(cli): narrow command module imports\n\n* docs(render): update structured-content test reference\n\n* fix(launch): register subtitle providers explicitly\n\n* docs(architecture): align advisory and render boundaries\n\n* docs(render): fix worker-boundary wording",
          "timestamp": "2026-08-13T05:15:18Z",
          "url": "https://github.com/serjflint/saitenka/commit/6c0052f37f373ccdfc5d8eab70573c4d24acf581"
        },
        "date": 1786622086807,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.467111,
            "range": "3 replicas; min 6.42917; max 6.66936; MAD 0.037943",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.675197,
            "range": "3 replicas; min 9.56654; max 10.1792; MAD 0.108652; worst 10.1792",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.329035,
            "range": "3 replicas; min 17.9792; max 19.7211; MAD 0.392102",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.535562,
            "range": "3 replicas; min 18.3308; max 19.9515; MAD 0.415915; worst 19.9515",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.122915,
            "range": "3 replicas; min 15.643; max 23.44; MAD 1.31706",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.173385,
            "range": "3 replicas; min 0.167673; max 0.179043; MAD 0.005658; worst 0.179043",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.992279,
            "range": "3 replicas; min 40.09; max 53.616; MAD 2.90223; worst 53.616",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 5.616482,
            "range": "3 replicas; min 3.266; max 236.846; MAD 2.35049; worst 236.846",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 2.308957,
            "range": "3 replicas; min 1.3234; max 4.09009; MAD 0.985558; worst 4.09009",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6",
          "message": "refactor: organize panel subsystem package (#334)",
          "timestamp": "2026-08-13T05:57:24Z",
          "url": "https://github.com/serjflint/saitenka/commit/39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6"
        },
        "date": 1786622138731,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.301828,
            "range": "3 replicas; min 4.9326; max 6.38487; MAD 0.083039",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.6837,
            "range": "3 replicas; min 6.89264; max 9.4537; MAD 0.770005; worst 9.4537",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.36047,
            "range": "3 replicas; min 14.1359; max 19.7397; MAD 0.379214",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.085587,
            "range": "3 replicas; min 14.7239; max 29.4679; MAD 5.36167; worst 29.4679",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.442229,
            "range": "3 replicas; min 20.3781; max 23.6701; MAD 0.064083",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.171331,
            "range": "3 replicas; min 0.10154; max 0.178254; MAD 0.006923; worst 0.178254",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.070449,
            "range": "3 replicas; min 30.0869; max 43.2971; MAD 1.22666; worst 43.2971",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.315447,
            "range": "3 replicas; min 2.91827; max 13.9043; MAD 0.397176; worst 13.9043",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.512608,
            "range": "3 replicas; min 1.36011; max 7.0594; MAD 0.152502; worst 7.0594",
            "unit": "ms"
          }
        ]
      },
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
          "id": "91b9e4c1bcca722428ab3222d01613a72218264d",
          "message": "Merge pull request #351 from serjflint/codex/libass-subtitle-implementation\n\nfeat(libasslite): add system libass binding",
          "timestamp": "2026-08-15T14:07:44+03:00",
          "tree_id": "7a0d18139bd6d77fb9c7fc8650e77f4315b73295",
          "url": "https://github.com/serjflint/saitenka/commit/91b9e4c1bcca722428ab3222d01613a72218264d"
        },
        "date": 1786792129583,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.353066,
            "range": "3 replicas; min 4.89188; max 6.57605; MAD 0.222982",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.631,
            "range": "3 replicas; min 6.79406; max 9.23599; MAD 0.604993; worst 9.23599",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.449513,
            "range": "3 replicas; min 13.736; max 19.74; MAD 0.290515",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.02014,
            "range": "3 replicas; min 13.8425; max 20.2691; MAD 0.249001; worst 20.2691",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.381942,
            "range": "3 replicas; min 19.4789; max 20.6188; MAD 0.236895",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.170029,
            "range": "3 replicas; min 0.101263; max 0.172201; MAD 0.002172; worst 0.172201",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.869843,
            "range": "3 replicas; min 31.4961; max 44.2243; MAD 0.354451; worst 44.2243",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.605978,
            "range": "3 replicas; min 2.60318; max 3.33389; MAD 0.002801; worst 3.33389",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.269231,
            "range": "3 replicas; min 0.986404; max 2.54214; MAD 0.282827; worst 2.54214",
            "unit": "ms"
          }
        ]
      },
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
          "id": "7ccbecf2a6b37ba2f917a4bf9816cd92e96a936d",
          "message": "Merge pull request #355 from serjflint/issue-353\n\ntest(libass): lock token-ID feasibility matrix",
          "timestamp": "2026-08-15T15:59:49+03:00",
          "tree_id": "4c931cc7196cf1a79deea76ab3f4b4639dfd6e9c",
          "url": "https://github.com/serjflint/saitenka/commit/7ccbecf2a6b37ba2f917a4bf9816cd92e96a936d"
        },
        "date": 1786798835439,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.457399,
            "range": "3 replicas; min 6.29982; max 6.46743; MAD 0.010029",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.935517,
            "range": "3 replicas; min 8.80142; max 10.8349; MAD 0.899433; worst 10.8349",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.351395,
            "range": "3 replicas; min 17.8769; max 19.8577; MAD 0.506309",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.314618,
            "range": "3 replicas; min 19.495; max 23.3957; MAD 0.819619; worst 23.3957",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.138011,
            "range": "3 replicas; min 18.0328; max 23.0407; MAD 1.9027",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.171982,
            "range": "3 replicas; min 0.132829; max 0.18687; MAD 0.014888; worst 0.18687",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.28033,
            "range": "3 replicas; min 42.7846; max 194.643; MAD 1.49571; worst 194.643",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.848434,
            "range": "3 replicas; min 2.5523; max 2.86512; MAD 0.016691; worst 2.86512",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.468755,
            "range": "3 replicas; min 1.16596; max 1.61752; MAD 0.148768; worst 1.61752",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a10fc7e15aba97ef4b538433b08b3d4a1662c623",
          "message": "Merge pull request #356 from serjflint/issue-354\n\ntest(mpv): prove source transition envelope",
          "timestamp": "2026-08-15T19:29:32+03:00",
          "tree_id": "3f9392810a77a2ad79a2c66ed10d914a45093437",
          "url": "https://github.com/serjflint/saitenka/commit/a10fc7e15aba97ef4b538433b08b3d4a1662c623"
        },
        "date": 1786811434927,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.280778,
            "range": "3 replicas; min 5.21005; max 6.4098; MAD 0.129021",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.882503,
            "range": "3 replicas; min 7.88784; max 9.33974; MAD 0.457238; worst 9.33974",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.699121,
            "range": "3 replicas; min 16.4487; max 19.2757; MAD 1.25045",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.643255,
            "range": "3 replicas; min 16.9181; max 20.921; MAD 1.27779; worst 20.921",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.429101,
            "range": "3 replicas; min 16.6603; max 170.96; MAD 3.76882",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.133208,
            "range": "3 replicas; min 0.108681; max 0.167773; MAD 0.024527; worst 0.167773",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 47.241243,
            "range": "3 replicas; min 42.8192; max 84.1431; MAD 4.42201; worst 84.1431",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.868772,
            "range": "3 replicas; min 2.48773; max 173.503; MAD 0.38104; worst 173.503",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.39938,
            "range": "3 replicas; min 1.26294; max 37.3486; MAD 0.136441; worst 37.3486",
            "unit": "ms"
          }
        ]
      },
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
          "id": "65ad1c0ad69e88efe6ce977742ab1f98f7ee2b64",
          "message": "Merge pull request #358 from serjflint/issue-357\n\nfeat(subtitles): add lossless ASS rewriting",
          "timestamp": "2026-08-16T08:31:25+03:00",
          "tree_id": "3387dee19dd84a3ad98a9d5dbbd83bb33ffd4c1f",
          "url": "https://github.com/serjflint/saitenka/commit/65ad1c0ad69e88efe6ce977742ab1f98f7ee2b64"
        },
        "date": 1786858349389,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.333147,
            "range": "3 replicas; min 6.31818; max 6.34341; MAD 0.010264",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.945928,
            "range": "3 replicas; min 8.67961; max 9.61564; MAD 0.266316; worst 9.61564",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.201557,
            "range": "3 replicas; min 17.8114; max 19.2968; MAD 0.390205",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.009478,
            "range": "3 replicas; min 17.9575; max 19.607; MAD 0.597539; worst 19.607",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.592402,
            "range": "3 replicas; min 15.3521; max 19.6238; MAD 0.031356",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.132978,
            "range": "3 replicas; min 0.132787; max 0.172872; MAD 0.000191; worst 0.172872",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.085267,
            "range": "3 replicas; min 38.704; max 42.7381; MAD 0.381242; worst 42.7381",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.442775,
            "range": "3 replicas; min 2.37511; max 2.93835; MAD 0.067661; worst 2.93835",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.128419,
            "range": "3 replicas; min 1.11627; max 1.24522; MAD 0.012149; worst 1.24522",
            "unit": "ms"
          }
        ]
      },
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
          "id": "9d83485068d089e54ef262d3f04760ba96039db6",
          "message": "Merge pull request #360 from serjflint/issue-359\n\ntest(subtitles): lock mpv geometry parity oracle",
          "timestamp": "2026-08-16T08:32:06+03:00",
          "tree_id": "2152309aeb18fda5c4bedb75779737b3c264faa7",
          "url": "https://github.com/serjflint/saitenka/commit/9d83485068d089e54ef262d3f04760ba96039db6"
        },
        "date": 1786858373631,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.985189,
            "range": "3 replicas; min 4.9298; max 6.34122; MAD 0.055388",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.963738,
            "range": "3 replicas; min 6.81228; max 8.9286; MAD 0.151458; worst 8.9286",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 13.809078,
            "range": "3 replicas; min 13.7759; max 17.8414; MAD 0.033197",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 15.715715,
            "range": "3 replicas; min 14.4177; max 18.0251; MAD 1.29803; worst 18.0251",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.05027,
            "range": "3 replicas; min 18.2761; max 79.2461; MAD 1.77417",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.11261,
            "range": "3 replicas; min 0.110163; max 0.132817; MAD 0.002447; worst 0.132817",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.861085,
            "range": "3 replicas; min 30.5713; max 218.736; MAD 8.28979; worst 218.736",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.082651,
            "range": "3 replicas; min 2.51244; max 8.89924; MAD 0.570209; worst 8.89924",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.843629,
            "range": "3 replicas; min 1.61188; max 4.20477; MAD 0.231746; worst 4.20477",
            "unit": "ms"
          }
        ]
      },
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
          "id": "131057a62451eb67bd270d7dbdaae863d6d192ce",
          "message": "Merge pull request #362 from serjflint/issue-361\n\nperf(subtitles): measure shadow prototype",
          "timestamp": "2026-08-16T08:32:43+03:00",
          "tree_id": "6b6af37352db1f0ffdbc71b97dba34ad84a753eb",
          "url": "https://github.com/serjflint/saitenka/commit/131057a62451eb67bd270d7dbdaae863d6d192ce"
        },
        "date": 1786858412929,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.368997,
            "range": "3 replicas; min 6.34663; max 6.39453; MAD 0.022367",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.408203,
            "range": "3 replicas; min 8.66795; max 9.51798; MAD 0.109778; worst 9.51798",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.349663,
            "range": "3 replicas; min 17.8713; max 19.3598; MAD 0.010131",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.563225,
            "range": "3 replicas; min 19.5512; max 26.8122; MAD 0.012045; worst 26.8122",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 23.342502,
            "range": "3 replicas; min 18.0571; max 43.0381; MAD 5.28535",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.169558,
            "range": "3 replicas; min 0.135563; max 0.185578; MAD 0.01602; worst 0.185578",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.177575,
            "range": "3 replicas; min 38.034; max 43.6937; MAD 0.516078; worst 43.6937",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.833052,
            "range": "3 replicas; min 2.53611; max 14.8886; MAD 0.296945; worst 14.8886",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.421696,
            "range": "3 replicas; min 1.20098; max 2.6383; MAD 0.220718; worst 2.6383",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8cc21f8a37c8a81fc9a2d9ef741fe72d9330b81d",
          "message": "Merge pull request #364 from serjflint/issue-363\n\nfeat(subtitles): add opt-in native libass geometry",
          "timestamp": "2026-08-16T08:33:35+03:00",
          "tree_id": "02c1f859c6bacc03e1d5fff051a86a05f8bd7da2",
          "url": "https://github.com/serjflint/saitenka/commit/8cc21f8a37c8a81fc9a2d9ef741fe72d9330b81d"
        },
        "date": 1786858502938,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.371059,
            "range": "3 replicas; min 6.27234; max 6.54763; MAD 0.098718",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.980205,
            "range": "3 replicas; min 8.63164; max 9.57856; MAD 0.348566; worst 9.57856",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.524247,
            "range": "3 replicas; min 17.6685; max 19.7655; MAD 0.241287",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.695138,
            "range": "3 replicas; min 17.9622; max 20.0322; MAD 0.337075; worst 20.0322",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.070327,
            "range": "3 replicas; min 16.4253; max 19.6229; MAD 1.55253",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.164257,
            "range": "3 replicas; min 0.132005; max 0.174418; MAD 0.010161; worst 0.174418",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.236024,
            "range": "3 replicas; min 38.7326; max 142.801; MAD 3.50338; worst 142.801",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.890902,
            "range": "3 replicas; min 2.286; max 3.28649; MAD 0.39559; worst 3.28649",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.24706,
            "range": "3 replicas; min 1.06113; max 1.39404; MAD 0.146978; worst 1.39404",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5d5ee539e877445ec27352a5ff1226f3eb802547",
          "message": "Merge pull request #366 from serjflint/issue-365\n\nbuild(libasslite): ship optional self-contained runtime bundle",
          "timestamp": "2026-08-16T08:34:07+03:00",
          "tree_id": "320675ff842168e916cfac4bc871e9be42817c6d",
          "url": "https://github.com/serjflint/saitenka/commit/5d5ee539e877445ec27352a5ff1226f3eb802547"
        },
        "date": 1786858522470,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.318083,
            "range": "3 replicas; min 3.59447; max 6.3846; MAD 0.066514",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.70114,
            "range": "3 replicas; min 5.19039; max 8.89708; MAD 0.195936; worst 8.89708",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.225539,
            "range": "3 replicas; min 9.91636; max 19.284; MAD 0.058426",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.475351,
            "range": "3 replicas; min 10.0079; max 19.5105; MAD 0.03519; worst 19.5105",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.724594,
            "range": "3 replicas; min 22.093; max 23.005; MAD 0.280408",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.176799,
            "range": "3 replicas; min 0.076355; max 0.179886; MAD 0.003087; worst 0.179886",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.593818,
            "range": "3 replicas; min 21.3199; max 43.6168; MAD 0.02294; worst 43.6168",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.072046,
            "range": "3 replicas; min 2.98429; max 10.1032; MAD 0.087756; worst 10.1032",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.535546,
            "range": "3 replicas; min 1.12753; max 1.59937; MAD 0.063826; worst 1.59937",
            "unit": "ms"
          }
        ]
      },
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
          "id": "75487cab368bf8e9f4a1fc90343d8cfc1d304f5b",
          "message": "Merge pull request #367 from serjflint/codex/testpypi-addon-publishers\n\nci: add TestPyPI dry-runs for addon packages",
          "timestamp": "2026-08-16T09:23:19+03:00",
          "tree_id": "dbb5a8324188fa67287ef625ead7deaf55cf49ab",
          "url": "https://github.com/serjflint/saitenka/commit/75487cab368bf8e9f4a1fc90343d8cfc1d304f5b"
        },
        "date": 1786861441699,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.331886,
            "range": "3 replicas; min 6.32036; max 6.34835; MAD 0.011524",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.905371,
            "range": "3 replicas; min 8.84031; max 9.30748; MAD 0.065058; worst 9.30748",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.743409,
            "range": "3 replicas; min 17.6033; max 19.3357; MAD 0.140074",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.868183,
            "range": "3 replicas; min 17.723; max 19.5132; MAD 0.145148; worst 19.5132",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 16.866485,
            "range": "3 replicas; min 16.7751; max 24.1461; MAD 0.091406",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.13309,
            "range": "3 replicas; min 0.130513; max 0.169978; MAD 0.002577; worst 0.169978",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.341387,
            "range": "3 replicas; min 38.1988; max 43.532; MAD 0.142575; worst 43.532",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.383528,
            "range": "3 replicas; min 2.29189; max 3.24303; MAD 0.091634; worst 3.24303",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.388329,
            "range": "3 replicas; min 1.05465; max 1.62092; MAD 0.232593; worst 1.62092",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f80179039f1c614982ea9d5944ae3836cf2cfef7",
          "message": "Merge pull request #368 from serjflint/codex/testpypi-libasslite-bundle\n\nci: validate libasslite TestPyPI releases",
          "timestamp": "2026-08-16T10:16:30+03:00",
          "tree_id": "cac593c4c55cc7366e790ac68302dc0aa4a84601",
          "url": "https://github.com/serjflint/saitenka/commit/f80179039f1c614982ea9d5944ae3836cf2cfef7"
        },
        "date": 1786864723843,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.300008,
            "range": "3 replicas; min 4.97154; max 6.35716; MAD 0.057149",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.615634,
            "range": "3 replicas; min 7.39913; max 12.6447; MAD 1.2165; worst 12.6447",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.310058,
            "range": "3 replicas; min 14.2683; max 19.6571; MAD 0.347032",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.661764,
            "range": "3 replicas; min 15.5853; max 21.7185; MAD 2.05673; worst 21.7185",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.188456,
            "range": "3 replicas; min 21.1925; max 23.0778; MAD 0.889309",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.165778,
            "range": "3 replicas; min 0.094436; max 0.206817; MAD 0.041039; worst 0.206817",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.070202,
            "range": "3 replicas; min 38.8146; max 166.037; MAD 4.2556; worst 166.037",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.784506,
            "range": "3 replicas; min 2.62209; max 3.02436; MAD 0.162418; worst 3.02436",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.424438,
            "range": "3 replicas; min 1.3568; max 1.55006; MAD 0.067639; worst 1.55006",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1a98326aa19fd6845de32e3c1e8eb73abbba742b",
          "message": "Merge pull request #369 from serjflint/codex/fix-bundle-release-repo-context\n\nfix(ci): provide repository context for bundle sources",
          "timestamp": "2026-08-16T11:13:18+03:00",
          "tree_id": "3d516e19aad8deca936bbccb1b0b88ffdd0eb8f8",
          "url": "https://github.com/serjflint/saitenka/commit/1a98326aa19fd6845de32e3c1e8eb73abbba742b"
        },
        "date": 1786868280544,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.335826,
            "range": "3 replicas; min 6.27618; max 6.70065; MAD 0.059645",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.731769,
            "range": "3 replicas; min 8.72369; max 9.55355; MAD 0.008077; worst 9.55355",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.241534,
            "range": "3 replicas; min 19.1041; max 19.6438; MAD 0.137465",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.549506,
            "range": "3 replicas; min 19.3285; max 21.6184; MAD 0.220996; worst 21.6184",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.89247,
            "range": "3 replicas; min 20.1496; max 25.5431; MAD 0.742843",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.16712,
            "range": "3 replicas; min 0.165441; max 0.17651; MAD 0.001679; worst 0.17651",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.433402,
            "range": "3 replicas; min 42.4816; max 44.6258; MAD 0.951784; worst 44.6258",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.884036,
            "range": "3 replicas; min 2.65062; max 5.69918; MAD 0.233413; worst 5.69918",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.490519,
            "range": "3 replicas; min 1.27296; max 1.62473; MAD 0.134211; worst 1.62473",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0750434093018cbf9dbd482bba09e53ac47b8a41",
          "message": "Merge pull request #370 from serjflint/codex/document-native-subtitle-architecture\n\ndocs: explain native subtitle architecture",
          "timestamp": "2026-08-16T11:35:44+03:00",
          "tree_id": "c9871d4b0e4307b5506235a1c95976133a64f9ca",
          "url": "https://github.com/serjflint/saitenka/commit/0750434093018cbf9dbd482bba09e53ac47b8a41"
        },
        "date": 1786869504565,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.931477,
            "range": "3 replicas; min 4.19075; max 6.34633; MAD 0.740727",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.914353,
            "range": "3 replicas; min 5.65628; max 9.1962; MAD 0.281849; worst 9.1962",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 14.015714,
            "range": "3 replicas; min 11.9392; max 19.3782; MAD 2.07648",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 14.095931,
            "range": "3 replicas; min 12.0795; max 19.6992; MAD 2.01638; worst 19.6992",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 41.638004,
            "range": "3 replicas; min 23.5122; max 72.6221; MAD 18.1258",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.105866,
            "range": "3 replicas; min 0.081981; max 0.168254; MAD 0.023885; worst 0.168254",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.42764,
            "range": "3 replicas; min 43.4309; max 136.601; MAD 0.996708; worst 136.601",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 4.621914,
            "range": "3 replicas; min 3.77476; max 11.225; MAD 0.847151; worst 11.225",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 3.323948,
            "range": "3 replicas; min 1.63053; max 65.1455; MAD 1.69342; worst 65.1455",
            "unit": "ms"
          }
        ]
      },
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
          "id": "37227eb039dbfe035667c0def097b5e16ea7babf",
          "message": "Merge pull request #371 from serjflint/codex/cache-vcpkg-downloads\n\nci: cache vcpkg source downloads",
          "timestamp": "2026-08-16T11:52:33+03:00",
          "tree_id": "b767b65a97303660f8b4a4ba80136f27064cbdc4",
          "url": "https://github.com/serjflint/saitenka/commit/37227eb039dbfe035667c0def097b5e16ea7babf"
        },
        "date": 1786870402236,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.326851,
            "range": "3 replicas; min 6.31887; max 6.35824; MAD 0.007978",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.88895,
            "range": "3 replicas; min 8.64565; max 8.95672; MAD 0.067769; worst 8.95672",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.950325,
            "range": "3 replicas; min 17.7832; max 19.4836; MAD 0.167081",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.234727,
            "range": "3 replicas; min 18.036; max 19.6433; MAD 0.198721; worst 19.6433",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.336895,
            "range": "3 replicas; min 16.8971; max 22.4166; MAD 0.439746",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.134924,
            "range": "3 replicas; min 0.130642; max 0.163446; MAD 0.004282; worst 0.163446",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.005793,
            "range": "3 replicas; min 37.7196; max 42.6492; MAD 0.286226; worst 42.6492",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.384585,
            "range": "3 replicas; min 2.32739; max 3.06121; MAD 0.057199; worst 3.06121",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.050773,
            "range": "3 replicas; min 0.959332; max 1.32224; MAD 0.091441; worst 1.32224",
            "unit": "ms"
          }
        ]
      },
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
          "id": "345f9276bda93b0b5b6174dd5ee933963f4cdb6b",
          "message": "fix: preserve subtitle interaction on native fallback (#372)",
          "timestamp": "2026-08-16T14:57:35+05:00",
          "tree_id": "bcc17d735b26159ce55530bde0e229fb07878aad",
          "url": "https://github.com/serjflint/saitenka/commit/345f9276bda93b0b5b6174dd5ee933963f4cdb6b"
        },
        "date": 1786874304978,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.381188,
            "range": "3 replicas; min 6.34997; max 6.56171; MAD 0.031217",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.487404,
            "range": "3 replicas; min 8.72515; max 11.6445; MAD 0.762254; worst 11.6445",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.496076,
            "range": "3 replicas; min 19.4878; max 19.4969; MAD 0.00084",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.787883,
            "range": "3 replicas; min 19.7562; max 19.8085; MAD 0.020663; worst 19.8085",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.458417,
            "range": "3 replicas; min 19.9527; max 24.2885; MAD 1.83011",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167483,
            "range": "3 replicas; min 0.166842; max 0.173445; MAD 0.000641; worst 0.173445",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.462737,
            "range": "3 replicas; min 43.1179; max 43.9107; MAD 0.344819; worst 43.9107",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.403547,
            "range": "3 replicas; min 2.65011; max 4.00525; MAD 0.601702; worst 4.00525",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.474429,
            "range": "3 replicas; min 1.32741; max 1.66022; MAD 0.147023; worst 1.66022",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a22d4e042a1dcb66570cdea833d1bdc37191f26a",
          "message": "Merge pull request #374 from serjflint/codex/native-subtitle-retina\n\nfix: support Retina native subtitle geometry",
          "timestamp": "2026-08-16T15:26:56+05:00",
          "tree_id": "ff8f2ec2e85b2e987130e6c14927a245e1cfe801",
          "url": "https://github.com/serjflint/saitenka/commit/a22d4e042a1dcb66570cdea833d1bdc37191f26a"
        },
        "date": 1786876086060,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.372275,
            "range": "3 replicas; min 6.31829; max 6.4171; MAD 0.044824",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.815589,
            "range": "3 replicas; min 8.64413; max 8.85261; MAD 0.03702; worst 8.85261",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.474433,
            "range": "3 replicas; min 19.1488; max 19.7731; MAD 0.298708",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.574651,
            "range": "3 replicas; min 19.35; max 20.0963; MAD 0.224615; worst 20.0963",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.306548,
            "range": "3 replicas; min 20.4253; max 26.0097; MAD 0.881274",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.171211,
            "range": "3 replicas; min 0.163819; max 0.171862; MAD 0.000651; worst 0.171862",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.236458,
            "range": "3 replicas; min 42.9724; max 123.274; MAD 2.26409; worst 123.274",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.667132,
            "range": "3 replicas; min 2.60224; max 3.80731; MAD 0.140182; worst 3.80731",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.777942,
            "range": "3 replicas; min 1.49506; max 2.44828; MAD 0.282881; worst 2.44828",
            "unit": "ms"
          }
        ]
      },
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
          "id": "435f70e18b5a80799a5c9e11936117f79996c993",
          "message": "Merge pull request #380 from serjflint/codex/native-subtitle-resilience\n\nfeat(subtitles): make native geometry frame resilient",
          "timestamp": "2026-08-16T18:18:10+05:00",
          "tree_id": "d7f53789414a83c2a5cdb63868dbf3a0c802486e",
          "url": "https://github.com/serjflint/saitenka/commit/435f70e18b5a80799a5c9e11936117f79996c993"
        },
        "date": 1786886356034,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.348725,
            "range": "3 replicas; min 6.31261; max 6.53301; MAD 0.036111",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.145887,
            "range": "3 replicas; min 8.6449; max 9.16184; MAD 0.015957; worst 9.16184",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.47515,
            "range": "3 replicas; min 19.2776; max 19.6053; MAD 0.13019",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.68294,
            "range": "3 replicas; min 19.418; max 20.1488; MAD 0.264919; worst 20.1488",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.331028,
            "range": "3 replicas; min 19.0276; max 23.3059; MAD 1.97485",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.169919,
            "range": "3 replicas; min 0.168906; max 0.186761; MAD 0.001013; worst 0.186761",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.112474,
            "range": "3 replicas; min 42.5663; max 43.3954; MAD 0.282914; worst 43.3954",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.146036,
            "range": "3 replicas; min 2.72867; max 3.67553; MAD 0.417367; worst 3.67553",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.794784,
            "range": "3 replicas; min 1.26297; max 2.38352; MAD 0.531814; worst 2.38352",
            "unit": "ms"
          }
        ]
      },
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
          "id": "f273d5a37dd137708b4b9914acfbb0f1dabfaa0c",
          "message": "Merge pull request #381 from serjflint/codex/native-sub-delay\n\nfix(subtitles): honor mpv delay in native geometry",
          "timestamp": "2026-08-16T20:06:42+05:00",
          "tree_id": "cd89e89c4d9a4080222b849433504e454e828085",
          "url": "https://github.com/serjflint/saitenka/commit/f273d5a37dd137708b4b9914acfbb0f1dabfaa0c"
        },
        "date": 1786892855618,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.316567,
            "range": "3 replicas; min 5.78731; max 6.32601; MAD 0.009439",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.776333,
            "range": "3 replicas; min 8.65226; max 9.62393; MAD 0.124073; worst 9.62393",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.954289,
            "range": "3 replicas; min 16.5153; max 19.3437; MAD 1.38944",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.171085,
            "range": "3 replicas; min 17.2349; max 19.6326; MAD 0.936184; worst 19.6326",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.260922,
            "range": "3 replicas; min 20.7999; max 24.8299; MAD 0.461",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.13228,
            "range": "3 replicas; min 0.111155; max 0.167064; MAD 0.021125; worst 0.167064",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.835403,
            "range": "3 replicas; min 39.3991; max 200.718; MAD 3.4363; worst 200.718",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 10.551649,
            "range": "3 replicas; min 2.951; max 29.8258; MAD 7.60065; worst 29.8258",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.516648,
            "range": "3 replicas; min 1.27064; max 1.56605; MAD 0.049403; worst 1.56605",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a06fd85d3e55c1d231de83a6363eb660b04791af",
          "message": "Merge pull request #382 from serjflint/codex/native-pending-no-flash\n\nfix: keep native subtitle pixels stable across geometry gaps",
          "timestamp": "2026-08-17T00:00:10+05:00",
          "tree_id": "4fa1418a0ce29418ea78d540ed7c103da51c8caf",
          "url": "https://github.com/serjflint/saitenka/commit/a06fd85d3e55c1d231de83a6363eb660b04791af"
        },
        "date": 1786906852803,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.404073,
            "range": "3 replicas; min 6.37371; max 6.40536; MAD 0.001289",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.850563,
            "range": "3 replicas; min 8.69699; max 8.97448; MAD 0.123917; worst 8.97448",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.497436,
            "range": "3 replicas; min 17.6301; max 19.5367; MAD 0.039259",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.672587,
            "range": "3 replicas; min 17.7858; max 20.0822; MAD 0.409619; worst 20.0822",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.86321,
            "range": "3 replicas; min 20.6345; max 23.1488; MAD 0.228737",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.164671,
            "range": "3 replicas; min 0.157638; max 0.168917; MAD 0.004246; worst 0.168917",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.822338,
            "range": "3 replicas; min 41.2293; max 43.6011; MAD 0.778774; worst 43.6011",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.920872,
            "range": "3 replicas; min 2.72033; max 3.14187; MAD 0.200546; worst 3.14187",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.965301,
            "range": "3 replicas; min 1.42104; max 2.07126; MAD 0.10596; worst 2.07126",
            "unit": "ms"
          }
        ]
      },
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
          "id": "630742cd95d52b43ff0fed7351615ea11aabf492",
          "message": "fix: stabilize subtitle picker and native scanning (#383)\n\n* fix: stabilize subtitle picker and scanning\n\n* fix: harden sparse subtitle interactions\n\n* fix: bound picker coalescing to event runs\n\n* fix: preserve picker coalescing across properties",
          "timestamp": "2026-08-17T02:26:31+05:00",
          "tree_id": "0c2a51cf6093d5b2058ead3d92ac632f0b43d96e",
          "url": "https://github.com/serjflint/saitenka/commit/630742cd95d52b43ff0fed7351615ea11aabf492"
        },
        "date": 1786915635567,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.352446,
            "range": "3 replicas; min 6.3266; max 6.35869; MAD 0.006248",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.78446,
            "range": "3 replicas; min 8.69226; max 8.79738; MAD 0.012923; worst 8.79738",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.320734,
            "range": "3 replicas; min 17.6761; max 19.9251; MAD 0.604355",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.299479,
            "range": "3 replicas; min 17.805; max 24.0837; MAD 2.49448; worst 24.0837",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.305362,
            "range": "3 replicas; min 19.785; max 26.6627; MAD 1.52035",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.16557,
            "range": "3 replicas; min 0.130494; max 0.17101; MAD 0.00544; worst 0.17101",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.985665,
            "range": "3 replicas; min 39.1497; max 44.3087; MAD 1.32308; worst 44.3087",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.76226,
            "range": "3 replicas; min 2.42942; max 2.98027; MAD 0.218008; worst 2.98027",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.988442,
            "range": "3 replicas; min 1.08502; max 2.19654; MAD 0.208099; worst 2.19654",
            "unit": "ms"
          }
        ]
      },
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
          "id": "56c767f809163b2391bce6081927dffc7ba00a0b",
          "message": "fix(dict): index exact term attestation (#384)",
          "timestamp": "2026-08-17T00:44:54+03:00",
          "tree_id": "8b309f327ba0e3ba37e4c8e6b0a29ff5228bb059",
          "url": "https://github.com/serjflint/saitenka/commit/56c767f809163b2391bce6081927dffc7ba00a0b"
        },
        "date": 1786916764503,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.663558,
            "range": "3 replicas; min 6.54498; max 6.80123; MAD 0.118575",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.425804,
            "range": "3 replicas; min 9.00275; max 9.52012; MAD 0.094316; worst 9.52012",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.593964,
            "range": "3 replicas; min 19.1378; max 19.7741; MAD 0.180114",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.841005,
            "range": "3 replicas; min 19.3363; max 20.1697; MAD 0.328645; worst 20.1697",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.431811,
            "range": "3 replicas; min 18.4554; max 20.8657; MAD 0.433896",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.174155,
            "range": "3 replicas; min 0.165919; max 0.174387; MAD 0.000232; worst 0.174387",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.001517,
            "range": "3 replicas; min 42.9562; max 44.4273; MAD 0.425763; worst 44.4273",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.031511,
            "range": "3 replicas; min 2.78537; max 4.43028; MAD 0.246142; worst 4.43028",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.507388,
            "range": "3 replicas; min 1.44626; max 1.59668; MAD 0.061128; worst 1.59668",
            "unit": "ms"
          }
        ]
      },
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
          "id": "747d4c58fcf5c9249cf2ebee79222dbf6b483c4a",
          "message": "fix(startup): keep reader loop responsive during annotation (#385)",
          "timestamp": "2026-08-17T04:52:12+05:00",
          "tree_id": "72f1b03db91339b166edd39fe1d02a7588367b92",
          "url": "https://github.com/serjflint/saitenka/commit/747d4c58fcf5c9249cf2ebee79222dbf6b483c4a"
        },
        "date": 1786924415855,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.348264,
            "range": "3 replicas; min 6.32976; max 6.39254; MAD 0.018504",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.891364,
            "range": "3 replicas; min 8.75682; max 8.94738; MAD 0.056016; worst 8.94738",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.998627,
            "range": "3 replicas; min 18.0491; max 19.4606; MAD 0.461986",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.26755,
            "range": "3 replicas; min 18.3373; max 19.9076; MAD 0.640024; worst 19.9076",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.391787,
            "range": "3 replicas; min 14.9138; max 22.9265; MAD 2.53469",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.165831,
            "range": "3 replicas; min 0.134592; max 0.168694; MAD 0.002863; worst 0.168694",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.093995,
            "range": "3 replicas; min 38.5797; max 43.3993; MAD 0.305291; worst 43.3993",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.856528,
            "range": "3 replicas; min 2.43879; max 2.97388; MAD 0.117352; worst 2.97388",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.595307,
            "range": "3 replicas; min 1.03178; max 1.6428; MAD 0.047493; worst 1.6428",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0551068f550609736f1e1d8abcb6e2db2fee560a",
          "message": "test(ipc): correlate Windows pipe reply (#386)",
          "timestamp": "2026-08-17T05:01:17+05:00",
          "tree_id": "e7af262875a612617815ca69d7e3bb2a9846d9bb",
          "url": "https://github.com/serjflint/saitenka/commit/0551068f550609736f1e1d8abcb6e2db2fee560a"
        },
        "date": 1786924926300,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.3359,
            "range": "3 replicas; min 4.16629; max 6.40873; MAD 0.072833",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.765055,
            "range": "3 replicas; min 5.94023; max 9.46465; MAD 0.699598; worst 9.46465",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.404048,
            "range": "3 replicas; min 11.8675; max 19.479; MAD 0.074997",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.57607,
            "range": "3 replicas; min 12.037; max 19.6732; MAD 0.097178; worst 19.6732",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.546958,
            "range": "3 replicas; min 21.1688; max 266.398; MAD 1.37818",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.164447,
            "range": "3 replicas; min 0.080013; max 0.167954; MAD 0.003507; worst 0.167954",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.976318,
            "range": "3 replicas; min 42.8078; max 67.3771; MAD 1.16855; worst 67.3771",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.718076,
            "range": "3 replicas; min 2.74532; max 347.243; MAD 0.972754; worst 347.243",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.747925,
            "range": "3 replicas; min 1.21862; max 22.3815; MAD 0.529307; worst 22.3815",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ead9ef2fac8e4b70d74e123a1fc2f569a398583c",
          "message": "Merge pull request #387 from serjflint/codex/native-interaction-polish\n\nfix: keep subtitle interactions off the event loop",
          "timestamp": "2026-08-17T16:19:13+05:00",
          "tree_id": "46e143cab5d63c8ff575eeecac92e99a8dd224b9",
          "url": "https://github.com/serjflint/saitenka/commit/ead9ef2fac8e4b70d74e123a1fc2f569a398583c"
        },
        "date": 1786965605135,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.314575,
            "range": "3 replicas; min 6.30104; max 6.39215; MAD 0.013534",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.938635,
            "range": "3 replicas; min 8.79478; max 9.24053; MAD 0.143851; worst 9.24053",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.238591,
            "range": "3 replicas; min 17.7801; max 19.6257; MAD 0.458511",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.560836,
            "range": "3 replicas; min 18.2736; max 21.3129; MAD 0.287267; worst 21.3129",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.761492,
            "range": "3 replicas; min 18.0364; max 27.8105; MAD 2.72509",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.14888,
            "range": "3 replicas; min 0.138626; max 0.166591; MAD 0.010254; worst 0.166591",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.893344,
            "range": "3 replicas; min 39.4935; max 253.014; MAD 4.39986; worst 253.014",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.612454,
            "range": "3 replicas; min 2.58135; max 4.07928; MAD 0.0311; worst 4.07928",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.35369,
            "range": "3 replicas; min 1.27195; max 1.96556; MAD 0.081744; worst 1.96556",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0536fb61766ec1459db0fcbd3f289c06344ff631",
          "message": "Merge pull request #389 from serjflint/codex/runtime-behavior-oracle\n\ntest(runtime): freeze semantic behavior",
          "timestamp": "2026-08-17T16:20:03+05:00",
          "tree_id": "387356f66a46511290e9f2cdc9a4127eccd1126e",
          "url": "https://github.com/serjflint/saitenka/commit/0536fb61766ec1459db0fcbd3f289c06344ff631"
        },
        "date": 1786965655243,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.34294,
            "range": "3 replicas; min 5.92807; max 6.34814; MAD 0.005199",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.770059,
            "range": "3 replicas; min 8.12985; max 9.22684; MAD 0.456785; worst 9.22684",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.443576,
            "range": "3 replicas; min 17.0809; max 20.0881; MAD 0.644492",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.33022,
            "range": "3 replicas; min 17.4156; max 21.367; MAD 1.03675; worst 21.367",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 25.549909,
            "range": "3 replicas; min 22.6709; max 267.209; MAD 2.879",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.168585,
            "range": "3 replicas; min 0.114604; max 0.176289; MAD 0.007704; worst 0.176289",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.747121,
            "range": "3 replicas; min 44.3563; max 125.624; MAD 0.390783; worst 125.624",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 4.194453,
            "range": "3 replicas; min 3.85839; max 229.763; MAD 0.336061; worst 229.763",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.875386,
            "range": "3 replicas; min 1.46496; max 3.42953; MAD 0.410422; worst 3.42953",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d3daf87dbee8435456ddd2f21d7820d9cae2a0c2",
          "message": "Merge pull request #388 from serjflint/codex/event-runtime-foundation\n\nrefactor(runtime): add event reactor foundation",
          "timestamp": "2026-08-17T16:20:43+05:00",
          "tree_id": "3c2cc3175d9c112e2f444761504b1763f8abde61",
          "url": "https://github.com/serjflint/saitenka/commit/d3daf87dbee8435456ddd2f21d7820d9cae2a0c2"
        },
        "date": 1786965697197,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.404165,
            "range": "3 replicas; min 6.35446; max 6.45486; MAD 0.049709",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.334772,
            "range": "3 replicas; min 8.89771; max 10.7123; MAD 0.437063; worst 10.7123",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.748052,
            "range": "3 replicas; min 19.5567; max 19.8589; MAD 0.110845",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.156179,
            "range": "3 replicas; min 19.8983; max 20.2806; MAD 0.124436; worst 20.2806",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.168584,
            "range": "3 replicas; min 21.4718; max 22.9508; MAD 0.696824",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.170728,
            "range": "3 replicas; min 0.167953; max 0.204523; MAD 0.002775; worst 0.204523",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.866887,
            "range": "3 replicas; min 42.932; max 51.8209; MAD 1.9349; worst 51.8209",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.241638,
            "range": "3 replicas; min 2.87852; max 4.93738; MAD 0.363123; worst 4.93738",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.465016,
            "range": "3 replicas; min 1.41774; max 12.9166; MAD 0.047276; worst 12.9166",
            "unit": "ms"
          }
        ]
      },
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
          "id": "26668608da7d9972012979b2c46b22d46e52953b",
          "message": "Merge pull request #390 from serjflint/codex/runtime-architecture-docs\n\ndocs: describe current interactive runtime",
          "timestamp": "2026-08-17T17:09:25+05:00",
          "tree_id": "2a7b8b6de9ad2cf589922dcdfe90ae1574e49556",
          "url": "https://github.com/serjflint/saitenka/commit/26668608da7d9972012979b2c46b22d46e52953b"
        },
        "date": 1786968614871,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.032525,
            "range": "3 replicas; min 4.93189; max 6.3695; MAD 0.100639",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.372694,
            "range": "3 replicas; min 7.04152; max 11.0385; MAD 0.331173; worst 11.0385",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 14.506093,
            "range": "3 replicas; min 13.8717; max 19.6352; MAD 0.634405",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 15.428063,
            "range": "3 replicas; min 13.9713; max 20.0501; MAD 1.45672; worst 20.0501",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.525199,
            "range": "3 replicas; min 22.0175; max 31.7433; MAD 0.507666",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.12024,
            "range": "3 replicas; min 0.102513; max 0.168746; MAD 0.017727; worst 0.168746",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 46.88126,
            "range": "3 replicas; min 37.5346; max 260.36; MAD 9.34667; worst 260.36",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 14.45695,
            "range": "3 replicas; min 6.48843; max 20.613; MAD 6.1561; worst 20.613",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 3.322406,
            "range": "3 replicas; min 1.50981; max 6.69412; MAD 1.8126; worst 6.69412",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8c662bd898fcd3cf0932e35670ea9162e32799e7",
          "message": "Merge pull request #392 from serjflint/codex/runtime-migration-manifests\n\ntest(runtime): lock migration debt and lifecycle duties",
          "timestamp": "2026-08-17T21:48:00+05:00",
          "tree_id": "25ad86b440f4f524a68552d3b946c824d630259f",
          "url": "https://github.com/serjflint/saitenka/commit/8c662bd898fcd3cf0932e35670ea9162e32799e7"
        },
        "date": 1786985350383,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.428993,
            "range": "3 replicas; min 6.33876; max 6.71962; MAD 0.090232",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.480153,
            "range": "3 replicas; min 9.45989; max 15.9425; MAD 0.020259; worst 15.9425",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.311907,
            "range": "3 replicas; min 17.9067; max 19.466; MAD 0.154075",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.520276,
            "range": "3 replicas; min 18.0948; max 19.559; MAD 0.038706; worst 19.559",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.194443,
            "range": "3 replicas; min 18.7405; max 19.2124; MAD 0.017938",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167505,
            "range": "3 replicas; min 0.13291; max 0.168945; MAD 0.00144; worst 0.168945",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.663948,
            "range": "3 replicas; min 40.9615; max 44.4614; MAD 0.797405; worst 44.4614",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.963534,
            "range": "3 replicas; min 2.78558; max 3.47993; MAD 0.177959; worst 3.47993",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.214026,
            "range": "3 replicas; min 1.18379; max 1.33178; MAD 0.030236; worst 1.33178",
            "unit": "ms"
          }
        ]
      },
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
          "id": "46eb86c18790ca539c08900878b23b76c7b45fc6",
          "message": "Merge pull request #393 from serjflint/codex/runtime-lifecycle-slices\n\nrefactor(runtime): route startup hint through typed effects",
          "timestamp": "2026-08-17T22:35:03+05:00",
          "tree_id": "470189c59a1a042d2a4c3f70be692f82d05f3263",
          "url": "https://github.com/serjflint/saitenka/commit/46eb86c18790ca539c08900878b23b76c7b45fc6"
        },
        "date": 1786988155862,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.960617,
            "range": "3 replicas; min 4.95751; max 6.37929; MAD 0.003107",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.626579,
            "range": "3 replicas; min 7.00282; max 9.24484; MAD 0.623764; worst 9.24484",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 14.149615,
            "range": "3 replicas; min 13.9916; max 19.6453; MAD 0.158065",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 15.887467,
            "range": "3 replicas; min 15.7654; max 29.8459; MAD 0.122051; worst 29.8459",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.682364,
            "range": "3 replicas; min 19.72; max 97.2728; MAD 0.962401",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.101373,
            "range": "3 replicas; min 0.094487; max 0.165679; MAD 0.006886; worst 0.165679",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 143.144694,
            "range": "3 replicas; min 42.9805; max 175.469; MAD 32.3239; worst 175.469",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.806677,
            "range": "3 replicas; min 2.40584; max 9.11263; MAD 0.400836; worst 9.11263",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.537415,
            "range": "3 replicas; min 1.29952; max 2.25469; MAD 0.237896; worst 2.25469",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c0c16569e2b7aa4c6ca25f92ce0d7b4996056c05",
          "message": "Merge pull request #394 from serjflint/codex/runtime-command-dispatch\n\nrefactor(runtime): route commands through typed policy",
          "timestamp": "2026-08-18T00:04:32+05:00",
          "tree_id": "50cd2460527c4609791fb06ab421b09afcff43f6",
          "url": "https://github.com/serjflint/saitenka/commit/c0c16569e2b7aa4c6ca25f92ce0d7b4996056c05"
        },
        "date": 1786993518081,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.321849,
            "range": "3 replicas; min 6.30666; max 6.3798; MAD 0.015192",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.941087,
            "range": "3 replicas; min 8.90741; max 12.9201; MAD 0.033681; worst 12.9201",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.574974,
            "range": "3 replicas; min 18.1218; max 19.8027; MAD 0.227712",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.840922,
            "range": "3 replicas; min 18.4198; max 20.1046; MAD 0.263679; worst 20.1046",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.885807,
            "range": "3 replicas; min 18.7738; max 23.6828; MAD 0.796991",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.17105,
            "range": "3 replicas; min 0.137526; max 0.172835; MAD 0.001785; worst 0.172835",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.683061,
            "range": "3 replicas; min 37.8817; max 103.571; MAD 6.80133; worst 103.571",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.794927,
            "range": "3 replicas; min 2.41175; max 2.87753; MAD 0.082602; worst 2.87753",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.51044,
            "range": "3 replicas; min 1.10104; max 1.73925; MAD 0.228808; worst 1.73925",
            "unit": "ms"
          }
        ]
      },
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
          "id": "855dfdf0fa08fbe4bafd9cf1aaee170b707465a7",
          "message": "refactor(runtime): fence lifecycle surface revisions (#395)",
          "timestamp": "2026-08-18T00:38:21+05:00",
          "tree_id": "330d34a01ea68398b445db3160b5b06383bd6860",
          "url": "https://github.com/serjflint/saitenka/commit/855dfdf0fa08fbe4bafd9cf1aaee170b707465a7"
        },
        "date": 1786995546977,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.394473,
            "range": "3 replicas; min 6.33568; max 6.58552; MAD 0.058789",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.956447,
            "range": "3 replicas; min 8.8544; max 9.6673; MAD 0.102045; worst 9.6673",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.841618,
            "range": "3 replicas; min 17.7699; max 17.9726; MAD 0.071702",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.111858,
            "range": "3 replicas; min 17.9371; max 18.1288; MAD 0.016949; worst 18.1288",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.978215,
            "range": "3 replicas; min 17.802; max 19.9127; MAD 0.176216",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.133979,
            "range": "3 replicas; min 0.129583; max 0.135109; MAD 0.00113; worst 0.135109",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.36845,
            "range": "3 replicas; min 38.3415; max 112.469; MAD 0.026997; worst 112.469",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.547959,
            "range": "3 replicas; min 2.53036; max 2.751; MAD 0.017601; worst 2.751",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.126093,
            "range": "3 replicas; min 1.0547; max 1.18031; MAD 0.054217; worst 1.18031",
            "unit": "ms"
          }
        ]
      },
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
          "id": "2241eaf7db51c2f6d810f198d03de36af086ed7a",
          "message": "refactor(runtime): drive lifecycle deadlines with named timers (#396)",
          "timestamp": "2026-08-18T01:05:19+05:00",
          "tree_id": "f66793444038de050030dbb9e92101b5cf32d90f",
          "url": "https://github.com/serjflint/saitenka/commit/2241eaf7db51c2f6d810f198d03de36af086ed7a"
        },
        "date": 1786997177981,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.448177,
            "range": "3 replicas; min 6.36103; max 6.61151; MAD 0.087143",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.082454,
            "range": "3 replicas; min 8.88739; max 10.0933; MAD 0.195064; worst 10.0933",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.990201,
            "range": "3 replicas; min 17.7107; max 18.0615; MAD 0.071346",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 21.398108,
            "range": "3 replicas; min 17.8566; max 22.0347; MAD 0.636627; worst 22.0347",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.127267,
            "range": "3 replicas; min 16.8309; max 20.5002; MAD 1.37298",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.134001,
            "range": "3 replicas; min 0.133088; max 0.135783; MAD 0.000913; worst 0.135783",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.926684,
            "range": "3 replicas; min 38.732; max 40.0017; MAD 0.194669; worst 40.0017",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.552781,
            "range": "3 replicas; min 2.50615; max 2.61165; MAD 0.046636; worst 2.61165",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.13178,
            "range": "3 replicas; min 1.03616; max 1.39931; MAD 0.095622; worst 1.39931",
            "unit": "ms"
          }
        ]
      },
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
          "id": "264cef4ec2da748bd3c1cbbaf47c10bcca390b6a",
          "message": "feat(runtime): own mpv reconnect lifecycle (#397)",
          "timestamp": "2026-08-18T01:47:45+05:00",
          "tree_id": "f5b35364886cbebdd6f87b2694d5f96f2535e8c1",
          "url": "https://github.com/serjflint/saitenka/commit/264cef4ec2da748bd3c1cbbaf47c10bcca390b6a"
        },
        "date": 1786999726575,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.905496,
            "range": "3 replicas; min 5.04809; max 6.38747; MAD 0.481973",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.306827,
            "range": "3 replicas; min 8.0514; max 8.93287; MAD 0.255431; worst 8.93287",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.143402,
            "range": "3 replicas; min 15.2218; max 17.9467; MAD 0.803276",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.443247,
            "range": "3 replicas; min 16.293; max 18.0771; MAD 0.633817; worst 18.0771",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 172.168579,
            "range": "3 replicas; min 17.9371; max 173.66; MAD 1.49179",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.114615,
            "range": "3 replicas; min 0.106443; max 0.13321; MAD 0.008172; worst 0.13321",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 53.154761,
            "range": "3 replicas; min 40.171; max 71.7254; MAD 12.9838; worst 71.7254",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 8.79702,
            "range": "3 replicas; min 3.05744; max 14.6681; MAD 5.73958; worst 14.6681",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 10.33099,
            "range": "3 replicas; min 1.16489; max 13.0869; MAD 2.75592; worst 13.0869",
            "unit": "ms"
          }
        ]
      },
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
          "id": "fcf0fc1426c05eb33e0f14535932ab358faf379b",
          "message": "feat(runtime): broker capability probes (#398)",
          "timestamp": "2026-08-18T02:13:52+05:00",
          "tree_id": "c728ecac3506d054d269972e966659c47d2e2a37",
          "url": "https://github.com/serjflint/saitenka/commit/fcf0fc1426c05eb33e0f14535932ab358faf379b"
        },
        "date": 1787001291280,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.321978,
            "range": "3 replicas; min 5.93745; max 7.38738; MAD 0.384528",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.83143,
            "range": "3 replicas; min 8.05691; max 10.5888; MAD 0.77452; worst 10.5888",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.385114,
            "range": "3 replicas; min 17.8058; max 20.7454; MAD 1.36033",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.647164,
            "range": "3 replicas; min 17.8993; max 21.2543; MAD 1.60719; worst 21.2543",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.75349,
            "range": "3 replicas; min 17.7023; max 21.9102; MAD 0.156754",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.165479,
            "range": "3 replicas; min 0.152967; max 0.167901; MAD 0.002422; worst 0.167901",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.370334,
            "range": "3 replicas; min 43.2714; max 44.8933; MAD 0.098958; worst 44.8933",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.086479,
            "range": "3 replicas; min 2.61606; max 3.10051; MAD 0.014035; worst 3.10051",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.118508,
            "range": "3 replicas; min 1.07912; max 1.45424; MAD 0.039387; worst 1.45424",
            "unit": "ms"
          }
        ]
      },
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
          "id": "93d56446e38922e53fa14cb1ebece2d8004060a6",
          "message": "refactor(runtime): broker mined-state seeding (#399)",
          "timestamp": "2026-08-18T09:27:08+05:00",
          "tree_id": "a319604b45767f0b078c4b3ad01991fe20964008",
          "url": "https://github.com/serjflint/saitenka/commit/93d56446e38922e53fa14cb1ebece2d8004060a6"
        },
        "date": 1787027277356,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.362093,
            "range": "3 replicas; min 6.33881; max 6.49026; MAD 0.023283",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.771536,
            "range": "3 replicas; min 8.75646; max 9.65812; MAD 0.015076; worst 9.65812",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.433922,
            "range": "3 replicas; min 19.3798; max 19.8743; MAD 0.054074",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.699209,
            "range": "3 replicas; min 19.624; max 22.7339; MAD 0.075174; worst 22.7339",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.231723,
            "range": "3 replicas; min 18.6364; max 22.9956; MAD 0.76387",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167231,
            "range": "3 replicas; min 0.166465; max 0.170339; MAD 0.000766; worst 0.170339",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.960927,
            "range": "3 replicas; min 44.2451; max 48.8812; MAD 1.71582; worst 48.8812",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.191466,
            "range": "3 replicas; min 3.06044; max 4.13693; MAD 0.131026; worst 4.13693",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.447947,
            "range": "3 replicas; min 1.4147; max 1.71131; MAD 0.033247; worst 1.71131",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4a913dd8ec97e53055d58ce2f7e106e68cea422e",
          "message": "refactor(runtime): broker episode analysis (#400)",
          "timestamp": "2026-08-18T09:47:57+05:00",
          "tree_id": "2db60973cc758db0522ef460ec78cc6f8fb04aed",
          "url": "https://github.com/serjflint/saitenka/commit/4a913dd8ec97e53055d58ce2f7e106e68cea422e"
        },
        "date": 1787028536257,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.409502,
            "range": "3 replicas; min 5.02821; max 6.47839; MAD 0.068892",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.219072,
            "range": "3 replicas; min 7.38416; max 9.44444; MAD 0.22537; worst 9.44444",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.987526,
            "range": "3 replicas; min 13.983; max 19.6812; MAD 1.69365",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.026698,
            "range": "3 replicas; min 14.8232; max 20.8274; MAD 0.800695; worst 20.8274",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.894024,
            "range": "3 replicas; min 15.7395; max 346.838; MAD 6.15451",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.155663,
            "range": "3 replicas; min 0.102423; max 0.186389; MAD 0.030726; worst 0.186389",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 47.006928,
            "range": "3 replicas; min 39.0189; max 116.444; MAD 7.988; worst 116.444",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 89.481208,
            "range": "3 replicas; min 3.90553; max 153.699; MAD 64.2178; worst 153.699",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.901828,
            "range": "3 replicas; min 1.2027; max 28.5016; MAD 0.699129; worst 28.5016",
            "unit": "ms"
          }
        ]
      },
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
          "id": "fe84b598d3d849e750d4e331306c9e197e396a3d",
          "message": "refactor(runtime): broker hover metadata (#401)",
          "timestamp": "2026-08-18T10:06:38+05:00",
          "tree_id": "68b77742172054d955728d1abcba08f02ac76d90",
          "url": "https://github.com/serjflint/saitenka/commit/fe84b598d3d849e750d4e331306c9e197e396a3d"
        },
        "date": 1787029647558,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.334156,
            "range": "3 replicas; min 5.90958; max 6.34795; MAD 0.013796",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.942955,
            "range": "3 replicas; min 8.89639; max 9.19341; MAD 0.046569; worst 9.19341",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.703956,
            "range": "3 replicas; min 16.4678; max 17.7942; MAD 0.090206",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.944447,
            "range": "3 replicas; min 16.6334; max 17.9937; MAD 0.049241; worst 17.9937",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.321165,
            "range": "3 replicas; min 16.6304; max 27.9741; MAD 1.6908",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.131548,
            "range": "3 replicas; min 0.11205; max 0.134911; MAD 0.003363; worst 0.134911",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.075857,
            "range": "3 replicas; min 38.9725; max 189.214; MAD 0.103352; worst 189.214",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.713973,
            "range": "3 replicas; min 2.70004; max 133.961; MAD 0.013932; worst 133.961",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.910447,
            "range": "3 replicas; min 1.18571; max 7.6018; MAD 0.724739; worst 7.6018",
            "unit": "ms"
          }
        ]
      },
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
          "id": "df1b5405187704cf10eeb5a2b9abc558af303121",
          "message": "refactor(runtime): broker subtitle acquisition (#402)",
          "timestamp": "2026-08-18T10:40:49+05:00",
          "tree_id": "4a5b42f59e24d1db2f78345b12e5e9c754e33f4c",
          "url": "https://github.com/serjflint/saitenka/commit/df1b5405187704cf10eeb5a2b9abc558af303121"
        },
        "date": 1787031695858,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.333933,
            "range": "3 replicas; min 4.97276; max 6.38874; MAD 0.054809",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.279463,
            "range": "3 replicas; min 7.06398; max 9.3381; MAD 0.058641; worst 9.3381",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.886091,
            "range": "3 replicas; min 14.2881; max 19.541; MAD 1.65493",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.877778,
            "range": "3 replicas; min 14.5209; max 20.9603; MAD 1.08253; worst 20.9603",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.8443,
            "range": "3 replicas; min 15.6553; max 28.225; MAD 5.38065",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.140589,
            "range": "3 replicas; min 0.125277; max 0.168666; MAD 0.015312; worst 0.168666",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.942154,
            "range": "3 replicas; min 35.7789; max 64.7917; MAD 4.16321; worst 64.7917",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.22813,
            "range": "3 replicas; min 2.60152; max 3.41909; MAD 0.190962; worst 3.41909",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.498917,
            "range": "3 replicas; min 0.971139; max 1.62392; MAD 0.125007; worst 1.62392",
            "unit": "ms"
          }
        ]
      },
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
          "id": "7bb06a243059beda8db56c7e496535b74de69e9b",
          "message": "refactor(runtime): broker cue annotation (#403)",
          "timestamp": "2026-08-18T11:26:29+05:00",
          "tree_id": "284a0ca57b5f1251ed9ac7d83954c6f333372f1b",
          "url": "https://github.com/serjflint/saitenka/commit/7bb06a243059beda8db56c7e496535b74de69e9b"
        },
        "date": 1787034441628,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.475852,
            "range": "3 replicas; min 6.40061; max 6.50189; MAD 0.026042",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.712958,
            "range": "3 replicas; min 9.61934; max 12.2204; MAD 0.093621; worst 12.2204",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.350406,
            "range": "3 replicas; min 18.1098; max 19.6531; MAD 0.302734",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.885026,
            "range": "3 replicas; min 18.2705; max 19.8992; MAD 0.014125; worst 19.8992",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 23.952539,
            "range": "3 replicas; min 19.0697; max 24.0387; MAD 0.086199",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.171951,
            "range": "3 replicas; min 0.17074; max 0.176482; MAD 0.001211; worst 0.176482",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 69.643599,
            "range": "3 replicas; min 44.4242; max 111.304; MAD 25.2194; worst 111.304",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.20098,
            "range": "3 replicas; min 2.67716; max 4.78085; MAD 0.523822; worst 4.78085",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.311013,
            "range": "3 replicas; min 1.10783; max 1.83906; MAD 0.203187; worst 1.83906",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a7352c425b59c6ba6335d0201fc30dbfaa989ec9",
          "message": "refactor(runtime): broker tooltip raster work (#404)",
          "timestamp": "2026-08-18T12:05:25+05:00",
          "tree_id": "c436dfb29e83516e9eb01dd4fe179f785f96edd1",
          "url": "https://github.com/serjflint/saitenka/commit/a7352c425b59c6ba6335d0201fc30dbfaa989ec9"
        },
        "date": 1787036781483,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.432891,
            "range": "3 replicas; min 4.31846; max 6.5154; MAD 0.082507",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.083851,
            "range": "3 replicas; min 6.00657; max 9.56355; MAD 0.4797; worst 9.56355",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.538047,
            "range": "3 replicas; min 12.1668; max 19.9106; MAD 0.372594",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.151583,
            "range": "3 replicas; min 12.3273; max 20.5296; MAD 0.377969; worst 20.5296",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.780095,
            "range": "3 replicas; min 21.0503; max 359.632; MAD 1.72975",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.172362,
            "range": "3 replicas; min 0.084752; max 0.173225; MAD 0.000863; worst 0.173225",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.365785,
            "range": "3 replicas; min 43.8723; max 111.724; MAD 1.49349; worst 111.724",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.392714,
            "range": "3 replicas; min 3.36552; max 144.967; MAD 0.027189; worst 144.967",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.552352,
            "range": "3 replicas; min 1.47567; max 418.487; MAD 0.076682; worst 418.487",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3ca29c3d2b9311fe46a9dcae634ce546990796db",
          "message": "refactor(runtime): broker engaged tooltip work (#405)",
          "timestamp": "2026-08-18T13:16:47+05:00",
          "tree_id": "c75a85f87b0824106739e9e35dd905df3ba46dad",
          "url": "https://github.com/serjflint/saitenka/commit/3ca29c3d2b9311fe46a9dcae634ce546990796db"
        },
        "date": 1787041058357,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.374014,
            "range": "3 replicas; min 6.35855; max 6.39363; MAD 0.015469",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.964623,
            "range": "3 replicas; min 8.84336; max 9.50684; MAD 0.121265; worst 9.50684",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.608379,
            "range": "3 replicas; min 17.8216; max 19.8471; MAD 0.238764",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.835694,
            "range": "3 replicas; min 18.211; max 19.9642; MAD 0.128519; worst 19.9642",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 27.676752,
            "range": "3 replicas; min 18.5263; max 60.713; MAD 9.15043",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.16627,
            "range": "3 replicas; min 0.140391; max 0.169467; MAD 0.003197; worst 0.169467",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.187992,
            "range": "3 replicas; min 39.2555; max 109.731; MAD 3.93245; worst 109.731",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.341729,
            "range": "3 replicas; min 2.70481; max 3.42305; MAD 0.08132; worst 3.42305",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.670409,
            "range": "3 replicas; min 1.10533; max 1.70953; MAD 0.039123; worst 1.70953",
            "unit": "ms"
          }
        ]
      },
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
          "id": "fef1db44eaadb5f9515e50e21e881a9e2dbbe5ae",
          "message": "Merge pull request #406 from serjflint/codex/runtime-speculative-prefetch\n\nrefactor(runtime): broker speculative prefetch",
          "timestamp": "2026-08-18T14:56:14+05:00",
          "tree_id": "107368acbe0cc96f687e70fc90e8bfbf55cf83c2",
          "url": "https://github.com/serjflint/saitenka/commit/fef1db44eaadb5f9515e50e21e881a9e2dbbe5ae"
        },
        "date": 1787047023231,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.391794,
            "range": "3 replicas; min 6.36053; max 6.40694; MAD 0.01515",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.132683,
            "range": "3 replicas; min 8.79415; max 11.0704; MAD 0.338529; worst 11.0704",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 19.27048,
            "range": "3 replicas; min 17.8087; max 19.4752; MAD 0.204761",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.123917,
            "range": "3 replicas; min 18.3529; max 20.6477; MAD 0.523807; worst 20.6477",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.168283,
            "range": "3 replicas; min 15.7494; max 23.2292; MAD 2.06094",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.166351,
            "range": "3 replicas; min 0.132499; max 0.168246; MAD 0.001895; worst 0.168246",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 62.803485,
            "range": "3 replicas; min 43.4998; max 83.7211; MAD 19.3037; worst 83.7211",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 4.094389,
            "range": "3 replicas; min 3.36755; max 91.7868; MAD 0.726838; worst 91.7868",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.426643,
            "range": "3 replicas; min 1.20508; max 1.66866; MAD 0.221558; worst 1.66866",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c385f4167c0c24b2e404ef988f1d30cbd8dc1ce5",
          "message": "Merge pull request #407 from serjflint/codex/runtime-mask-atlas-startup\n\nrefactor(runtime): broker mask atlas startup",
          "timestamp": "2026-08-18T15:15:02+05:00",
          "tree_id": "cdfb11bdad805b3f74eb7b7ef4105eb54cf77b75",
          "url": "https://github.com/serjflint/saitenka/commit/c385f4167c0c24b2e404ef988f1d30cbd8dc1ce5"
        },
        "date": 1787048152639,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.275365,
            "range": "3 replicas; min 5.58091; max 6.33515; MAD 0.059786",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.688195,
            "range": "3 replicas; min 8.05603; max 10.3351; MAD 0.632162; worst 10.3351",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.779135,
            "range": "3 replicas; min 14.1552; max 19.6418; MAD 1.86264",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.588252,
            "range": "3 replicas; min 14.2558; max 23.3804; MAD 4.33242; worst 23.3804",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.351647,
            "range": "3 replicas; min 18.0155; max 25.5211; MAD 0.336099",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.131075,
            "range": "3 replicas; min 0.106038; max 0.167233; MAD 0.025037; worst 0.167233",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 47.448459,
            "range": "3 replicas; min 38.8028; max 134.089; MAD 8.64566; worst 134.089",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.350056,
            "range": "3 replicas; min 2.59919; max 3.49135; MAD 0.141294; worst 3.49135",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.412766,
            "range": "3 replicas; min 1.21506; max 2.42963; MAD 0.197704; worst 2.42963",
            "unit": "ms"
          }
        ]
      },
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
          "id": "83c8fc75dd7fd05f942a84d54c78d199f6b4df42",
          "message": "Merge pull request #408 from serjflint/codex/runtime-wp4-playback\n\nrefactor(runtime): drive the session from the event runtime (WP4–WP6, D1–D3)",
          "timestamp": "2026-08-22T22:00:30+05:00",
          "tree_id": "2244abf979cf9e96136deb7dea25e63111cddead",
          "url": "https://github.com/serjflint/saitenka/commit/83c8fc75dd7fd05f942a84d54c78d199f6b4df42"
        },
        "date": 1787418109060,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.936365,
            "range": "3 replicas; min 4.93258; max 6.36733; MAD 0.003785",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.063967,
            "range": "3 replicas; min 6.95417; max 9.90652; MAD 0.109796; worst 9.90652",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 14.953634,
            "range": "3 replicas; min 14.5667; max 18.6994; MAD 0.38691",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 15.166931,
            "range": "3 replicas; min 14.6586; max 18.8351; MAD 0.508335; worst 18.8351",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.193236,
            "range": "3 replicas; min 17.5172; max 19.5354; MAD 0.342179",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.103063,
            "range": "3 replicas; min 0.101112; max 0.130587; MAD 0.001951; worst 0.130587",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 31.527079,
            "range": "3 replicas; min 30.6663; max 39.7456; MAD 0.860796; worst 39.7456",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.741426,
            "range": "3 replicas; min 2.68849; max 7.19143; MAD 0.052933; worst 7.19143",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.193962,
            "range": "3 replicas; min 1.16241; max 1.39446; MAD 0.031552; worst 1.39446",
            "unit": "ms"
          }
        ]
      },
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
          "id": "9a227494fa0f753e36efed5e9f5f5f956a3b33b1",
          "message": "Merge pull request #413 from serjflint/fix/ci-runner-context\n\nfix(ci): stop using the runner context in a job-level env",
          "timestamp": "2026-08-23T01:37:46+05:00",
          "tree_id": "d22c2cef17af9fc88a607184e9a8b9d7cd385aca",
          "url": "https://github.com/serjflint/saitenka/commit/9a227494fa0f753e36efed5e9f5f5f956a3b33b1"
        },
        "date": 1787431134457,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.40695,
            "range": "3 replicas; min 6.36185; max 6.55789; MAD 0.045104",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.135636,
            "range": "3 replicas; min 8.74576; max 9.25382; MAD 0.118185; worst 9.25382",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.638078,
            "range": "3 replicas; min 18.5614; max 18.8347; MAD 0.076716",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.019058,
            "range": "3 replicas; min 18.8132; max 20.0411; MAD 0.205862; worst 20.0411",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.002441,
            "range": "3 replicas; min 16.0399; max 17.8104; MAD 0.808003",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.131464,
            "range": "3 replicas; min 0.129325; max 0.133338; MAD 0.001874; worst 0.133338",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.92069,
            "range": "3 replicas; min 38.7591; max 43.2576; MAD 0.161589; worst 43.2576",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.555779,
            "range": "3 replicas; min 2.47747; max 2.78533; MAD 0.078305; worst 2.78533",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.098046,
            "range": "3 replicas; min 0.982137; max 1.13492; MAD 0.036876; worst 1.13492",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b89603b66e8387ac535a7fce349c91e5643661de",
          "message": "Merge pull request #414 from serjflint/fix/probe-race-and-bench-output\n\nfix: a probe test that raced itself, and a benchmark that buried its own verdict",
          "timestamp": "2026-08-23T12:22:03+05:00",
          "tree_id": "274e9c9093eca2e6421c63c58ca474b041a64141",
          "url": "https://github.com/serjflint/saitenka/commit/b89603b66e8387ac535a7fce349c91e5643661de"
        },
        "date": 1787469792011,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.379047,
            "range": "3 replicas; min 4.97775; max 6.88309; MAD 0.504041",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.473669,
            "range": "3 replicas; min 7.65828; max 9.95553; MAD 0.481862; worst 9.95553",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.333058,
            "range": "3 replicas; min 15.6706; max 21.2176; MAD 0.884527",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.553451,
            "range": "3 replicas; min 17.0835; max 21.6328; MAD 1.07931; worst 21.6328",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 25.39486,
            "range": "3 replicas; min 23.9896; max 29.0325; MAD 1.40529",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.168015,
            "range": "3 replicas; min 0.107374; max 0.176291; MAD 0.008276; worst 0.176291",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 47.636706,
            "range": "3 replicas; min 44.2478; max 149.544; MAD 3.38887; worst 149.544",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.906735,
            "range": "3 replicas; min 3.62067; max 88.0741; MAD 0.286063; worst 88.0741",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.515734,
            "range": "3 replicas; min 1.27201; max 1.5936; MAD 0.077868; worst 1.5936",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c31c1d9f316343cf9195fe3d3b758dde2e7afe35",
          "message": "Merge pull request #416 from serjflint/feat/one-subtitle-engine\n\nfeat(subtitles): colour mpv's own subtitle glyphs, and take the tracks it converts",
          "timestamp": "2026-08-24T04:40:03+05:00",
          "tree_id": "9f029ac47cac292352e127e8fb0ccb424bb804c5",
          "url": "https://github.com/serjflint/saitenka/commit/c31c1d9f316343cf9195fe3d3b758dde2e7afe35"
        },
        "date": 1787528553880,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.434116,
            "range": "3 replicas; min 4.99195; max 6.43593; MAD 0.001815",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.82269,
            "range": "3 replicas; min 7.35027; max 9.26506; MAD 0.442368; worst 9.26506",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.347409,
            "range": "3 replicas; min 15.1165; max 20.7178; MAD 0.370402",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.661254,
            "range": "3 replicas; min 15.252; max 21.0517; MAD 0.390411; worst 21.0517",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 23.977669,
            "range": "3 replicas; min 17.9388; max 27.2596; MAD 3.28192",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.17162,
            "range": "3 replicas; min 0.097128; max 0.172101; MAD 0.000481; worst 0.172101",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.44646,
            "range": "3 replicas; min 43.9184; max 46.0142; MAD 0.52809; worst 46.0142",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.417699,
            "range": "3 replicas; min 3.40679; max 3.52556; MAD 0.010912; worst 3.52556",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.793562,
            "range": "3 replicas; min 1.55252; max 2.02243; MAD 0.228869; worst 2.02243",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a3ad93abcc4f26b9548a585b52742aa9a9f83577",
          "message": "Merge pull request #419 from serjflint/chore/release-4.0.0\n\nchore(overlay): release 4.0.0",
          "timestamp": "2026-08-24T05:15:14+05:00",
          "tree_id": "b27a2ee638ff8d334e40b26ae613575326b9d942",
          "url": "https://github.com/serjflint/saitenka/commit/a3ad93abcc4f26b9548a585b52742aa9a9f83577"
        },
        "date": 1787530695227,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 4.878756,
            "range": "3 replicas; min 3.45043; max 6.37156; MAD 1.42833",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.954352,
            "range": "3 replicas; min 5.36111; max 10.5467; MAD 1.59324; worst 10.5467",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 14.371642,
            "range": "3 replicas; min 9.83261; max 20.3471; MAD 4.53904",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 14.60547,
            "range": "3 replicas; min 9.89563; max 20.7099; MAD 4.70984; worst 20.7099",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.635931,
            "range": "3 replicas; min 16.8736; max 23.1969; MAD 2.76229",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.105227,
            "range": "3 replicas; min 0.074333; max 0.16533; MAD 0.030894; worst 0.16533",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.983012,
            "range": "3 replicas; min 19.7512; max 52.3018; MAD 8.3188; worst 52.3018",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.421576,
            "range": "3 replicas; min 2.34436; max 3.62652; MAD 0.204947; worst 3.62652",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.644188,
            "range": "3 replicas; min 1.05087; max 2.17209; MAD 0.527902; worst 2.17209",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3a87dd0d7573e1bc581a7fc63fca260672b75afb",
          "message": "Merge pull request #420 from serjflint/fix/install-editable-bakes-the-tool-binary\n\nfix(plugin): bake the saitenka that is asking, not the one on PATH",
          "timestamp": "2026-08-24T10:25:00+05:00",
          "tree_id": "a1ac4124a5fe0d29b834564eb70808adf7a839dc",
          "url": "https://github.com/serjflint/saitenka/commit/3a87dd0d7573e1bc581a7fc63fca260672b75afb"
        },
        "date": 1787549171050,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.497344,
            "range": "3 replicas; min 5.15459; max 6.79482; MAD 0.297477",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.250595,
            "range": "3 replicas; min 7.67087; max 9.50061; MAD 0.250011; worst 9.50061",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.718662,
            "range": "3 replicas; min 15.795; max 20.9035; MAD 0.184885",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.989617,
            "range": "3 replicas; min 16.7725; max 21.2603; MAD 0.270699; worst 21.2603",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.177947,
            "range": "3 replicas; min 20.4935; max 192.137; MAD 1.68445",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.173013,
            "range": "3 replicas; min 0.106281; max 0.17607; MAD 0.003057; worst 0.17607",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.373755,
            "range": "3 replicas; min 44.3318; max 121.912; MAD 0.041987; worst 121.912",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.993385,
            "range": "3 replicas; min 3.42378; max 20.8071; MAD 0.569601; worst 20.8071",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.690888,
            "range": "3 replicas; min 1.38437; max 29.4685; MAD 0.306514; worst 29.4685",
            "unit": "ms"
          }
        ]
      },
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
          "id": "663188e33e1e8ec4ba4142a5a295c9637b4dc006",
          "message": "Merge pull request #421 from serjflint/refactor/tooltip-controller-pilot\n\nrefactor(app): give tooltip work a bounded owner",
          "timestamp": "2026-08-24T10:37:57+05:00",
          "tree_id": "e92f8e783a60b614a9f43b6c7a469eec30c1602a",
          "url": "https://github.com/serjflint/saitenka/commit/663188e33e1e8ec4ba4142a5a295c9637b4dc006"
        },
        "date": 1787549940624,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.376927,
            "range": "3 replicas; min 5.75229; max 6.40752; MAD 0.030591",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.824204,
            "range": "3 replicas; min 8.52722; max 8.99669; MAD 0.172489; worst 8.99669",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.55353,
            "range": "3 replicas; min 17.4784; max 20.6123; MAD 0.058774",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.703953,
            "range": "3 replicas; min 17.7391; max 20.9349; MAD 0.230963; worst 20.9349",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.727663,
            "range": "3 replicas; min 18.9954; max 23.2839; MAD 0.556221",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.169355,
            "range": "3 replicas; min 0.111509; max 0.169818; MAD 0.000463; worst 0.169818",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.716959,
            "range": "3 replicas; min 42.7414; max 43.7351; MAD 0.018111; worst 43.7351",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.379772,
            "range": "3 replicas; min 3.22642; max 4.27613; MAD 0.153356; worst 4.27613",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.589523,
            "range": "3 replicas; min 1.50908; max 3.14687; MAD 0.080439; worst 3.14687",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5a4aba0fb881bff4e0da9efb952ccd2e7efb2e7f",
          "message": "Merge pull request #423 from serjflint/fix/native-integration-tail-budget\n\nGive the interaction CPU delta the headroom a tail metric needs",
          "timestamp": "2026-08-24T12:20:20+05:00",
          "tree_id": "2beb7881db2e0ec799d4532b5ceb004a10737d6a",
          "url": "https://github.com/serjflint/saitenka/commit/5a4aba0fb881bff4e0da9efb952ccd2e7efb2e7f"
        },
        "date": 1787556188521,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.383071,
            "range": "3 replicas; min 6.37597; max 6.39618; MAD 0.007101",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.911967,
            "range": "3 replicas; min 8.82115; max 11.8405; MAD 0.090818; worst 11.8405",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.527561,
            "range": "3 replicas; min 18.6767; max 20.8824; MAD 0.354831",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.752063,
            "range": "3 replicas; min 18.7858; max 21.1874; MAD 0.435294; worst 21.1874",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.689547,
            "range": "3 replicas; min 15.968; max 20.9768; MAD 1.28725",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.17131,
            "range": "3 replicas; min 0.131699; max 0.177232; MAD 0.005922; worst 0.177232",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.073398,
            "range": "3 replicas; min 38.5268; max 44.2113; MAD 0.137886; worst 44.2113",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.556422,
            "range": "3 replicas; min 2.50879; max 4.95937; MAD 1.04763; worst 4.95937",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.508797,
            "range": "3 replicas; min 1.07736; max 2.21491; MAD 0.43144; worst 2.21491",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d09306d9584826e3d7b7b23764c64df5864e6252",
          "message": "Merge pull request #422 from serjflint/chore/gate-speed-and-noise\n\nCut poe all from 7 minutes and 7877 lines",
          "timestamp": "2026-08-24T12:21:00+05:00",
          "tree_id": "60a434ad04319c725f7312ac3abab9fd826db12a",
          "url": "https://github.com/serjflint/saitenka/commit/d09306d9584826e3d7b7b23764c64df5864e6252"
        },
        "date": 1787556207857,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.323259,
            "range": "3 replicas; min 6.28414; max 6.38035; MAD 0.039115",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.668226,
            "range": "3 replicas; min 8.62287; max 8.91662; MAD 0.045358; worst 8.91662",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.345221,
            "range": "3 replicas; min 18.6383; max 20.5364; MAD 0.191219",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.601939,
            "range": "3 replicas; min 18.9472; max 20.8063; MAD 0.204397; worst 20.8063",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.877895,
            "range": "3 replicas; min 16.7931; max 21.3467; MAD 0.468773",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167232,
            "range": "3 replicas; min 0.131097; max 0.168045; MAD 0.000813; worst 0.168045",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.822812,
            "range": "3 replicas; min 39.1137; max 43.3989; MAD 0.576083; worst 43.3989",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.860307,
            "range": "3 replicas; min 2.47817; max 3.00223; MAD 0.141927; worst 3.00223",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.212924,
            "range": "3 replicas; min 1.01604; max 1.6266; MAD 0.196889; worst 1.6266",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c5f5d18707286654a002233400b80331423bd234",
          "message": "Merge pull request #424 from serjflint/docs/architecture-inquiry-skill\n\nfeat(skills): add architecture inquiry workflow",
          "timestamp": "2026-08-24T12:36:40+05:00",
          "tree_id": "9b597f1d2d914781361b5142bfc0c54a79de88cc",
          "url": "https://github.com/serjflint/saitenka/commit/c5f5d18707286654a002233400b80331423bd234"
        },
        "date": 1787557125073,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.26012,
            "range": "3 replicas; min 5.12257; max 6.3694; MAD 0.109284",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.615472,
            "range": "3 replicas; min 7.68912; max 9.37782; MAD 0.762344; worst 9.37782",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.363617,
            "range": "3 replicas; min 15.1076; max 20.4708; MAD 0.107205",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.575844,
            "range": "3 replicas; min 16.647; max 20.8086; MAD 0.232788; worst 20.8086",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.365649,
            "range": "3 replicas; min 17.7911; max 25.2928; MAD 1.57453",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.166712,
            "range": "3 replicas; min 0.102319; max 0.168485; MAD 0.001773; worst 0.168485",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.832362,
            "range": "3 replicas; min 39.7951; max 44.0428; MAD 1.21042; worst 44.0428",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.295998,
            "range": "3 replicas; min 3.15042; max 3.43186; MAD 0.135864; worst 3.43186",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.377076,
            "range": "3 replicas; min 1.29502; max 1.62422; MAD 0.082059; worst 1.62422",
            "unit": "ms"
          }
        ]
      },
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
          "id": "784fa060446d3aa81c06b50446207de73ca1f274",
          "message": "Merge pull request #425 from serjflint/chore/runtime-console-log\n\nLog the runtime's two user-facing lines instead of printing them",
          "timestamp": "2026-08-24T12:37:41+05:00",
          "tree_id": "2a16f4d76e20069018a8a20ffb844184d76b9ddb",
          "url": "https://github.com/serjflint/saitenka/commit/784fa060446d3aa81c06b50446207de73ca1f274"
        },
        "date": 1787557144196,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.327074,
            "range": "3 replicas; min 6.29778; max 6.37854; MAD 0.029292",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.894919,
            "range": "3 replicas; min 8.67616; max 9.12893; MAD 0.218755; worst 9.12893",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.512366,
            "range": "3 replicas; min 20.1364; max 20.5876; MAD 0.075238",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.68504,
            "range": "3 replicas; min 20.4731; max 20.859; MAD 0.173922; worst 20.859",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 23.224821,
            "range": "3 replicas; min 17.5672; max 23.7936; MAD 0.568732",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.166591,
            "range": "3 replicas; min 0.16527; max 0.168956; MAD 0.001321; worst 0.168956",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.850638,
            "range": "3 replicas; min 43.1684; max 48.375; MAD 0.682239; worst 48.375",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.258696,
            "range": "3 replicas; min 2.70737; max 3.3502; MAD 0.091509; worst 3.3502",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.354328,
            "range": "3 replicas; min 1.24315; max 1.93117; MAD 0.111175; worst 1.93117",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ffae3004a7fb7a5d2a46eaee851ac3d490bdd862",
          "message": "Merge pull request #426 from serjflint/refactor/session-controller-rename\n\nrefactor(app): rename Reader to SessionController",
          "timestamp": "2026-08-24T18:03:26+05:00",
          "tree_id": "a7b57df1085065296cf4c3c2a34dfee1d865db99",
          "url": "https://github.com/serjflint/saitenka/commit/ffae3004a7fb7a5d2a46eaee851ac3d490bdd862"
        },
        "date": 1787576771571,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.742572,
            "range": "3 replicas; min 4.95972; max 6.40456; MAD 0.661987",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.297621,
            "range": "3 replicas; min 7.54204; max 9.33563; MAD 0.755579; worst 9.33563",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.029999,
            "range": "3 replicas; min 14.7209; max 20.3469; MAD 2.30905",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.332214,
            "range": "3 replicas; min 15.6831; max 20.659; MAD 1.64911; worst 20.659",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 24.402847,
            "range": "3 replicas; min 18.7704; max 26.7069; MAD 2.30405",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.114823,
            "range": "3 replicas; min 0.097651; max 0.167518; MAD 0.017172; worst 0.167518",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.39303,
            "range": "3 replicas; min 37.1067; max 152.753; MAD 7.2863; worst 152.753",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.647205,
            "range": "3 replicas; min 2.9103; max 6.33803; MAD 0.736904; worst 6.33803",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.33986,
            "range": "3 replicas; min 1.23563; max 1.65978; MAD 0.104228; worst 1.65978",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d61af686f1171f06acff39718ad927a3d9a5949a",
          "message": "Merge pull request #427 from serjflint/refactor/profile-controller\n\nrefactor(app): give reading profiles a bounded controller",
          "timestamp": "2026-08-24T18:38:24+05:00",
          "tree_id": "51d02eceb3e22684d205c68f13e8626f76b69d8f",
          "url": "https://github.com/serjflint/saitenka/commit/d61af686f1171f06acff39718ad927a3d9a5949a"
        },
        "date": 1787578772931,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.310915,
            "range": "3 replicas; min 5.22939; max 6.35873; MAD 0.047813",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.956075,
            "range": "3 replicas; min 8.12096; max 8.98542; MAD 0.029347; worst 8.98542",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.487937,
            "range": "3 replicas; min 16.2697; max 18.603; MAD 0.11508",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.62304,
            "range": "3 replicas; min 17.1796; max 18.777; MAD 0.153957; worst 18.777",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.502156,
            "range": "3 replicas; min 19.0032; max 195.101; MAD 0.498984",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.131698,
            "range": "3 replicas; min 0.106831; max 0.134001; MAD 0.002303; worst 0.134001",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.242098,
            "range": "3 replicas; min 38.691; max 198.413; MAD 0.551126; worst 198.413",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.785892,
            "range": "3 replicas; min 2.53801; max 131.034; MAD 0.247881; worst 131.034",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.195379,
            "range": "3 replicas; min 1.11091; max 158.66; MAD 0.084469; worst 158.66",
            "unit": "ms"
          }
        ]
      },
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
          "id": "87055d330f0e9f9c22b7ae93d2860d4f5e3cdfd2",
          "message": "Merge pull request #428 from serjflint/chore/bind-gate-copies-and-store-wiring\n\nBind CI's gate copies, and assert each store is wired to the sqlite mechanism",
          "timestamp": "2026-08-24T18:50:28+05:00",
          "tree_id": "462a0924943e75cc78b60a6bedee58368c0b03fc",
          "url": "https://github.com/serjflint/saitenka/commit/87055d330f0e9f9c22b7ae93d2860d4f5e3cdfd2"
        },
        "date": 1787579499587,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.373775,
            "range": "3 replicas; min 6.29914; max 6.43352; MAD 0.059746",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.030419,
            "range": "3 replicas; min 8.82602; max 9.72113; MAD 0.2044; worst 9.72113",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.394442,
            "range": "3 replicas; min 18.7638; max 20.4561; MAD 0.061676",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.722774,
            "range": "3 replicas; min 18.8767; max 20.8116; MAD 0.08882; worst 20.8116",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 25.862982,
            "range": "3 replicas; min 15.8658; max 28.6214; MAD 2.75843",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167963,
            "range": "3 replicas; min 0.136994; max 0.172533; MAD 0.00457; worst 0.172533",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.235251,
            "range": "3 replicas; min 44.1611; max 71.2145; MAD 0.074154; worst 71.2145",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.293451,
            "range": "3 replicas; min 2.5093; max 4.55989; MAD 0.784149; worst 4.55989",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 2.244591,
            "range": "3 replicas; min 1.02114; max 2.5934; MAD 0.348807; worst 2.5934",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4dce381189dc0c89cd5c3efe4f9414406eeb0a84",
          "message": "Merge pull request #429 from serjflint/chore/full-extra-native-addons\n\nPull the portable native add-ons into saitenka[full]",
          "timestamp": "2026-08-24T20:17:36+05:00",
          "tree_id": "cfa6deb1d5ac0172f48e770af1544a4736462f01",
          "url": "https://github.com/serjflint/saitenka/commit/4dce381189dc0c89cd5c3efe4f9414406eeb0a84"
        },
        "date": 1787584789706,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.414711,
            "range": "3 replicas; min 6.3831; max 6.50895; MAD 0.031612",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.1939,
            "range": "3 replicas; min 9.04647; max 9.39952; MAD 0.147434; worst 9.39952",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.492497,
            "range": "3 replicas; min 18.4682; max 20.5025; MAD 0.010036",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.825588,
            "range": "3 replicas; min 18.6465; max 23.3543; MAD 2.17906; worst 23.3543",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.096231,
            "range": "3 replicas; min 16.0025; max 20.4477; MAD 0.351492",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.170338,
            "range": "3 replicas; min 0.130787; max 0.172372; MAD 0.002034; worst 0.172372",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.9159,
            "range": "3 replicas; min 39.3551; max 44.4222; MAD 0.506299; worst 44.4222",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.956942,
            "range": "3 replicas; min 2.42675; max 3.27651; MAD 0.31957; worst 3.27651",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.330502,
            "range": "3 replicas; min 0.921481; max 3.50714; MAD 0.409021; worst 3.50714",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b0d10c669d4f870c1614f1c4730fd37b0c94225a",
          "message": "Merge pull request #430 from serjflint/docs/session-controller-post-pilot-review\n\ndocs(architecture): record post-pilot controller review",
          "timestamp": "2026-08-24T23:49:37+05:00",
          "tree_id": "1a2aa269b6d42e52dbf51d8eb9b8d804ead09605",
          "url": "https://github.com/serjflint/saitenka/commit/b0d10c669d4f870c1614f1c4730fd37b0c94225a"
        },
        "date": 1787597461844,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.39121,
            "range": "3 replicas; min 6.34507; max 6.40182; MAD 0.010606",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.381406,
            "range": "3 replicas; min 8.90567; max 9.64008; MAD 0.258678; worst 9.64008",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.256092,
            "range": "3 replicas; min 18.4578; max 20.4216; MAD 0.16547",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.516159,
            "range": "3 replicas; min 18.9151; max 20.673; MAD 0.156842; worst 20.673",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.067295,
            "range": "3 replicas; min 15.7777; max 18.5101; MAD 0.442769",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.16527,
            "range": "3 replicas; min 0.133276; max 0.167523; MAD 0.002253; worst 0.167523",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.656357,
            "range": "3 replicas; min 39.2525; max 43.742; MAD 0.085595; worst 43.742",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.90556,
            "range": "3 replicas; min 2.50379; max 3.16738; MAD 0.261823; worst 3.16738",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.127485,
            "range": "3 replicas; min 1.0645; max 1.17071; MAD 0.043224; worst 1.17071",
            "unit": "ms"
          }
        ]
      },
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
          "id": "093861f18c2ca06aee45a8ceb161df83b807a4c3",
          "message": "Merge pull request #431 from serjflint/fix/ft-split-libass\n\nfix(ci): test PyPI libass bundle in FT jobs",
          "timestamp": "2026-08-25T00:57:58+05:00",
          "tree_id": "949f6bcca0da98fadedc379cc5bab1a7790210ea",
          "url": "https://github.com/serjflint/saitenka/commit/093861f18c2ca06aee45a8ceb161df83b807a4c3"
        },
        "date": 1787601569711,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.344023,
            "range": "3 replicas; min 6.33304; max 6.42835; MAD 0.010987",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.86892,
            "range": "3 replicas; min 8.8247; max 10.2038; MAD 0.044221; worst 10.2038",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.502815,
            "range": "3 replicas; min 18.4049; max 18.655; MAD 0.097898",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.706736,
            "range": "3 replicas; min 18.5329; max 18.7672; MAD 0.060443; worst 18.7672",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 15.061877,
            "range": "3 replicas; min 14.4527; max 15.2127; MAD 0.150818",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.132505,
            "range": "3 replicas; min 0.129281; max 0.134031; MAD 0.001526; worst 0.134031",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.871621,
            "range": "3 replicas; min 38.8261; max 299.85; MAD 0.045507; worst 299.85",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.441178,
            "range": "3 replicas; min 2.38238; max 2.50097; MAD 0.058796; worst 2.50097",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 0.918814,
            "range": "3 replicas; min 0.868003; max 0.928461; MAD 0.009647; worst 0.928461",
            "unit": "ms"
          }
        ]
      },
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
          "id": "2a38c97e941c629e7df97071131c6cc86f82d31e",
          "message": "Merge pull request #432 from serjflint/chore/resvg-py-switch\n\nfeat(images): install resvg-py instead of the in-tree resvglite",
          "timestamp": "2026-08-25T01:44:53+05:00",
          "tree_id": "1fa532df57d5ec431ad15dcfac77bbf08fa72242",
          "url": "https://github.com/serjflint/saitenka/commit/2a38c97e941c629e7df97071131c6cc86f82d31e"
        },
        "date": 1787604377486,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.290167,
            "range": "3 replicas; min 4.96919; max 6.33555; MAD 0.045387",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.658319,
            "range": "3 replicas; min 7.10842; max 8.85722; MAD 0.198902; worst 8.85722",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.405976,
            "range": "3 replicas; min 14.411; max 20.4309; MAD 2.02497",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.607969,
            "range": "3 replicas; min 14.5229; max 20.6622; MAD 2.05419; worst 20.6622",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.24566,
            "range": "3 replicas; min 16.8947; max 23.5007; MAD 1.35097",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.127465,
            "range": "3 replicas; min 0.102934; max 0.164968; MAD 0.024531; worst 0.164968",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.137534,
            "range": "3 replicas; min 37.2597; max 45.5651; MAD 1.42759; worst 45.5651",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.128161,
            "range": "3 replicas; min 2.64322; max 6.26132; MAD 0.484941; worst 6.26132",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.06721,
            "range": "3 replicas; min 0.895478; max 1.20966; MAD 0.142447; worst 1.20966",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b73b9a759e1c4f506e1c2fa04ca4ef2b3e7aa2ec",
          "message": "Merge pull request #433 from serjflint/chore/release-4.1.0\n\nchore(overlay): release 4.1.0",
          "timestamp": "2026-08-25T02:14:37+05:00",
          "tree_id": "16d7fa8cc975f0bd3d69b26dd8ddd32466df64e1",
          "url": "https://github.com/serjflint/saitenka/commit/b73b9a759e1c4f506e1c2fa04ca4ef2b3e7aa2ec"
        },
        "date": 1787606283337,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.157677,
            "range": "3 replicas; min 4.20767; max 6.46645; MAD 0.950012",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.531092,
            "range": "3 replicas; min 6.4744; max 9.28864; MAD 1.05669; worst 9.28864",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 15.406779,
            "range": "3 replicas; min 14.3802; max 20.3962; MAD 1.02655",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 16.508147,
            "range": "3 replicas; min 14.6079; max 20.6293; MAD 1.90022; worst 20.6293",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 41.162685,
            "range": "3 replicas; min 21.5871; max 351.016; MAD 19.5756",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.102424,
            "range": "3 replicas; min 0.088276; max 0.173221; MAD 0.014148; worst 0.173221",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.470486,
            "range": "3 replicas; min 41.7833; max 92.7046; MAD 2.68717; worst 92.7046",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 4.042659,
            "range": "3 replicas; min 3.61364; max 82.7419; MAD 0.429014; worst 82.7419",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 2.131857,
            "range": "3 replicas; min 1.23053; max 5.49061; MAD 0.901322; worst 5.49061",
            "unit": "ms"
          }
        ]
      },
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
          "id": "617d830aff54a491de4e003148753e6bc2115429",
          "message": "Merge pull request #434 from serjflint/refactor/mining-controller\n\nrefactor(mining): give session mining a bounded owner",
          "timestamp": "2026-08-25T09:56:55+05:00",
          "tree_id": "4b9e94c58dd589c3393bfd4a6f0b92859e9b1283",
          "url": "https://github.com/serjflint/saitenka/commit/617d830aff54a491de4e003148753e6bc2115429"
        },
        "date": 1787633894565,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.258405,
            "range": "3 replicas; min 4.93765; max 6.34616; MAD 0.320758",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.520148,
            "range": "3 replicas; min 6.8926; max 8.72173; MAD 0.62755; worst 8.72173",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 15.924353,
            "range": "3 replicas; min 14.3507; max 20.6327; MAD 1.57364",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.799356,
            "range": "3 replicas; min 14.4219; max 21.3148; MAD 0.515399; worst 21.3148",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 26.080014,
            "range": "3 replicas; min 18.082; max 115.665; MAD 7.99806",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.103316,
            "range": "3 replicas; min 0.096672; max 0.169157; MAD 0.006644; worst 0.169157",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.439469,
            "range": "3 replicas; min 30.5334; max 96.9035; MAD 12.9061; worst 96.9035",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 7.156207,
            "range": "3 replicas; min 2.63359; max 10.6695; MAD 3.51325; worst 10.6695",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.273846,
            "range": "3 replicas; min 1.09918; max 4.63863; MAD 0.174666; worst 4.63863",
            "unit": "ms"
          }
        ]
      },
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
          "id": "5e2fec4df146a1460214e4083386e3528f6140c9",
          "message": "Merge pull request #435 from serjflint/refactor/owner-absorption\n\nrefactor: complete tooltip controller ownership",
          "timestamp": "2026-08-25T12:32:39+05:00",
          "tree_id": "2472ef85e37917ee9e403796f0ff560f8b5a350b",
          "url": "https://github.com/serjflint/saitenka/commit/5e2fec4df146a1460214e4083386e3528f6140c9"
        },
        "date": 1787643270680,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.415229,
            "range": "3 replicas; min 6.37048; max 6.62124; MAD 0.044749",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.936343,
            "range": "3 replicas; min 8.85266; max 9.17149; MAD 0.083688; worst 9.17149",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.39124,
            "range": "3 replicas; min 20.1665; max 20.8798; MAD 0.224786",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.63641,
            "range": "3 replicas; min 20.4242; max 21.2109; MAD 0.212219; worst 21.2109",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.342331,
            "range": "3 replicas; min 17.1804; max 21.1816; MAD 1.16192",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.169177,
            "range": "3 replicas; min 0.165522; max 0.169447; MAD 0.00027; worst 0.169447",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.226421,
            "range": "3 replicas; min 43.7501; max 44.3874; MAD 0.160971; worst 44.3874",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.081567,
            "range": "3 replicas; min 2.99078; max 3.14099; MAD 0.059426; worst 3.14099",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.471658,
            "range": "3 replicas; min 1.09891; max 1.52868; MAD 0.05702; worst 1.52868",
            "unit": "ms"
          }
        ]
      },
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
          "id": "bf4f28271f3670f46224d3abb5bd13663232ad13",
          "message": "Merge pull request #436 from serjflint/fix/e2e-libass-runtime\n\nfix(ci): green E2E, with a leg per declared mpv floor",
          "timestamp": "2026-08-25T13:39:15+05:00",
          "tree_id": "fb7db7d30331b83f7c4d2e80735336d51e0d3c91",
          "url": "https://github.com/serjflint/saitenka/commit/bf4f28271f3670f46224d3abb5bd13663232ad13"
        },
        "date": 1787647239594,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.359901,
            "range": "3 replicas; min 4.19892; max 6.61952; MAD 0.259616",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.767313,
            "range": "3 replicas; min 5.80103; max 10.0756; MAD 1.30828; worst 10.0756",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.476743,
            "range": "3 replicas; min 12.8653; max 20.4379; MAD 1.96112",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.911607,
            "range": "3 replicas; min 15.0017; max 20.5771; MAD 1.66553; worst 20.5771",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.186194,
            "range": "3 replicas; min 15.5529; max 36.1627; MAD 6.63326",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.13293,
            "range": "3 replicas; min 0.081801; max 0.168997; MAD 0.036067; worst 0.168997",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.022738,
            "range": "3 replicas; min 39.8414; max 129.68; MAD 4.18133; worst 129.68",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.63825,
            "range": "3 replicas; min 2.90668; max 146.238; MAD 0.731568; worst 146.238",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.379737,
            "range": "3 replicas; min 0.960575; max 64.0885; MAD 0.419162; worst 64.0885",
            "unit": "ms"
          }
        ]
      },
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
          "id": "683685c6b22868f36dd3a451f2194e6dcf367131",
          "message": "Merge pull request #437 from serjflint/fix/libasslite-ci-flakes\n\nfix(ci): stop the libasslite perf gate and Gate B probe going red on noise",
          "timestamp": "2026-08-25T16:51:26+05:00",
          "tree_id": "3fa57da41cc289211ef9cbb245e56154b4427614",
          "url": "https://github.com/serjflint/saitenka/commit/683685c6b22868f36dd3a451f2194e6dcf367131"
        },
        "date": 1787658772080,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.306059,
            "range": "3 replicas; min 6.30051; max 6.39263; MAD 0.005551",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.193289,
            "range": "3 replicas; min 8.59239; max 9.26089; MAD 0.067606; worst 9.26089",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.273127,
            "range": "3 replicas; min 18.4541; max 20.4532; MAD 0.180086",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.485462,
            "range": "3 replicas; min 18.6704; max 21.3436; MAD 0.858125; worst 21.3436",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.390345,
            "range": "3 replicas; min 16.7226; max 18.7754; MAD 0.667725",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.165588,
            "range": "3 replicas; min 0.131486; max 0.167301; MAD 0.001713; worst 0.167301",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.608009,
            "range": "3 replicas; min 39.6661; max 43.2092; MAD 0.601152; worst 43.2092",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.58437,
            "range": "3 replicas; min 2.53823; max 2.73448; MAD 0.046139; worst 2.73448",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.146668,
            "range": "3 replicas; min 1.13144; max 1.19026; MAD 0.015232; worst 1.19026",
            "unit": "ms"
          }
        ]
      },
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
          "id": "93b42c4f9c0de84ab8dbbdb2c7ec7c902e5cdb3c",
          "message": "Merge pull request #438 from serjflint/docs/uplift-benchmark-and-fake-fidelity\n\nfix(live): fail on missing tooling, and land the standing rules behind it",
          "timestamp": "2026-08-25T19:30:39+05:00",
          "tree_id": "831cfb0c55d730b4745134ffb2d5ca26b89ee719",
          "url": "https://github.com/serjflint/saitenka/commit/93b42c4f9c0de84ab8dbbdb2c7ec7c902e5cdb3c"
        },
        "date": 1787668330901,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.382741,
            "range": "3 replicas; min 6.31045; max 6.38641; MAD 0.003668",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.891797,
            "range": "3 replicas; min 8.80343; max 10.3237; MAD 0.088372; worst 10.3237",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.669276,
            "range": "3 replicas; min 18.659; max 20.4808; MAD 0.010243",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.939536,
            "range": "3 replicas; min 18.7753; max 20.686; MAD 0.164211; worst 20.686",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 15.880994,
            "range": "3 replicas; min 14.9761; max 19.7192; MAD 0.904871",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.130316,
            "range": "3 replicas; min 0.130246; max 0.171232; MAD 7e-05; worst 0.171232",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.186103,
            "range": "3 replicas; min 39.1169; max 43.8904; MAD 0.069193; worst 43.8904",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.465862,
            "range": "3 replicas; min 2.43222; max 2.90952; MAD 0.033642; worst 2.90952",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.033628,
            "range": "3 replicas; min 0.951037; max 1.14314; MAD 0.082591; worst 1.14314",
            "unit": "ms"
          }
        ]
      },
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
          "id": "10c6972f309b95fc3926723a130ba43b6a413262",
          "message": "Merge pull request #439 from serjflint/refactor/session-assembly-registration\n\nrefactor(session): register feature-owned assembly",
          "timestamp": "2026-08-25T22:52:12+05:00",
          "tree_id": "0bac9e7973562cf65f63fc20cd2e8d46186189ad",
          "url": "https://github.com/serjflint/saitenka/commit/10c6972f309b95fc3926723a130ba43b6a413262"
        },
        "date": 1787680415544,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.458549,
            "range": "3 replicas; min 6.32496; max 6.63141; MAD 0.133592",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.310343,
            "range": "3 replicas; min 9.2072; max 9.3966; MAD 0.086261; worst 9.3966",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.989493,
            "range": "3 replicas; min 18.7188; max 20.5411; MAD 0.270708",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.114829,
            "range": "3 replicas; min 18.9803; max 23.0886; MAD 0.134561; worst 23.0886",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 15.985228,
            "range": "3 replicas; min 15.9724; max 21.2082; MAD 0.012871",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.133692,
            "range": "3 replicas; min 0.131785; max 0.166451; MAD 0.001907; worst 0.166451",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.404171,
            "range": "3 replicas; min 39.2063; max 43.872; MAD 0.197916; worst 43.872",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.587137,
            "range": "3 replicas; min 2.43835; max 3.32316; MAD 0.148784; worst 3.32316",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.11538,
            "range": "3 replicas; min 0.955947; max 1.49292; MAD 0.159433; worst 1.49292",
            "unit": "ms"
          }
        ]
      },
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
          "id": "36e607c3b4c36a5bbac3a110c16f52c20b73c244",
          "message": "Merge pull request #440 from serjflint/refactor/app-feature-packages\n\nrefactor(app): group feature modules into packages",
          "timestamp": "2026-08-26T00:28:55+05:00",
          "tree_id": "9fcd5651c9805ccf368e6381caf347923f218aa4",
          "url": "https://github.com/serjflint/saitenka/commit/36e607c3b4c36a5bbac3a110c16f52c20b73c244"
        },
        "date": 1787686233158,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.362489,
            "range": "3 replicas; min 6.35732; max 6.40494; MAD 0.005166",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.226887,
            "range": "3 replicas; min 9.18712; max 9.29805; MAD 0.039771; worst 9.29805",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.526898,
            "range": "3 replicas; min 18.7198; max 21.0127; MAD 0.48576",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 21.06855,
            "range": "3 replicas; min 18.804; max 21.26; MAD 0.19146; worst 21.26",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.688673,
            "range": "3 replicas; min 14.5716; max 20.2775; MAD 2.58883",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.168684,
            "range": "3 replicas; min 0.129566; max 0.169116; MAD 0.000432; worst 0.169116",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.083346,
            "range": "3 replicas; min 39.4274; max 44.2709; MAD 0.187544; worst 44.2709",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.949633,
            "range": "3 replicas; min 2.48229; max 3.4453; MAD 0.467339; worst 3.4453",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.133178,
            "range": "3 replicas; min 0.900262; max 1.51302; MAD 0.232916; worst 1.51302",
            "unit": "ms"
          }
        ]
      },
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
          "id": "97ddb0b2a1bd3d82aee0015b48b83b204d46ca2a",
          "message": "Merge pull request #441 from serjflint/refactor/test-suite-packages\n\nrefactor(tests): align suites with feature ownership",
          "timestamp": "2026-08-26T01:37:30+05:00",
          "tree_id": "060d11fe3fb5c394b74a72489d352d71e2eaeaee",
          "url": "https://github.com/serjflint/saitenka/commit/97ddb0b2a1bd3d82aee0015b48b83b204d46ca2a"
        },
        "date": 1787690345410,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.2926,
            "range": "3 replicas; min 6.26556; max 6.34728; MAD 0.027039",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.90581,
            "range": "3 replicas; min 8.59761; max 9.25134; MAD 0.308204; worst 9.25134",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.344182,
            "range": "3 replicas; min 18.4029; max 20.6315; MAD 0.287299",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.628084,
            "range": "3 replicas; min 18.589; max 20.8753; MAD 0.247233; worst 20.8753",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.976041,
            "range": "3 replicas; min 13.8204; max 18.6322; MAD 0.656161",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.162664,
            "range": "3 replicas; min 0.138077; max 0.166088; MAD 0.003424; worst 0.166088",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.251228,
            "range": "3 replicas; min 38.9636; max 43.377; MAD 0.125749; worst 43.377",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.754261,
            "range": "3 replicas; min 2.29985; max 2.84844; MAD 0.094178; worst 2.84844",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.282634,
            "range": "3 replicas; min 0.802297; max 1.32282; MAD 0.040189; worst 1.32282",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3ce0fa26355441597898a87d1b70ff1bf0cc645b",
          "message": "Merge pull request #442 from serjflint/refactor/session-authority-capabilities\n\nrefactor: bound stateless session command authority",
          "timestamp": "2026-08-26T10:49:06+05:00",
          "tree_id": "08bb22bd83848a38f4c51b0f316acd1bf1a0fb31",
          "url": "https://github.com/serjflint/saitenka/commit/3ce0fa26355441597898a87d1b70ff1bf0cc645b"
        },
        "date": 1787723432182,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.975664,
            "range": "3 replicas; min 4.90811; max 6.44125; MAD 0.465582",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.169031,
            "range": "3 replicas; min 7.08933; max 9.41929; MAD 1.0797; worst 9.41929",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.008319,
            "range": "3 replicas; min 14.2678; max 20.5455; MAD 2.53718",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.356254,
            "range": "3 replicas; min 14.659; max 20.8715; MAD 2.51525; worst 20.8715",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.638408,
            "range": "3 replicas; min 18.6876; max 179.31; MAD 3.95081",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.116442,
            "range": "3 replicas; min 0.104228; max 0.165299; MAD 0.012214; worst 0.165299",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 49.078208,
            "range": "3 replicas; min 44.2921; max 139.018; MAD 4.78613; worst 139.018",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 7.598223,
            "range": "3 replicas; min 2.67445; max 52.1006; MAD 4.92378; worst 52.1006",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.311255,
            "range": "3 replicas; min 1.1391; max 1.31399; MAD 0.002737; worst 1.31399",
            "unit": "ms"
          }
        ]
      },
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
          "id": "7f755c3bbd134ae3ceefa970bd0fb067d85dc41f",
          "message": "Merge pull request #443 from serjflint/refactor/analysis-feature-owner\n\nrefactor(analysis): give feature a private owner",
          "timestamp": "2026-08-26T20:45:14+05:00",
          "tree_id": "7d3da4f332feac09e203e62effe323ed10f321b8",
          "url": "https://github.com/serjflint/saitenka/commit/7f755c3bbd134ae3ceefa970bd0fb067d85dc41f"
        },
        "date": 1787760080622,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.189067,
            "range": "3 replicas; min 4.2344; max 6.378; MAD 0.188936",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.6748,
            "range": "3 replicas; min 6.67608; max 8.75608; MAD 0.081279; worst 8.75608",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.544819,
            "range": "3 replicas; min 12.551; max 20.4684; MAD 1.92359",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.783093,
            "range": "3 replicas; min 12.6968; max 20.7686; MAD 1.98547; worst 20.7686",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.397204,
            "range": "3 replicas; min 16.3385; max 109.576; MAD 2.05874",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.119654,
            "range": "3 replicas; min 0.081187; max 0.169989; MAD 0.038467; worst 0.169989",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 47.112756,
            "range": "3 replicas; min 44.1034; max 54.8967; MAD 3.00933; worst 54.8967",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.730712,
            "range": "3 replicas; min 2.39119; max 10.1884; MAD 0.339518; worst 10.1884",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.159319,
            "range": "3 replicas; min 1.04959; max 1.87198; MAD 0.109729; worst 1.87198",
            "unit": "ms"
          }
        ]
      },
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
          "id": "3c6c4abcb84e012c523c32e605864fbb25520e83",
          "message": "Merge pull request #444 from serjflint/refactor/cue-presentation-owner\n\nrefactor(annotation): give cue annotation a feature owner",
          "timestamp": "2026-08-26T20:45:58+05:00",
          "tree_id": "da9bab37815867c62cd87c42753f100c6fdaf75d",
          "url": "https://github.com/serjflint/saitenka/commit/3c6c4abcb84e012c523c32e605864fbb25520e83"
        },
        "date": 1787760271619,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.374404,
            "range": "3 replicas; min 6.27308; max 6.71773; MAD 0.101322",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.811716,
            "range": "3 replicas; min 8.80497; max 9.33378; MAD 0.006749; worst 9.33378",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.413783,
            "range": "3 replicas; min 20.2555; max 20.895; MAD 0.158312",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.675673,
            "range": "3 replicas; min 20.3895; max 21.2339; MAD 0.286171; worst 21.2339",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.527246,
            "range": "3 replicas; min 19.5651; max 21.4686; MAD 0.941364",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.165619,
            "range": "3 replicas; min 0.163596; max 0.18044; MAD 0.002023; worst 0.18044",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.868141,
            "range": "3 replicas; min 43.8072; max 44.5604; MAD 0.060939; worst 44.5604",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.874336,
            "range": "3 replicas; min 2.79715; max 3.30599; MAD 0.077182; worst 3.30599",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.185353,
            "range": "3 replicas; min 1.05225; max 1.87434; MAD 0.133108; worst 1.87434",
            "unit": "ms"
          }
        ]
      },
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
          "id": "057659244cf594549c912fb9dae379a7351b3e3c",
          "message": "Merge pull request #445 from serjflint/refactor/tooltip-preparation-owner\n\nrefactor(tooltip): own persistent preparation",
          "timestamp": "2026-08-27T16:37:20+05:00",
          "tree_id": "c70fc3580e111288835e8a5b8304f0875a3f54e4",
          "url": "https://github.com/serjflint/saitenka/commit/057659244cf594549c912fb9dae379a7351b3e3c"
        },
        "date": 1787830740530,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.815941,
            "range": "3 replicas; min 4.87943; max 6.30603; MAD 0.490088",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.02203,
            "range": "3 replicas; min 7.43756; max 8.58983; MAD 0.567804; worst 8.58983",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.551592,
            "range": "3 replicas; min 14.6544; max 20.6399; MAD 2.89716",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.938277,
            "range": "3 replicas; min 16.5993; max 21.0674; MAD 1.339; worst 21.0674",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.528509,
            "range": "3 replicas; min 22.3318; max 88.3374; MAD 0.196696",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.113169,
            "range": "3 replicas; min 0.09876; max 0.165509; MAD 0.014409; worst 0.165509",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.315811,
            "range": "3 replicas; min 43.5933; max 282.29; MAD 1.72248; worst 282.29",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.445543,
            "range": "3 replicas; min 3.14073; max 6.69731; MAD 0.304817; worst 6.69731",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.331528,
            "range": "3 replicas; min 1.26803; max 1.96198; MAD 0.0635; worst 1.96198",
            "unit": "ms"
          }
        ]
      },
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
          "id": "322fc0b4569fdd38be7acccdbfe0f78b7743d6c9",
          "message": "Refactor Tooltip's public ownership boundary (#446)\n\n* refactor: make tooltip ownership boundary explicit\n\n* refactor: close tooltip observation leaks\n\n* refactor: enforce tooltip ownership boundary\n\n* fix: handle annotation-only ownership aliases\n\n* refactor: close tooltip port projections\n\n* refactor: close remaining tooltip capabilities\n\n* refactor: narrow tooltip command endpoints\n\n* refactor: generalize tooltip authority checks\n\n* refactor: bind tooltip navigation at its action seam\n\n* fix: type tooltip back action result",
          "timestamp": "2026-08-27T20:36:48+05:00",
          "tree_id": "51166f2d16c2c24d968e05c6ffefca940afa33e7",
          "url": "https://github.com/serjflint/saitenka/commit/322fc0b4569fdd38be7acccdbfe0f78b7743d6c9"
        },
        "date": 1787845082246,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.402358,
            "range": "3 replicas; min 5.33853; max 6.55035; MAD 0.147987",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.832607,
            "range": "3 replicas; min 7.83347; max 9.90492; MAD 0.999133; worst 9.90492",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.580399,
            "range": "3 replicas; min 14.3801; max 20.6873; MAD 2.10687",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.76599,
            "range": "3 replicas; min 14.6695; max 20.8396; MAD 2.07362; worst 20.8396",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.049204,
            "range": "3 replicas; min 16.8879; max 20.0858; MAD 0.036637",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.140262,
            "range": "3 replicas; min 0.104266; max 0.169838; MAD 0.029576; worst 0.169838",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.022284,
            "range": "3 replicas; min 36.3331; max 45.4001; MAD 2.68914; worst 45.4001",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.095121,
            "range": "3 replicas; min 2.67691; max 3.10712; MAD 0.012001; worst 3.10712",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.351731,
            "range": "3 replicas; min 1.2151; max 4.48133; MAD 0.136634; worst 4.48133",
            "unit": "ms"
          }
        ]
      },
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
          "id": "7fe95fffbf1cc909d6c1763c7023cde90be78ff8",
          "message": "Merge pull request #447 from serjflint/docs/authority-reachability-process\n\ndocs(architecture): trace authority reachability",
          "timestamp": "2026-08-28T02:36:30+05:00",
          "tree_id": "1d7dc7523d70108f204527a614710b7475dc6260",
          "url": "https://github.com/serjflint/saitenka/commit/7fe95fffbf1cc909d6c1763c7023cde90be78ff8"
        },
        "date": 1787866687401,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.452492,
            "range": "3 replicas; min 6.32008; max 6.54419; MAD 0.091698",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.735165,
            "range": "3 replicas; min 9.45118; max 10.3908; MAD 0.283982; worst 10.3908",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.295392,
            "range": "3 replicas; min 18.6714; max 20.4691; MAD 0.173752",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.501748,
            "range": "3 replicas; min 18.9902; max 20.6233; MAD 0.121546; worst 20.6233",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.410449,
            "range": "3 replicas; min 15.3792; max 25.0119; MAD 3.60146",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167102,
            "range": "3 replicas; min 0.133206; max 0.167265; MAD 0.000163; worst 0.167265",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.748476,
            "range": "3 replicas; min 39.4889; max 44.446; MAD 0.697554; worst 44.446",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.911063,
            "range": "3 replicas; min 2.52508; max 4.58706; MAD 0.675992; worst 4.58706",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 2.092068,
            "range": "3 replicas; min 0.937638; max 2.11995; MAD 0.027881; worst 2.11995",
            "unit": "ms"
          }
        ]
      },
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
          "id": "9f8a28a407be029e4e66142b04b518454b2a74ae",
          "message": "Merge pull request #448 from serjflint/refactor/translation-reveal-owner\n\nrefactor(translation): own reveal policy",
          "timestamp": "2026-08-28T03:20:04+05:00",
          "tree_id": "2d71844ff3801a93c13655b43678be408cfb9f6c",
          "url": "https://github.com/serjflint/saitenka/commit/9f8a28a407be029e4e66142b04b518454b2a74ae"
        },
        "date": 1787869312441,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.39671,
            "range": "3 replicas; min 6.39277; max 6.45286; MAD 0.003942",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.315956,
            "range": "3 replicas; min 8.92445; max 9.55707; MAD 0.24111; worst 9.55707",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.611268,
            "range": "3 replicas; min 18.4366; max 20.5114; MAD 0.174678",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.749196,
            "range": "3 replicas; min 18.6106; max 20.7803; MAD 0.13856; worst 20.7803",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 15.342949,
            "range": "3 replicas; min 15.3293; max 20.7027; MAD 0.013696",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.135095,
            "range": "3 replicas; min 0.134308; max 0.165332; MAD 0.000787; worst 0.165332",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.231216,
            "range": "3 replicas; min 39.0681; max 43.6052; MAD 0.163107; worst 43.6052",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.94549,
            "range": "3 replicas; min 2.53876; max 3.1879; MAD 0.242407; worst 3.1879",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 0.976348,
            "range": "3 replicas; min 0.916686; max 1.67866; MAD 0.059662; worst 1.67866",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d0c626f42aaae74d08b307ad3e21692d7c4be68b",
          "message": "Merge pull request #449 from serjflint/inquiry/subtitle-acquisition-geometry\n\nrefactor: give subtitle acquisition and refresh owners",
          "timestamp": "2026-08-28T13:51:11+05:00",
          "tree_id": "e75ba5809b2b45ad96b4c16235016da8a8e3dc56",
          "url": "https://github.com/serjflint/saitenka/commit/d0c626f42aaae74d08b307ad3e21692d7c4be68b"
        },
        "date": 1787907350989,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.354592,
            "range": "3 replicas; min 6.34805; max 6.41194; MAD 0.006543",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.562022,
            "range": "3 replicas; min 8.80643; max 11.7869; MAD 0.755589; worst 11.7869",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.892345,
            "range": "3 replicas; min 18.7629; max 21.113; MAD 0.220651",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 21.141765,
            "range": "3 replicas; min 18.8404; max 21.3035; MAD 0.161767; worst 21.3035",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.272768,
            "range": "3 replicas; min 17.0326; max 19.8561; MAD 0.583363",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.166791,
            "range": "3 replicas; min 0.136164; max 0.168597; MAD 0.001806; worst 0.168597",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.638402,
            "range": "3 replicas; min 39.0789; max 44.3217; MAD 0.683322; worst 44.3217",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.150852,
            "range": "3 replicas; min 2.71206; max 3.28148; MAD 0.130631; worst 3.28148",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.142542,
            "range": "3 replicas; min 0.942478; max 1.60658; MAD 0.200064; worst 1.60658",
            "unit": "ms"
          }
        ]
      },
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
          "id": "08709165fb6bfe84c5e09339dce6bb2b6ae1d132",
          "message": "Merge pull request #450 from serjflint/inquiry/physical-session-boundary\n\nrefactor: give playback observation a session owner",
          "timestamp": "2026-08-28T14:49:48+05:00",
          "tree_id": "9af4cea13e9fd77ed87d69d214e4c644ba6b4ddc",
          "url": "https://github.com/serjflint/saitenka/commit/08709165fb6bfe84c5e09339dce6bb2b6ae1d132"
        },
        "date": 1787910659251,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.522076,
            "range": "3 replicas; min 5.82599; max 6.68064; MAD 0.158567",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.308122,
            "range": "3 replicas; min 8.58823; max 9.78751; MAD 0.479386; worst 9.78751",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.771789,
            "range": "3 replicas; min 17.24; max 20.7395; MAD 1.53179",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.977102,
            "range": "3 replicas; min 17.4951; max 20.997; MAD 1.48201; worst 20.997",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.796467,
            "range": "3 replicas; min 19.5266; max 22.6775; MAD 0.881082",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.153276,
            "range": "3 replicas; min 0.113063; max 0.180187; MAD 0.026911; worst 0.180187",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.413024,
            "range": "3 replicas; min 41.1416; max 44.7541; MAD 0.34107; worst 44.7541",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.392118,
            "range": "3 replicas; min 2.74221; max 9.34428; MAD 0.649905; worst 9.34428",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.208538,
            "range": "3 replicas; min 1.07597; max 7.11608; MAD 0.132568; worst 7.11608",
            "unit": "ms"
          }
        ]
      },
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
          "id": "cba869b63ae5079df442c19b63534ccec0bf61d1",
          "message": "Merge pull request #451 from serjflint/inquiry/mining-session-boundary\n\nrefactor: compose mining projections through feature owners",
          "timestamp": "2026-08-28T15:01:55+05:00",
          "tree_id": "d24e862d8dbbab915397364fc7ec5368d3aaaf22",
          "url": "https://github.com/serjflint/saitenka/commit/cba869b63ae5079df442c19b63534ccec0bf61d1"
        },
        "date": 1787911403705,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.477422,
            "range": "3 replicas; min 6.32274; max 6.69226; MAD 0.15468",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.946716,
            "range": "3 replicas; min 8.71083; max 10.8816; MAD 0.23589; worst 10.8816",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.385852,
            "range": "3 replicas; min 18.5532; max 20.8627; MAD 0.476874",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.620803,
            "range": "3 replicas; min 19.9338; max 27.5254; MAD 0.686964; worst 27.5254",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.518648,
            "range": "3 replicas; min 15.4279; max 21.2906; MAD 2.77198",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167865,
            "range": "3 replicas; min 0.136503; max 0.170558; MAD 0.002693; worst 0.170558",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.239011,
            "range": "3 replicas; min 39.489; max 44.6952; MAD 0.456177; worst 44.6952",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.835133,
            "range": "3 replicas; min 2.59496; max 3.10748; MAD 0.240173; worst 3.10748",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.51332,
            "range": "3 replicas; min 1.072; max 2.11883; MAD 0.44132; worst 2.11883",
            "unit": "ms"
          }
        ]
      },
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
          "id": "0340a5fb67e3fa3da3a4edd997396a328237d14e",
          "message": "Merge pull request #452 from serjflint/refactor/session-controller-completion\n\nrefactor: complete session controller ownership",
          "timestamp": "2026-08-29T13:14:46+05:00",
          "tree_id": "2d4d3bff7a624169ac9ed11e761ea6fc8b1da2be",
          "url": "https://github.com/serjflint/saitenka/commit/0340a5fb67e3fa3da3a4edd997396a328237d14e"
        },
        "date": 1787991370924,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.349612,
            "range": "3 replicas; min 6.03674; max 6.4036; MAD 0.053985",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.731186,
            "range": "3 replicas; min 8.2368; max 9.49886; MAD 0.494386; worst 9.49886",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.699015,
            "range": "3 replicas; min 18.8377; max 20.8338; MAD 0.134744",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.834278,
            "range": "3 replicas; min 19.0012; max 20.99; MAD 0.155723; worst 20.99",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.331092,
            "range": "3 replicas; min 17.2822; max 19.6138; MAD 0.282707",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.169526,
            "range": "3 replicas; min 0.121032; max 0.169598; MAD 7.2e-05; worst 0.169598",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.468928,
            "range": "3 replicas; min 44.7645; max 45.6337; MAD 0.164741; worst 45.6337",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.029449,
            "range": "3 replicas; min 2.63043; max 3.259; MAD 0.229548; worst 3.259",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.199249,
            "range": "3 replicas; min 1.07419; max 1.42793; MAD 0.125059; worst 1.42793",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4542b38a6aa570d0aa863a8bf1404ecd1f18dd7a",
          "message": "Merge pull request #453 from serjflint/refactor/session-kernel-completion\n\nComplete the SessionController migration",
          "timestamp": "2026-08-29T18:31:06+05:00",
          "tree_id": "a742c01329989d138155cc5e34b3d1d057dbf570",
          "url": "https://github.com/serjflint/saitenka/commit/4542b38a6aa570d0aa863a8bf1404ecd1f18dd7a"
        },
        "date": 1788010358173,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.232541,
            "range": "3 replicas; min 4.22215; max 7.05403; MAD 0.821486",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.593986,
            "range": "3 replicas; min 6.48875; max 10.0169; MAD 1.42295; worst 10.0169",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.786747,
            "range": "3 replicas; min 12.4914; max 20.7515; MAD 1.96476",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.977596,
            "range": "3 replicas; min 12.5878; max 20.8662; MAD 1.88858; worst 20.8662",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 22.970502,
            "range": "3 replicas; min 16.74; max 444.99; MAD 6.23047",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.135189,
            "range": "3 replicas; min 0.079608; max 0.166713; MAD 0.031524; worst 0.166713",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.919939,
            "range": "3 replicas; min 30.4911; max 43.284; MAD 3.36406; worst 43.284",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.098728,
            "range": "3 replicas; min 2.94982; max 146.529; MAD 0.148906; worst 146.529",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.517644,
            "range": "3 replicas; min 1.19336; max 177.182; MAD 0.324281; worst 177.182",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d0bd53cc8ea62f21160a572d4a2b67a8143c08c6",
          "message": "Merge pull request #454 from serjflint/fix/session-refactor-closure\n\nfix: close SessionController refactor gaps",
          "timestamp": "2026-08-29T20:43:31+05:00",
          "tree_id": "1f71cb3270a126b2cf8e0ad587749cf44267f85f",
          "url": "https://github.com/serjflint/saitenka/commit/d0bd53cc8ea62f21160a572d4a2b67a8143c08c6"
        },
        "date": 1788018294802,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.463279,
            "range": "3 replicas; min 6.40493; max 6.55363; MAD 0.058349",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.742568,
            "range": "3 replicas; min 8.91761; max 10.3867; MAD 0.644142; worst 10.3867",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.722804,
            "range": "3 replicas; min 18.7187; max 21.1158; MAD 0.393025",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 21.456524,
            "range": "3 replicas; min 19.0141; max 23.3945; MAD 1.93801; worst 23.3945",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.461027,
            "range": "3 replicas; min 15.2562; max 20.797; MAD 1.33593",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.170039,
            "range": "3 replicas; min 0.137053; max 0.174195; MAD 0.004156; worst 0.174195",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.412521,
            "range": "3 replicas; min 38.9932; max 45.1865; MAD 0.773998; worst 45.1865",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.960874,
            "range": "3 replicas; min 2.62439; max 3.40374; MAD 0.336481; worst 3.40374",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.166997,
            "range": "3 replicas; min 0.879588; max 1.45279; MAD 0.285793; worst 1.45279",
            "unit": "ms"
          }
        ]
      },
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
          "id": "84c6f1d2abcd9e69b4301495dee2c34c00d46c74",
          "message": "Merge pull request #455 from serjflint/docs/refresh-session-architecture-census\n\nchore: release Saitenka 5.0.0",
          "timestamp": "2026-08-29T21:58:57+05:00",
          "tree_id": "7262a816c9bc79002596965b95e2c9536b12da08",
          "url": "https://github.com/serjflint/saitenka/commit/84c6f1d2abcd9e69b4301495dee2c34c00d46c74"
        },
        "date": 1788022947200,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.419304,
            "range": "3 replicas; min 4.98446; max 6.49633; MAD 0.077022",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.807212,
            "range": "3 replicas; min 6.81594; max 9.67634; MAD 0.869132; worst 9.67634",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.635322,
            "range": "3 replicas; min 14.8305; max 20.7152; MAD 2.07988",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.76743,
            "range": "3 replicas; min 15.0372; max 21.0029; MAD 2.23546; worst 21.0029",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.055872,
            "range": "3 replicas; min 16.136; max 25.5424; MAD 1.91992",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.136645,
            "range": "3 replicas; min 0.095665; max 0.171061; MAD 0.034416; worst 0.171061",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.321898,
            "range": "3 replicas; min 37.2277; max 45.0992; MAD 2.09418; worst 45.0992",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.477044,
            "range": "3 replicas; min 2.68799; max 315.13; MAD 0.789052; worst 315.13",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.93184,
            "range": "3 replicas; min 0.99107; max 2.1785; MAD 0.246664; worst 2.1785",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c9c1637128ee9afd2e7c52461a889e1eab7c9d07",
          "message": "Merge pull request #456 from serjflint/chore/release-4.2.0\n\nfix: correct release version to 4.2.0",
          "timestamp": "2026-08-29T22:21:51+05:00",
          "tree_id": "a809cef8e1d2321992357f537215acb7da19a2c9",
          "url": "https://github.com/serjflint/saitenka/commit/c9c1637128ee9afd2e7c52461a889e1eab7c9d07"
        },
        "date": 1788024302875,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.341278,
            "range": "3 replicas; min 4.26212; max 6.41616; MAD 0.074879",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.701036,
            "range": "3 replicas; min 5.83499; max 9.00932; MAD 0.308284; worst 9.00932",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.605376,
            "range": "3 replicas; min 12.6478; max 20.3887; MAD 1.7833",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.791311,
            "range": "3 replicas; min 12.8242; max 20.5081; MAD 1.71677; worst 20.5081",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.23621,
            "range": "3 replicas; min 15.7924; max 117.594; MAD 4.44384",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.13564,
            "range": "3 replicas; min 0.081306; max 0.168604; MAD 0.032964; worst 0.168604",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.496139,
            "range": "3 replicas; min 31.5746; max 44.4278; MAD 4.9317; worst 44.4278",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.532193,
            "range": "3 replicas; min 2.63901; max 11.3411; MAD 0.893184; worst 11.3411",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.11639,
            "range": "3 replicas; min 0.954591; max 21.6031; MAD 0.161799; worst 21.6031",
            "unit": "ms"
          }
        ]
      },
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
          "id": "b6ad4e4616e16ff4108775877ed21e5799ddb9a8",
          "message": "Merge pull request #457 from serjflint/fix/runtime-reliability\n\nfix: make runtime failures visible and non-destructive",
          "timestamp": "2026-08-30T13:04:08+05:00",
          "tree_id": "e860796fe39518194d12c32c1be5307db3c8e0c2",
          "url": "https://github.com/serjflint/saitenka/commit/b6ad4e4616e16ff4108775877ed21e5799ddb9a8"
        },
        "date": 1788077128277,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.392867,
            "range": "3 replicas; min 5.92053; max 6.45771; MAD 0.064841",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.817162,
            "range": "3 replicas; min 8.09456; max 8.95429; MAD 0.137132; worst 8.95429",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.554115,
            "range": "3 replicas; min 18.5128; max 20.511; MAD 0.041281",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.975802,
            "range": "3 replicas; min 18.7051; max 20.6163; MAD 0.27075; worst 20.6163",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.243272,
            "range": "3 replicas; min 17.2417; max 17.6944; MAD 0.001591",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.13326,
            "range": "3 replicas; min 0.119259; max 0.169845; MAD 0.014001; worst 0.169845",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.724719,
            "range": "3 replicas; min 39.6155; max 53.7486; MAD 6.10923; worst 53.7486",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.637041,
            "range": "3 replicas; min 2.58164; max 2.89674; MAD 0.055402; worst 2.89674",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.120228,
            "range": "3 replicas; min 0.807421; max 1.14282; MAD 0.022597; worst 1.14282",
            "unit": "ms"
          }
        ]
      },
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
          "id": "4e056ac0b5540ef354bdcfa59e9e511967443569",
          "message": "Merge pull request #459 from serjflint/chore/quiet-gate-output\n\nchore(gate): stop poe all narrating a green run",
          "timestamp": "2026-08-30T14:34:39+05:00",
          "tree_id": "8b0a234efca332030d4d4f72eafbee22ce49ab9a",
          "url": "https://github.com/serjflint/saitenka/commit/4e056ac0b5540ef354bdcfa59e9e511967443569"
        },
        "date": 1788082699117,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.31444,
            "range": "3 replicas; min 4.95411; max 6.40322; MAD 0.088779",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.748694,
            "range": "3 replicas; min 7.75601; max 9.05738; MAD 0.308685; worst 9.05738",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.448037,
            "range": "3 replicas; min 14.2971; max 20.8447; MAD 2.39664",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.549085,
            "range": "3 replicas; min 14.4516; max 20.9901; MAD 2.44098; worst 20.9901",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 26.317916,
            "range": "3 replicas; min 15.2795; max 167.622; MAD 11.0384",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.134107,
            "range": "3 replicas; min 0.102685; max 0.168795; MAD 0.031422; worst 0.168795",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 40.453903,
            "range": "3 replicas; min 39.2334; max 44.7864; MAD 1.22049; worst 44.7864",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.511831,
            "range": "3 replicas; min 2.52058; max 3.67227; MAD 0.160442; worst 3.67227",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.300626,
            "range": "3 replicas; min 0.894685; max 1.86302; MAD 0.405941; worst 1.86302",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a2e07bfc95b2d58e7e7ebcf4ba70e66e9e8e50a6",
          "message": "Fix picker clicks after cue retirement (#458)\n\n* fix(picker): route clicks after cue retirement\n\n* docs(changelog): note picker click recovery\n\n* fix(interaction): scope retired-cue clicks by surface\n\n* fix(overlays): preserve mpv id range\n\n* test(overlays): cover full id range",
          "timestamp": "2026-08-30T14:50:48+05:00",
          "tree_id": "fa06cefe810ccc1e7a392acd06fbf333e7027b62",
          "url": "https://github.com/serjflint/saitenka/commit/a2e07bfc95b2d58e7e7ebcf4ba70e66e9e8e50a6"
        },
        "date": 1788083523222,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.477493,
            "range": "3 replicas; min 5.73211; max 6.51404; MAD 0.036547",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.359944,
            "range": "3 replicas; min 8.16656; max 9.47161; MAD 0.111669; worst 9.47161",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.466654,
            "range": "3 replicas; min 17.5749; max 19.0388; MAD 0.572176",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.656376,
            "range": "3 replicas; min 17.6904; max 19.1402; MAD 0.483836; worst 19.1402",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.597415,
            "range": "3 replicas; min 15.8414; max 24.4468; MAD 1.75603",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.120753,
            "range": "3 replicas; min 0.111792; max 0.132518; MAD 0.008961; worst 0.132518",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.366196,
            "range": "3 replicas; min 39.4133; max 45.9605; MAD 1.5943; worst 45.9605",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.762281,
            "range": "3 replicas; min 2.75937; max 5.97861; MAD 0.002914; worst 5.97861",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 0.956834,
            "range": "3 replicas; min 0.875807; max 1.50916; MAD 0.081027; worst 1.50916",
            "unit": "ms"
          }
        ]
      },
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
          "id": "619597de202f54581d33209a5cb42dac456f5b07",
          "message": "Merge pull request #460 from serjflint/fix/overlay-zorder\n\nfix(overlays): make paint order explicit",
          "timestamp": "2026-08-30T15:47:39+05:00",
          "tree_id": "80bfc22cfd162f930658c9dbd02bacbd0b9b2f9b",
          "url": "https://github.com/serjflint/saitenka/commit/619597de202f54581d33209a5cb42dac456f5b07"
        },
        "date": 1788086958214,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.244629,
            "range": "3 replicas; min 6.23241; max 6.30939; MAD 0.01222",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.643908,
            "range": "3 replicas; min 8.63509; max 8.67961; MAD 0.008815; worst 8.67961",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.463909,
            "range": "3 replicas; min 20.4538; max 20.7206; MAD 0.010106",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.699119,
            "range": "3 replicas; min 20.6221; max 21.7595; MAD 0.076973; worst 21.7595",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 20.04896,
            "range": "3 replicas; min 18.5843; max 20.2542; MAD 0.205261",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.16691,
            "range": "3 replicas; min 0.163326; max 0.183453; MAD 0.003584; worst 0.183453",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.340229,
            "range": "3 replicas; min 43.3773; max 50.1908; MAD 0.962941; worst 50.1908",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.824753,
            "range": "3 replicas; min 2.80087; max 2.91139; MAD 0.023885; worst 2.91139",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.13679,
            "range": "3 replicas; min 1.0313; max 1.22681; MAD 0.090021; worst 1.22681",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8ff377aadd67c24be0a0718e10071fe10b9f6564",
          "message": "Merge pull request #461 from serjflint/release/4.3.0\n\nchore(overlay): release 4.3.0",
          "timestamp": "2026-08-30T16:02:29+05:00",
          "tree_id": "943ee3c5dfa1a3e5c2e372890f4710e95d613a3d",
          "url": "https://github.com/serjflint/saitenka/commit/8ff377aadd67c24be0a0718e10071fe10b9f6564"
        },
        "date": 1788087916552,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.292847,
            "range": "3 replicas; min 5.77739; max 6.32347; MAD 0.030619",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.706327,
            "range": "3 replicas; min 7.89991; max 8.83547; MAD 0.129142; worst 8.83547",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.659111,
            "range": "3 replicas; min 17.4765; max 20.8337; MAD 0.174559",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.94274,
            "range": "3 replicas; min 17.9051; max 21.1157; MAD 0.172962; worst 21.1157",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.209558,
            "range": "3 replicas; min 18.3897; max 23.2764; MAD 2.06685",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.167232,
            "range": "3 replicas; min 0.110627; max 0.168267; MAD 0.001035; worst 0.168267",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.89723,
            "range": "3 replicas; min 43.113; max 44.9266; MAD 0.029384; worst 44.9266",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.169677,
            "range": "3 replicas; min 2.78825; max 3.96501; MAD 0.381424; worst 3.96501",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.286721,
            "range": "3 replicas; min 1.22508; max 1.38808; MAD 0.061642; worst 1.38808",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a6435aa9dbd02b20ede7c2e1bf11081b3c0e2954",
          "message": "Merge pull request #462 from serjflint/fix/attach-geometry-source-and-scale-override\n\nfix(attach): keep the geometry source through mpv's sid echo",
          "timestamp": "2026-08-30T20:51:43+05:00",
          "tree_id": "5cbd7ce0ff5d4fa874db678599378b443941aa92",
          "url": "https://github.com/serjflint/saitenka/commit/a6435aa9dbd02b20ede7c2e1bf11081b3c0e2954"
        },
        "date": 1788105214452,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.287855,
            "range": "3 replicas; min 6.24994; max 6.84986; MAD 0.037914",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.530944,
            "range": "3 replicas; min 8.60869; max 9.65778; MAD 0.126837; worst 9.65778",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.714921,
            "range": "3 replicas; min 20.5728; max 21.2994; MAD 0.142104",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 21.074036,
            "range": "3 replicas; min 20.7893; max 21.495; MAD 0.284764; worst 21.495",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.049502,
            "range": "3 replicas; min 18.591; max 21.5327; MAD 0.458494",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.169178,
            "range": "3 replicas; min 0.1654; max 0.191551; MAD 0.003778; worst 0.191551",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.768425,
            "range": "3 replicas; min 43.9336; max 46.6871; MAD 0.918667; worst 46.6871",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.068025,
            "range": "3 replicas; min 2.94578; max 3.4825; MAD 0.122243; worst 3.4825",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.278842,
            "range": "3 replicas; min 1.08146; max 1.35132; MAD 0.072474; worst 1.35132",
            "unit": "ms"
          }
        ]
      },
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
          "id": "694bbc96c9b20be6e8185fc8bfdfa5338659d1b7",
          "message": "fix(render): raster pills, pitch graphs and markers natively at the display scale (#463)\n\nThe crisp path draws body text through FreeType at size×scale, but composited every\nchip, pitch graph and bullet marker by LANCZOS-upscaling its 1× sprite. Next to\nnatively-drawn glyphs they read soft — visible on any display where the tooltip\nrasters above 1.05×, and on every render when tip_scale is pinned.\n\nChips now redraw in device px, and ImgBox takes a `native` provider so the pitch\ngraph and the icon markers redraw at size rather than being stretched. scale == 1.0\nstays on the untouched reference path: the 1× panel cache depends on it being\nbyte-identical.\n\nThe native pill fills its 1× box projected by the scale, not its own natural extent.\nAn integer font size does not scale linearly, so a freely-sized pill came out several\npx wide of the slot layout reserved for it and, at 1.22×, closed the gap to its\nneighbour outright. The box stays authoritative and the slack is split between the\ntwo pads; ChipBox keeps measuring the 1× sprite, so hit-test geometry is unmoved.",
          "timestamp": "2026-08-31T00:12:10+05:00",
          "tree_id": "26225767e2d259f5523b77609558c9932f232b8e",
          "url": "https://github.com/serjflint/saitenka/commit/694bbc96c9b20be6e8185fc8bfdfa5338659d1b7"
        },
        "date": 1788117215646,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.359414,
            "range": "3 replicas; min 5.91768; max 6.84148; MAD 0.441738",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.870864,
            "range": "3 replicas; min 7.99932; max 10.2781; MAD 0.871548; worst 10.2781",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.628919,
            "range": "3 replicas; min 18.1929; max 20.8797; MAD 0.250763",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.92754,
            "range": "3 replicas; min 18.379; max 21.1254; MAD 0.197843; worst 21.1254",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 25.857056,
            "range": "3 replicas; min 19.8588; max 60.4442; MAD 5.99821",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.175679,
            "range": "3 replicas; min 0.115407; max 0.177363; MAD 0.001684; worst 0.177363",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.094393,
            "range": "3 replicas; min 43.8633; max 45.8958; MAD 0.801429; worst 45.8958",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.755695,
            "range": "3 replicas; min 3.50451; max 87.2101; MAD 0.251185; worst 87.2101",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.758253,
            "range": "3 replicas; min 1.0417; max 79.2594; MAD 0.716558; worst 79.2594",
            "unit": "ms"
          }
        ]
      },
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
          "id": "fd0209426b30163bc5e53e6e3a69603ed2b84031",
          "message": "Merge pull request #464 from serjflint/release/4.3.1\n\nrelease/4.3.0 → 4.3.1",
          "timestamp": "2026-08-31T10:11:13+05:00",
          "tree_id": "2a1432f054369328ba0aca9e98b7d2554cec8d08",
          "url": "https://github.com/serjflint/saitenka/commit/fd0209426b30163bc5e53e6e3a69603ed2b84031"
        },
        "date": 1788153280513,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.3047,
            "range": "3 replicas; min 6.30422; max 6.47244; MAD 0.000483",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.108573,
            "range": "3 replicas; min 8.84486; max 10.8878; MAD 0.263712; worst 10.8878",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.64567,
            "range": "3 replicas; min 20.3809; max 20.7947; MAD 0.149058",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.920954,
            "range": "3 replicas; min 20.525; max 21.0707; MAD 0.149779; worst 21.0707",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 19.76412,
            "range": "3 replicas; min 19.4539; max 23.9152; MAD 0.310196",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.164748,
            "range": "3 replicas; min 0.163045; max 0.173765; MAD 0.001703; worst 0.173765",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.608129,
            "range": "3 replicas; min 43.4187; max 44.7982; MAD 0.189441; worst 44.7982",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.989631,
            "range": "3 replicas; min 2.96311; max 4.54876; MAD 0.026525; worst 4.54876",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.236245,
            "range": "3 replicas; min 1.20294; max 1.7821; MAD 0.033308; worst 1.7821",
            "unit": "ms"
          }
        ]
      },
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
          "id": "056df8ff4b63fdfa4cdb648a6951973f728893ee",
          "message": "Merge pull request #465 from serjflint/fix/gaiji-box-fit\n\nfix: gaiji sprite overdraw at 1×, an IPC ordering race, and the branch shape behind both",
          "timestamp": "2026-08-31T11:22:50+05:00",
          "tree_id": "b808b5184c328f6dcdfa0d1c800e63a64c2bef3b",
          "url": "https://github.com/serjflint/saitenka/commit/056df8ff4b63fdfa4cdb648a6951973f728893ee"
        },
        "date": 1788157473393,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.004978,
            "range": "3 replicas; min 4.97755; max 6.34317; MAD 0.027429",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 6.866181,
            "range": "3 replicas; min 6.81937; max 8.71293; MAD 0.046809; worst 8.71293",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 15.158226,
            "range": "3 replicas; min 14.2775; max 20.9288; MAD 0.880753",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 15.402609,
            "range": "3 replicas; min 14.3601; max 21.0158; MAD 1.04252; worst 21.0158",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 21.329322,
            "range": "3 replicas; min 18.6159; max 41.8078; MAD 2.71342",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.10615,
            "range": "3 replicas; min 0.093189; max 0.16494; MAD 0.012961; worst 0.16494",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 36.922204,
            "range": "3 replicas; min 30.6795; max 43.5742; MAD 6.24273; worst 43.5742",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.921897,
            "range": "3 replicas; min 2.8424; max 3.31312; MAD 0.079492; worst 3.31312",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.233866,
            "range": "3 replicas; min 1.1705; max 1.23945; MAD 0.005582; worst 1.23945",
            "unit": "ms"
          }
        ]
      },
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
          "id": "06fc7accb918e4d5937343a1b81cdc317ba15ab1",
          "message": "Merge pull request #470 from serjflint/docs/jitenmpv-comparison\n\ndocs(comparisons): add JitenMPV, and refresh the rows that shipped since",
          "timestamp": "2026-08-31T14:46:02+05:00",
          "tree_id": "7889052df52be3756d001737d2c0b23d7145dc4b",
          "url": "https://github.com/serjflint/saitenka/commit/06fc7accb918e4d5937343a1b81cdc317ba15ab1"
        },
        "date": 1788169763967,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.360554,
            "range": "3 replicas; min 6.2992; max 6.58397; MAD 0.061356",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.748595,
            "range": "3 replicas; min 8.7361; max 10.1365; MAD 0.012498; worst 10.1365",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.886663,
            "range": "3 replicas; min 20.5628; max 21.1822; MAD 0.295552",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 21.09892,
            "range": "3 replicas; min 20.7802; max 21.5468; MAD 0.318746; worst 21.5468",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.68157,
            "range": "3 replicas; min 18.5379; max 21.695; MAD 0.143652",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.169438,
            "range": "3 replicas; min 0.167273; max 0.169896; MAD 0.000458; worst 0.169896",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 46.533878,
            "range": "3 replicas; min 43.6548; max 51.0845; MAD 2.87906; worst 51.0845",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.17628,
            "range": "3 replicas; min 2.83715; max 3.79842; MAD 0.33913; worst 3.79842",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.181332,
            "range": "3 replicas; min 1.01557; max 1.41573; MAD 0.165766; worst 1.41573",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8e9d78e9b2997bbcf91742558846713aae9caf92",
          "message": "Merge pull request #472 from serjflint/refactor/server-client-seam\n\nrefactor: sever the served-capability edges, extract two packages, and give the dictionary database one client",
          "timestamp": "2026-09-01T11:34:11+05:00",
          "tree_id": "9d8d2c1b9b07e8941c3003d294c693ff9969d059",
          "url": "https://github.com/serjflint/saitenka/commit/8e9d78e9b2997bbcf91742558846713aae9caf92"
        },
        "date": 1788244547807,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.216302,
            "range": "3 replicas; min 6.00992; max 6.39286; MAD 0.176561",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.590695,
            "range": "3 replicas; min 8.16249; max 9.46915; MAD 0.428209; worst 9.46915",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.867631,
            "range": "3 replicas; min 18.0197; max 20.7929; MAD 0.847884",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.978094,
            "range": "3 replicas; min 18.1402; max 20.9353; MAD 0.837868; worst 20.9353",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 16.773359,
            "range": "3 replicas; min 14.1119; max 16.8173; MAD 0.043927",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.090412,
            "range": "3 replicas; min 0.080957; max 0.113663; MAD 0.009455; worst 0.113663",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 46.209382,
            "range": "3 replicas; min 43.782; max 46.3068; MAD 0.097379; worst 46.3068",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.297037,
            "range": "3 replicas; min 2.52766; max 3.29981; MAD 0.002775; worst 3.29981",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.195202,
            "range": "3 replicas; min 1.06989; max 2.01185; MAD 0.125315; worst 2.01185",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1f51a36f31bdbb684de0d5d530ac131e739ea62e",
          "message": "Merge pull request #473 from serjflint/refactor/render-analysis-ownership\n\nrefactor(render): let the analysis renderer draw rows, not the application's type",
          "timestamp": "2026-09-01T13:08:02+05:00",
          "tree_id": "041a6b5c2dabecb3e7147299c9d12c0b7a0f61d2",
          "url": "https://github.com/serjflint/saitenka/commit/1f51a36f31bdbb684de0d5d530ac131e739ea62e"
        },
        "date": 1788250163972,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.788477,
            "range": "3 replicas; min 4.93743; max 5.79069; MAD 0.002215",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.851443,
            "range": "3 replicas; min 6.91829; max 8.12188; MAD 0.270439; worst 8.12188",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.515323,
            "range": "3 replicas; min 14.3053; max 17.6041; MAD 0.088802",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.830884,
            "range": "3 replicas; min 14.4694; max 17.9233; MAD 0.092394; worst 17.9233",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 15.608649,
            "range": "3 replicas; min 13.1282; max 19.8009; MAD 2.48043",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.078219,
            "range": "3 replicas; min 0.066648; max 0.079519; MAD 0.0013; worst 0.079519",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 42.735123,
            "range": "3 replicas; min 30.2546; max 45.8952; MAD 3.16008; worst 45.8952",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.119295,
            "range": "3 replicas; min 2.42817; max 8.28378; MAD 0.691126; worst 8.28378",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.346514,
            "range": "3 replicas; min 1.1614; max 4.17016; MAD 0.185112; worst 4.17016",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ac40f638257a29944caaeec3e0fd2315c9ea8016",
          "message": "Merge pull request #475 from serjflint/refactor/retire-dict-meta-sources\n\ntest(dict): pin the kana-keyed term_meta bug, and the oracle for retiring the duplicate readers",
          "timestamp": "2026-09-01T13:22:23+05:00",
          "tree_id": "ffe5d9344b687802daba884ceb73ebef69e71a69",
          "url": "https://github.com/serjflint/saitenka/commit/ac40f638257a29944caaeec3e0fd2315c9ea8016"
        },
        "date": 1788251032172,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.430173,
            "range": "3 replicas; min 6.37721; max 6.44275; MAD 0.012581",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.883738,
            "range": "3 replicas; min 8.7214; max 8.99158; MAD 0.107845; worst 8.99158",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.704442,
            "range": "3 replicas; min 18.6013; max 20.5112; MAD 0.103118",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.866587,
            "range": "3 replicas; min 18.7709; max 20.7316; MAD 0.095687; worst 20.7316",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 11.88593,
            "range": "3 replicas; min 11.7898; max 14.8716; MAD 0.096152",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.081703,
            "range": "3 replicas; min 0.081474; max 0.11211; MAD 0.000229; worst 0.11211",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.334835,
            "range": "3 replicas; min 39.2162; max 46.0482; MAD 1.71337; worst 46.0482",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.697304,
            "range": "3 replicas; min 2.60907; max 2.95958; MAD 0.08823; worst 2.95958",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.000171,
            "range": "3 replicas; min 0.973268; max 1.16296; MAD 0.026903; worst 1.16296",
            "unit": "ms"
          }
        ]
      },
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
          "id": "56a112ed12fba3dade16f1ea955dc2f10ad255d2",
          "message": "Merge pull request #476 from serjflint/fix/kana-keyed-term-meta-lookup\n\nfix(dict): reach a term_meta row keyed by the reading, without diluting the precise ones",
          "timestamp": "2026-09-01T13:24:34+05:00",
          "tree_id": "9198bd58749bae15476cc04dace7c6332defcb75",
          "url": "https://github.com/serjflint/saitenka/commit/56a112ed12fba3dade16f1ea955dc2f10ad255d2"
        },
        "date": 1788251145253,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.460443,
            "range": "3 replicas; min 6.40611; max 6.5159; MAD 0.054331",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.455405,
            "range": "3 replicas; min 8.84822; max 9.81777; MAD 0.362361; worst 9.81777",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.541036,
            "range": "3 replicas; min 18.361; max 20.8054; MAD 0.18004",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.730735,
            "range": "3 replicas; min 18.5206; max 21.0397; MAD 1.2101; worst 21.0397",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 12.930365,
            "range": "3 replicas; min 12.6266; max 15.4794; MAD 0.303762",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.088193,
            "range": "3 replicas; min 0.086468; max 0.119854; MAD 0.001725; worst 0.119854",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 45.161809,
            "range": "3 replicas; min 39.6222; max 45.2917; MAD 0.12994; worst 45.2917",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.016632,
            "range": "3 replicas; min 2.62541; max 3.11565; MAD 0.099021; worst 3.11565",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.094361,
            "range": "3 replicas; min 0.962215; max 1.42981; MAD 0.132146; worst 1.42981",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c3cb972ff6aef8574a25a16157438d1e55a54516",
          "message": "Merge pull request #474 from serjflint/refactor/mpvio-transport-boundary\n\nrefactor(mpvio): move the runtime adapter out of the transport package",
          "timestamp": "2026-09-01T13:44:51+05:00",
          "tree_id": "0973b45901015e3f73ce5c0bebf81d54d205265a",
          "url": "https://github.com/serjflint/saitenka/commit/c3cb972ff6aef8574a25a16157438d1e55a54516"
        },
        "date": 1788252385824,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.484456,
            "range": "3 replicas; min 4.94057; max 6.44874; MAD 0.543886",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.001836,
            "range": "3 replicas; min 6.80163; max 9.06315; MAD 1.06132; worst 9.06315",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 15.562877,
            "range": "3 replicas; min 14.3786; max 20.489; MAD 1.18425",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.479009,
            "range": "3 replicas; min 14.519; max 20.7546; MAD 2.96001; worst 20.7546",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 16.348413,
            "range": "3 replicas; min 12.6851; max 84.4494; MAD 3.66331",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.077551,
            "range": "3 replicas; min 0.06642; max 0.11745; MAD 0.011131; worst 0.11745",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.3531,
            "range": "3 replicas; min 30.4683; max 44.1099; MAD 4.75684; worst 44.1099",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 6.435944,
            "range": "3 replicas; min 4.87628; max 151.231; MAD 1.55967; worst 151.231",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.384621,
            "range": "3 replicas; min 1.20812; max 3.84489; MAD 0.176506; worst 3.84489",
            "unit": "ms"
          }
        ]
      },
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
          "id": "8771537f3b8792f375ea5b9f3f7c425d1c9e41d6",
          "message": "Merge pull request #477 from serjflint/fix/reading-orthography-match\n\nfix(dict): match a reading by its sound, not its spelling",
          "timestamp": "2026-09-01T15:18:08+05:00",
          "tree_id": "66adc14d7231d7ff591f69b890aafd859e61d7b4",
          "url": "https://github.com/serjflint/saitenka/commit/8771537f3b8792f375ea5b9f3f7c425d1c9e41d6"
        },
        "date": 1788257978101,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.482026,
            "range": "3 replicas; min 6.40058; max 6.90466; MAD 0.081447",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.936847,
            "range": "3 replicas; min 8.71487; max 9.75641; MAD 0.221974; worst 9.75641",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.699106,
            "range": "3 replicas; min 18.6278; max 20.8761; MAD 0.071267",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.978717,
            "range": "3 replicas; min 18.7744; max 21.0923; MAD 0.204327; worst 21.0923",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 13.130998,
            "range": "3 replicas; min 12.2148; max 13.381; MAD 0.249952",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.090657,
            "range": "3 replicas; min 0.088783; max 0.123391; MAD 0.001874; worst 0.123391",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.803564,
            "range": "3 replicas; min 39.3561; max 43.6902; MAD 0.447452; worst 43.6902",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.820727,
            "range": "3 replicas; min 2.53672; max 2.88647; MAD 0.065746; worst 2.88647",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.098685,
            "range": "3 replicas; min 0.999307; max 1.2132; MAD 0.099378; worst 1.2132",
            "unit": "ms"
          }
        ]
      },
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
          "id": "d3ac31973939611b0dde55f042a27b45898c066b",
          "message": "Merge pull request #478 from serjflint/refactor/retire-dict-meta-sources-2\n\nrefactor(app): retire the app's term_meta readers into the store",
          "timestamp": "2026-09-01T15:19:23+05:00",
          "tree_id": "ff1835303a383b5e701abdf7cd0fe6124674b113",
          "url": "https://github.com/serjflint/saitenka/commit/d3ac31973939611b0dde55f042a27b45898c066b"
        },
        "date": 1788258035580,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.39248,
            "range": "3 replicas; min 6.24385; max 6.82868; MAD 0.148635",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.905345,
            "range": "3 replicas; min 8.74424; max 9.63589; MAD 0.161103; worst 9.63589",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.65963,
            "range": "3 replicas; min 18.2916; max 20.9064; MAD 0.368011",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.766993,
            "range": "3 replicas; min 18.4903; max 21.1437; MAD 0.276726; worst 21.1437",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 18.273183,
            "range": "3 replicas; min 12.473; max 196.806; MAD 5.80016",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.092429,
            "range": "3 replicas; min 0.089255; max 0.125545; MAD 0.003174; worst 0.125545",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.97366,
            "range": "3 replicas; min 38.4439; max 44.751; MAD 0.529751; worst 44.751",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.072838,
            "range": "3 replicas; min 2.81602; max 4.83787; MAD 0.256814; worst 4.83787",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.456574,
            "range": "3 replicas; min 1.05369; max 1.49765; MAD 0.041081; worst 1.49765",
            "unit": "ms"
          }
        ]
      },
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
          "id": "a5a9c0c0df7a5f7db922456b0ddacad07a9b9775",
          "message": "Merge pull request #479 from serjflint/refactor/mpvio-runtime-contract\n\nrefactor(mpvio): let the transport carry a command and a bool, not the runtime's vocabulary",
          "timestamp": "2026-09-01T18:44:58+05:00",
          "tree_id": "c8ebfb4de77cf57c20534de8e55f973e75af783a",
          "url": "https://github.com/serjflint/saitenka/commit/a5a9c0c0df7a5f7db922456b0ddacad07a9b9775"
        },
        "date": 1788270436242,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.352322,
            "range": "3 replicas; min 6.32923; max 6.42549; MAD 0.023092",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.203763,
            "range": "3 replicas; min 8.85655; max 9.35931; MAD 0.155547; worst 9.35931",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.653549,
            "range": "3 replicas; min 20.528; max 20.838; MAD 0.125571",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.8776,
            "range": "3 replicas; min 20.7126; max 20.9641; MAD 0.086491; worst 20.9641",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.815897,
            "range": "3 replicas; min 14.4733; max 14.9252; MAD 0.109265",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.122648,
            "range": "3 replicas; min 0.121828; max 0.124584; MAD 0.00082; worst 0.124584",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.901037,
            "range": "3 replicas; min 43.2997; max 44.8187; MAD 0.601375; worst 44.8187",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.251981,
            "range": "3 replicas; min 2.97857; max 3.31107; MAD 0.059088; worst 3.31107",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.301014,
            "range": "3 replicas; min 1.18249; max 1.4206; MAD 0.118529; worst 1.4206",
            "unit": "ms"
          }
        ]
      },
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
          "id": "babf27bb088854bd6de0eeafc20de6eb482f6dd5",
          "message": "Merge pull request #480 from serjflint/ci/runner-shutdown-probe\n\nci: make a runner shutdown leave evidence, and stop calling a confounded pair a control",
          "timestamp": "2026-09-01T18:45:21+05:00",
          "tree_id": "a5ccb8dc6219c3e57753f783b2d7fdb7fb26df3f",
          "url": "https://github.com/serjflint/saitenka/commit/babf27bb088854bd6de0eeafc20de6eb482f6dd5"
        },
        "date": 1788270459798,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.391293,
            "range": "3 replicas; min 6.37827; max 6.42471; MAD 0.013021",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.923105,
            "range": "3 replicas; min 8.88626; max 9.77007; MAD 0.036849; worst 9.77007",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.570838,
            "range": "3 replicas; min 18.5293; max 20.7798; MAD 0.041569",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.718756,
            "range": "3 replicas; min 18.6885; max 21.2266; MAD 0.030232; worst 21.2266",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 12.416636,
            "range": "3 replicas; min 12.0894; max 16.9998; MAD 0.327263",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.091776,
            "range": "3 replicas; min 0.090272; max 0.121757; MAD 0.001504; worst 0.121757",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.350212,
            "range": "3 replicas; min 38.9319; max 44.5645; MAD 0.41833; worst 44.5645",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.854998,
            "range": "3 replicas; min 2.56213; max 4.82193; MAD 0.292865; worst 4.82193",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 0.929196,
            "range": "3 replicas; min 0.853156; max 1.4806; MAD 0.07604; worst 1.4806",
            "unit": "ms"
          }
        ]
      },
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
          "id": "1db9d13e702c4b3b473481a23b25321383c3412e",
          "message": "Merge pull request #481 from serjflint/refactor/extract-saitenka-subtitles\n\nrefactor(subtitles): extract saitenka-subtitles as its own distribution",
          "timestamp": "2026-09-01T18:46:05+05:00",
          "tree_id": "0540f62df2cdfe0939358cd855ecb6e8e3c97dc9",
          "url": "https://github.com/serjflint/saitenka/commit/1db9d13e702c4b3b473481a23b25321383c3412e"
        },
        "date": 1788270490585,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.32545,
            "range": "3 replicas; min 5.00107; max 6.90001; MAD 0.574561",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.654227,
            "range": "3 replicas; min 6.90827; max 9.89453; MAD 1.2403; worst 9.89453",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.779699,
            "range": "3 replicas; min 15.0222; max 20.7876; MAD 2.00794",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.934198,
            "range": "3 replicas; min 15.2487; max 20.9899; MAD 2.05571; worst 20.9899",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.229414,
            "range": "3 replicas; min 13.181; max 16.8455; MAD 1.04844",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.090714,
            "range": "3 replicas; min 0.072767; max 0.122018; MAD 0.017947; worst 0.122018",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 40.202514,
            "range": "3 replicas; min 36.0506; max 43.6538; MAD 3.45125; worst 43.6538",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.842851,
            "range": "3 replicas; min 2.82391; max 3.45932; MAD 0.018936; worst 3.45932",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.062564,
            "range": "3 replicas; min 1.06065; max 1.14386; MAD 0.001918; worst 1.14386",
            "unit": "ms"
          }
        ]
      },
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
          "id": "fdaf3c44db7fb2b71551fae10a7a8c4e240e85e8",
          "message": "Merge pull request #482 from serjflint/refactor/extract-saitenka-card\n\nrefactor(anki): split the shape of a card out of the module that reaches Anki",
          "timestamp": "2026-09-01T18:46:44+05:00",
          "tree_id": "2f2607d1baac9397ab48545e5386cbff914a1830",
          "url": "https://github.com/serjflint/saitenka/commit/fdaf3c44db7fb2b71551fae10a7a8c4e240e85e8"
        },
        "date": 1788270507318,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.343847,
            "range": "3 replicas; min 6.34333; max 6.38462; MAD 0.000514",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.742574,
            "range": "3 replicas; min 8.70528; max 10.54; MAD 0.037291; worst 10.54",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.676924,
            "range": "3 replicas; min 20.5459; max 20.7926; MAD 0.115721",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.8509,
            "range": "3 replicas; min 20.6668; max 21.8109; MAD 0.184112; worst 21.8109",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.839128,
            "range": "3 replicas; min 14.021; max 16.0197; MAD 0.818113",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.123361,
            "range": "3 replicas; min 0.12309; max 0.124151; MAD 0.000271; worst 0.124151",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.636988,
            "range": "3 replicas; min 43.5317; max 45.4023; MAD 0.765312; worst 45.4023",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.096874,
            "range": "3 replicas; min 2.8936; max 3.45634; MAD 0.203272; worst 3.45634",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.095876,
            "range": "3 replicas; min 1.08849; max 1.49401; MAD 0.007383; worst 1.49401",
            "unit": "ms"
          }
        ]
      },
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
          "id": "cad2521f7eb54c52de0282007a236503c0532055",
          "message": "Merge pull request #483 from serjflint/fix/hover-drops-its-own-metadata-request\n\nfix(tooltip): stop a hover invalidating its own in-flight metadata request",
          "timestamp": "2026-09-02T00:19:00+05:00",
          "tree_id": "f0ab48d6d59965c9d35b084ea52e3b3dd194086b",
          "url": "https://github.com/serjflint/saitenka/commit/cad2521f7eb54c52de0282007a236503c0532055"
        },
        "date": 1788290436608,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.386849,
            "range": "3 replicas; min 6.05499; max 6.9569; MAD 0.331856",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.7624,
            "range": "3 replicas; min 8.69454; max 9.77267; MAD 0.067859; worst 9.77267",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.530229,
            "range": "3 replicas; min 17.7025; max 20.9984; MAD 0.468194",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.626409,
            "range": "3 replicas; min 18.0244; max 21.2008; MAD 0.574422; worst 21.2008",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 15.435384,
            "range": "3 replicas; min 14.7827; max 196.792; MAD 0.652694",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.123562,
            "range": "3 replicas; min 0.09288; max 0.127297; MAD 0.003735; worst 0.127297",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 47.151353,
            "range": "3 replicas; min 44.4621; max 48.6154; MAD 1.464; worst 48.6154",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.768087,
            "range": "3 replicas; min 3.14544; max 193.831; MAD 0.62265; worst 193.831",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.592952,
            "range": "3 replicas; min 1.30874; max 55.491; MAD 0.284215; worst 55.491",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c8e976f7d14eadf92f611bea7fcaf1a9ecb37c61",
          "message": "Merge pull request #484 from serjflint/refactor/tests-own-their-sessions\n\nrefactor(tests): let tests own their sessions, via the make_session fixture",
          "timestamp": "2026-09-02T00:36:34+05:00",
          "tree_id": "0d6ef1a7dc26aa367a1a8faf6e46c72cf1171720",
          "url": "https://github.com/serjflint/saitenka/commit/c8e976f7d14eadf92f611bea7fcaf1a9ecb37c61"
        },
        "date": 1788291467315,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.383723,
            "range": "3 replicas; min 3.4655; max 6.40569; MAD 0.021972",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.761697,
            "range": "3 replicas; min 4.9092; max 8.80229; MAD 0.04059; worst 8.80229",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.406732,
            "range": "3 replicas; min 9.84553; max 20.9535; MAD 2.54681",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.62845,
            "range": "3 replicas; min 10.7727; max 21.684; MAD 3.05557; worst 21.684",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.363873,
            "range": "3 replicas; min 12.2747; max 133.418; MAD 2.08919",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.088281,
            "range": "3 replicas; min 0.049734; max 0.125585; MAD 0.037304; worst 0.125585",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.797289,
            "range": "3 replicas; min 19.6883; max 46.2267; MAD 7.4294; worst 46.2267",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.669689,
            "range": "3 replicas; min 2.88895; max 336.816; MAD 0.780744; worst 336.816",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 0.989514,
            "range": "3 replicas; min 0.951979; max 1.12424; MAD 0.037535; worst 1.12424",
            "unit": "ms"
          }
        ]
      },
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
          "id": "cac3dd38c83f31da4430ea2e2fd00ef91f40ca3e",
          "message": "Merge pull request #485 from serjflint/fix/statemachine-example-teardown\n\ntest(tooltip): release each state-machine example's session before the next",
          "timestamp": "2026-09-02T01:54:39+05:00",
          "tree_id": "c1b9f5ed40f6c85d845d591aff28bf64560ecc44",
          "url": "https://github.com/serjflint/saitenka/commit/cac3dd38c83f31da4430ea2e2fd00ef91f40ca3e"
        },
        "date": 1788296158171,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.403151,
            "range": "3 replicas; min 6.05195; max 6.67439; MAD 0.271238",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.870459,
            "range": "3 replicas; min 8.37518; max 10.1254; MAD 0.495279; worst 10.1254",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.664001,
            "range": "3 replicas; min 18.4345; max 21.0657; MAD 0.229541",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.860794,
            "range": "3 replicas; min 18.5646; max 21.238; MAD 0.296152; worst 21.238",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.200446,
            "range": "3 replicas; min 11.9141; max 17.2645; MAD 2.2863",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.093522,
            "range": "3 replicas; min 0.087629; max 0.127439; MAD 0.005893; worst 0.127439",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.773864,
            "range": "3 replicas; min 38.5047; max 45.4034; MAD 0.629539; worst 45.4034",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.302705,
            "range": "3 replicas; min 2.60475; max 3.50908; MAD 0.206377; worst 3.50908",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.341816,
            "range": "3 replicas; min 0.917601; max 1.34921; MAD 0.007398; worst 1.34921",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ecc08d2df89d420dbdc238da1c0f3cec9c5176d7",
          "message": "Merge pull request #486 from serjflint/research/architecture-inquiry-evidence-subtraction\n\ndocs(agents): subtract existing evidence before test routing",
          "timestamp": "2026-09-03T12:45:26+05:00",
          "tree_id": "a4d61654ae6dda423e9814e52a92acbba286d634",
          "url": "https://github.com/serjflint/saitenka/commit/ecc08d2df89d420dbdc238da1c0f3cec9c5176d7"
        },
        "date": 1788421604454,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.380826,
            "range": "3 replicas; min 6.34327; max 6.40917; MAD 0.028341",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.828385,
            "range": "3 replicas; min 8.76376; max 9.97251; MAD 0.064624; worst 9.97251",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.548663,
            "range": "3 replicas; min 18.4125; max 20.6707; MAD 0.136149",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.739276,
            "range": "3 replicas; min 18.5356; max 20.9456; MAD 0.203678; worst 20.9456",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 12.222087,
            "range": "3 replicas; min 11.5484; max 17.8743; MAD 0.673708",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.092457,
            "range": "3 replicas; min 0.087137; max 0.12305; MAD 0.00532; worst 0.12305",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.703595,
            "range": "3 replicas; min 38.372; max 43.5932; MAD 0.33164; worst 43.5932",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.054209,
            "range": "3 replicas; min 2.54678; max 3.35109; MAD 0.296881; worst 3.35109",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.023084,
            "range": "3 replicas; min 0.912811; max 1.6468; MAD 0.110273; worst 1.6468",
            "unit": "ms"
          }
        ]
      },
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
          "id": "6d84f845e4d9642c2a6b0c41d6ff088239a4504e",
          "message": "Merge pull request #487 from serjflint/fix/profile-translation-language\n\nfix: keep profile language generations coherent",
          "timestamp": "2026-09-03T16:46:28+05:00",
          "tree_id": "45a12c615dd0a9ea27ad88794a52edab86fa8e2b",
          "url": "https://github.com/serjflint/saitenka/commit/6d84f845e4d9642c2a6b0c41d6ff088239a4504e"
        },
        "date": 1788436063607,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 5.178283,
            "range": "3 replicas; min 4.9454; max 6.43907; MAD 0.232885",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 7.100397,
            "range": "3 replicas; min 6.95227; max 8.7819; MAD 0.148125; worst 8.7819",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 15.028354,
            "range": "3 replicas; min 14.3504; max 20.358; MAD 0.677992",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 15.299177,
            "range": "3 replicas; min 14.5473; max 20.589; MAD 0.751892; worst 20.589",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 13.847522,
            "range": "3 replicas; min 12.6076; max 15.9295; MAD 1.23992",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.077569,
            "range": "3 replicas; min 0.075041; max 0.119053; MAD 0.002528; worst 0.119053",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 37.093587,
            "range": "3 replicas; min 30.9171; max 43.5747; MAD 6.17652; worst 43.5747",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.220284,
            "range": "3 replicas; min 2.37813; max 10.3501; MAD 0.842157; worst 10.3501",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.146298,
            "range": "3 replicas; min 1.00446; max 1.4317; MAD 0.141838; worst 1.4317",
            "unit": "ms"
          }
        ]
      },
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
          "id": "364eaaed8945743b390a10e359079b813812045e",
          "message": "Merge pull request #488 from serjflint/feat/assurance-pipeline\n\nfeat: add package assurance pipeline",
          "timestamp": "2026-09-03T19:25:32+05:00",
          "tree_id": "5bb77807aa7517b4f464ad264bcf56d5238e6574",
          "url": "https://github.com/serjflint/saitenka/commit/364eaaed8945743b390a10e359079b813812045e"
        },
        "date": 1788445641595,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.500326,
            "range": "3 replicas; min 6.40819; max 6.50642; MAD 0.006096",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.545129,
            "range": "3 replicas; min 9.07383; max 9.71894; MAD 0.173807; worst 9.71894",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.603008,
            "range": "3 replicas; min 18.5667; max 20.6999; MAD 0.096855",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.908937,
            "range": "3 replicas; min 18.6808; max 20.9273; MAD 0.018382; worst 20.9273",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.530928,
            "range": "3 replicas; min 12.4557; max 16.6463; MAD 2.07525",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.123822,
            "range": "3 replicas; min 0.090225; max 0.130974; MAD 0.007152; worst 0.130974",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.042087,
            "range": "3 replicas; min 39.2281; max 44.7879; MAD 0.745847; worst 44.7879",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.183568,
            "range": "3 replicas; min 2.55456; max 3.80059; MAD 0.617021; worst 3.80059",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.369205,
            "range": "3 replicas; min 0.935415; max 1.45291; MAD 0.083706; worst 1.45291",
            "unit": "ms"
          }
        ]
      },
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
          "id": "2f7228f0c718b4e1a88a1be95028aaf279b29f78",
          "message": "Merge pull request #489 from serjflint/test/quality-loops-20260903-batch-3\n\ntest: classify clean-exit watchdog check as integration",
          "timestamp": "2026-09-03T23:58:34+05:00",
          "tree_id": "587c499aad6310e4feb97d524c73ebc77c5ad2dd",
          "url": "https://github.com/serjflint/saitenka/commit/2f7228f0c718b4e1a88a1be95028aaf279b29f78"
        },
        "date": 1788462001776,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.038698,
            "range": "3 replicas; min 4.92996; max 6.48357; MAD 0.444869",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.126482,
            "range": "3 replicas; min 7.20961; max 10.9786; MAD 0.916873; worst 10.9786",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 17.75506,
            "range": "3 replicas; min 14.3651; max 19.1083; MAD 1.35327",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 17.984851,
            "range": "3 replicas; min 14.559; max 24.2151; MAD 3.42582; worst 24.2151",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.302357,
            "range": "3 replicas; min 12.8596; max 73.1039; MAD 1.44274",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.088711,
            "range": "3 replicas; min 0.066658; max 0.090634; MAD 0.001923; worst 0.090634",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.662644,
            "range": "3 replicas; min 30.6651; max 44.9594; MAD 5.29673; worst 44.9594",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.668324,
            "range": "3 replicas; min 2.51794; max 18.5062; MAD 0.150382; worst 18.5062",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.081767,
            "range": "3 replicas; min 1.06777; max 2.96053; MAD 0.013993; worst 2.96053",
            "unit": "ms"
          }
        ]
      },
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
          "id": "13ed5dc11ff463de50d5c1b9e067bb1162e92e97",
          "message": "Merge pull request #490 from serjflint/test/quality-loops-20260903-batch-3\n\nfeat(grow): remember no-gap module audits",
          "timestamp": "2026-09-04T00:38:00+05:00",
          "tree_id": "b7c9fed4a4358e98aa915edd353673ba363273cc",
          "url": "https://github.com/serjflint/saitenka/commit/13ed5dc11ff463de50d5c1b9e067bb1162e92e97"
        },
        "date": 1788464353025,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.357287,
            "range": "3 replicas; min 5.01267; max 6.4494; MAD 0.092117",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.078701,
            "range": "3 replicas; min 7.24614; max 11.8877; MAD 1.83256; worst 11.8877",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.891099,
            "range": "3 replicas; min 14.3859; max 20.3752; MAD 1.4841",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.062942,
            "range": "3 replicas; min 14.7454; max 20.6308; MAD 1.5679; worst 20.6308",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.549363,
            "range": "3 replicas; min 13.249; max 57.4393; MAD 1.30039",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.089041,
            "range": "3 replicas; min 0.078258; max 0.120576; MAD 0.010783; worst 0.120576",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.949909,
            "range": "3 replicas; min 34.0768; max 42.8869; MAD 2.93702; worst 42.8869",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.148675,
            "range": "3 replicas; min 2.62728; max 3.71506; MAD 0.5214; worst 3.71506",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.21289,
            "range": "3 replicas; min 1.20049; max 1.39887; MAD 0.012399; worst 1.39887",
            "unit": "ms"
          }
        ]
      },
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
          "id": "930d4935891d4c782bfbf24d3b4aa7d70f46d6e3",
          "message": "Merge pull request #491 from serjflint/fix/grow-reflect-enforcement\n\nfix(agents): enforce quality-loop completion receipts",
          "timestamp": "2026-09-05T00:44:34+05:00",
          "tree_id": "14c2cb52f5df65e5e5a0eeacbd148e754794a7ca",
          "url": "https://github.com/serjflint/saitenka/commit/930d4935891d4c782bfbf24d3b4aa7d70f46d6e3"
        },
        "date": 1788551152144,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.456475,
            "range": "3 replicas; min 6.39736; max 6.57741; MAD 0.059111",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.04152,
            "range": "3 replicas; min 8.86831; max 9.44697; MAD 0.173208; worst 9.44697",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.664529,
            "range": "3 replicas; min 18.6501; max 20.6581; MAD 0.014462",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.824734,
            "range": "3 replicas; min 18.8245; max 21.9926; MAD 0.000211; worst 21.9926",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 16.350032,
            "range": "3 replicas; min 13.4819; max 22.9351; MAD 2.86817",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.08855,
            "range": "3 replicas; min 0.087809; max 0.125977; MAD 0.000741; worst 0.125977",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.699082,
            "range": "3 replicas; min 39.0802; max 44.8137; MAD 0.618891; worst 44.8137",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.437113,
            "range": "3 replicas; min 2.68962; max 3.77162; MAD 0.334509; worst 3.77162",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.024517,
            "range": "3 replicas; min 0.944365; max 1.2044; MAD 0.080152; worst 1.2044",
            "unit": "ms"
          }
        ]
      },
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
          "id": "fa9823abae6eaabff63090d68ac408e513a5885a",
          "message": "Merge pull request #492 from serjflint/refactor/sharpen-policy-core\n\nrefactor(agents): centralize sharpen disposition policy",
          "timestamp": "2026-09-05T01:02:34+05:00",
          "tree_id": "bfcddc5894c854e1f76307c2390843f28a06cca8",
          "url": "https://github.com/serjflint/saitenka/commit/fa9823abae6eaabff63090d68ac408e513a5885a"
        },
        "date": 1788552246408,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.454671,
            "range": "3 replicas; min 6.43938; max 6.54941; MAD 0.015291",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.627693,
            "range": "3 replicas; min 9.61342; max 11.3744; MAD 0.01427; worst 11.3744",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.476564,
            "range": "3 replicas; min 18.6989; max 20.5896; MAD 0.112996",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.671323,
            "range": "3 replicas; min 18.8489; max 20.6934; MAD 0.022105; worst 20.6934",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.611151,
            "range": "3 replicas; min 14.4042; max 14.9629; MAD 0.206961",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.121908,
            "range": "3 replicas; min 0.08815; max 0.12336; MAD 0.001452; worst 0.12336",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.691357,
            "range": "3 replicas; min 39.3254; max 44.0296; MAD 0.33823; worst 44.0296",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.925654,
            "range": "3 replicas; min 2.89165; max 2.93151; MAD 0.005853; worst 2.93151",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.073666,
            "range": "3 replicas; min 1.0103; max 1.15055; MAD 0.063369; worst 1.15055",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c96ce015dba37c47c4a2f40947001d83ebabb273",
          "message": "Merge pull request #496 from serjflint/test/quality-findings\n\ntest: lock picker, history, and process contracts",
          "timestamp": "2026-09-05T15:26:21+05:00",
          "tree_id": "553eb848d057e4153bc44792d5e37d55a270dc1b",
          "url": "https://github.com/serjflint/saitenka/commit/c96ce015dba37c47c4a2f40947001d83ebabb273"
        },
        "date": 1788604175296,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.513418,
            "range": "3 replicas; min 6.43999; max 6.54105; MAD 0.027627",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.065774,
            "range": "3 replicas; min 8.82456; max 9.27438; MAD 0.208604; worst 9.27438",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 20.449365,
            "range": "3 replicas; min 18.6404; max 20.6828; MAD 0.233431",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 20.572415,
            "range": "3 replicas; min 19.5588; max 20.9107; MAD 0.338306; worst 20.9107",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 14.572915,
            "range": "3 replicas; min 12.7201; max 17.4492; MAD 1.85279",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.121687,
            "range": "3 replicas; min 0.094403; max 0.124873; MAD 0.003186; worst 0.124873",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 43.790489,
            "range": "3 replicas; min 39.5602; max 81.7686; MAD 4.23025; worst 81.7686",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.551491,
            "range": "3 replicas; min 2.616; max 4.62844; MAD 0.935486; worst 4.62844",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.283044,
            "range": "3 replicas; min 1.11914; max 1.5718; MAD 0.163902; worst 1.5718",
            "unit": "ms"
          }
        ]
      },
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
          "id": "2eabefdbaa30c7cb4839271b299aa5c2327b8538",
          "message": "Merge pull request #497 from serjflint/refactor/simplify-quality-loops\n\nrefactor(agents): simplify Grow and Sharpen loops",
          "timestamp": "2026-09-05T15:34:25+05:00",
          "tree_id": "e25c87ec7a5c2b24e73b41992c69bda260c3b042",
          "url": "https://github.com/serjflint/saitenka/commit/2eabefdbaa30c7cb4839271b299aa5c2327b8538"
        },
        "date": 1788604547499,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.431972,
            "range": "3 replicas; min 4.37702; max 6.46745; MAD 0.035479",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.95192,
            "range": "3 replicas; min 5.92816; max 9.71692; MAD 0.764999; worst 9.71692",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.64054,
            "range": "3 replicas; min 12.6785; max 20.4997; MAD 1.85918",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 18.74171,
            "range": "3 replicas; min 12.8619; max 21.1155; MAD 2.37382; worst 21.1155",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 17.565604,
            "range": "3 replicas; min 12.4405; max 31.0809; MAD 5.12507",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.090685,
            "range": "3 replicas; min 0.077505; max 0.124302; MAD 0.01318; worst 0.124302",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 38.627299,
            "range": "3 replicas; min 33.9958; max 44.2073; MAD 4.63154; worst 44.2073",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 3.00174,
            "range": "3 replicas; min 2.69337; max 3.78791; MAD 0.308369; worst 3.78791",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 2.417939,
            "range": "3 replicas; min 0.973216; max 2.73824; MAD 0.320301; worst 2.73824",
            "unit": "ms"
          }
        ]
      },
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
          "id": "c75c1ce1085cc1b7f43a6d849241f533d2b4f55e",
          "message": "Merge pull request #498 from serjflint/issue-494\n\ndocs: correct SubMiner review capability",
          "timestamp": "2026-09-06T02:49:19+05:00",
          "tree_id": "738ceac39404ec2b75f6f0587aae8b689e399535",
          "url": "https://github.com/serjflint/saitenka/commit/c75c1ce1085cc1b7f43a6d849241f533d2b4f55e"
        },
        "date": 1788645178719,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.374483,
            "range": "3 replicas; min 6.34387; max 6.4914; MAD 0.030612",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 9.641812,
            "range": "3 replicas; min 8.91025; max 9.77493; MAD 0.133119; worst 9.77493",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.639885,
            "range": "3 replicas; min 18.5766; max 20.6406; MAD 0.063274",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.19031,
            "range": "3 replicas; min 18.7752; max 21.1321; MAD 0.415136; worst 21.1321",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 12.571173,
            "range": "3 replicas; min 12.3924; max 16.5135; MAD 0.1788",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.102332,
            "range": "3 replicas; min 0.088841; max 0.1231; MAD 0.013491; worst 0.1231",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 39.186286,
            "range": "3 replicas; min 38.7167; max 49.0226; MAD 0.469597; worst 49.0226",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.925237,
            "range": "3 replicas; min 2.5915; max 4.15674; MAD 0.333735; worst 4.15674",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 1.049816,
            "range": "3 replicas; min 0.871936; max 1.17656; MAD 0.126742; worst 1.17656",
            "unit": "ms"
          }
        ]
      },
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
          "id": "ba1dabc660b56697bbc529b3af21c29294ce1e00",
          "message": "Merge pull request #499 from serjflint/issue-375\n\nfix(mining): bound audio extraction",
          "timestamp": "2026-09-06T02:50:41+05:00",
          "tree_id": "5fa7fd16ad8ab8675dc3ca8afa8e2f7a69e371e2",
          "url": "https://github.com/serjflint/saitenka/commit/ba1dabc660b56697bbc529b3af21c29294ce1e00"
        },
        "date": 1788645234167,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "synth median render",
            "value": 6.32782,
            "range": "3 replicas; min 6.10565; max 6.37731; MAD 0.049488",
            "unit": "ms"
          },
          {
            "name": "synth p99 render",
            "value": 8.822679,
            "range": "3 replicas; min 8.56126; max 9.32363; MAD 0.261418; worst 9.32363",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize median",
            "value": 18.819307,
            "range": "3 replicas; min 18.6748; max 18.962; MAD 0.142649",
            "unit": "ms"
          },
          {
            "name": "subtitles: parse/index/tokenize p95",
            "value": 19.099663,
            "range": "3 replicas; min 19.0034; max 20.8127; MAD 0.096288; worst 20.8127",
            "unit": "ms"
          },
          {
            "name": "dictionary: generated archive import",
            "value": 12.182247,
            "range": "3 replicas; min 11.9286; max 14.511; MAD 0.253651",
            "unit": "ms"
          },
          {
            "name": "dictionary: exact lookup p95",
            "value": 0.090003,
            "range": "3 replicas; min 0.088323; max 0.092772; MAD 0.00168; worst 0.092772",
            "unit": "ms"
          },
          {
            "name": "click: sidebar redraw p95",
            "value": 44.56362,
            "range": "3 replicas; min 38.6636; max 47.584; MAD 3.02043; worst 47.584",
            "unit": "ms"
          },
          {
            "name": "click: backlog write p95",
            "value": 2.886641,
            "range": "3 replicas; min 2.59014; max 492.827; MAD 0.296503; worst 492.827",
            "unit": "ms"
          },
          {
            "name": "click: mined-card store p95",
            "value": 0.93028,
            "range": "3 replicas; min 0.895973; max 1.04079; MAD 0.034307; worst 1.04079",
            "unit": "ms"
          }
        ]
      }
    ],
    "Saitenka live jank": [
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "0d80b9166c1c6509648a9e78b00db72f11a85f7a",
          "message": "Merge pull request #248 from serjflint/fix/e2e-windows-green\n\nfix: green the Windows e2e leg (UTF-8 reads, path-sep test, artifact dirtying)",
          "timestamp": "2026-08-08T23:38:11Z",
          "url": "https://github.com/serjflint/saitenka/commit/0d80b9166c1c6509648a9e78b00db72f11a85f7a"
        },
        "date": 1786232626684,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
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
          "id": "ef4693b931857d236878233e574a7baa6bcddb6e",
          "message": "Merge pull request #279 from serjflint/chore/release-2.3.0\n\nchore(overlay): release 2.3.0",
          "timestamp": "2026-08-09T17:16:52+03:00",
          "tree_id": "07e93d4f42aa7edb32be7ded9eb126fa1a5e63b4",
          "url": "https://github.com/serjflint/saitenka/commit/ef4693b931857d236878233e574a7baa6bcddb6e"
        },
        "date": 1786285280666,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
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
          "id": "1171d922b7f4fa465dfe5717961486849b97cdc5",
          "message": "Merge pull request #289 from serjflint/release/2.4.0\n\nchore(overlay): release 2.4.0",
          "timestamp": "2026-08-10T02:09:37+03:00",
          "tree_id": "38a22b14cc5c6d51314b721f5d8258b0583100e4",
          "url": "https://github.com/serjflint/saitenka/commit/1171d922b7f4fa465dfe5717961486849b97cdc5"
        },
        "date": 1786317389987,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "1171d922b7f4fa465dfe5717961486849b97cdc5",
          "message": "Merge pull request #289 from serjflint/release/2.4.0\n\nchore(overlay): release 2.4.0",
          "timestamp": "2026-08-09T23:09:37Z",
          "url": "https://github.com/serjflint/saitenka/commit/1171d922b7f4fa465dfe5717961486849b97cdc5"
        },
        "date": 1786318034051,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "1171d922b7f4fa465dfe5717961486849b97cdc5",
          "message": "Merge pull request #289 from serjflint/release/2.4.0\n\nchore(overlay): release 2.4.0",
          "timestamp": "2026-08-09T23:09:37Z",
          "url": "https://github.com/serjflint/saitenka/commit/1171d922b7f4fa465dfe5717961486849b97cdc5"
        },
        "date": 1786346312363,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
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
          "id": "458cff989ca77abc254b0e6e6f79b90c2a64bfb2",
          "message": "Merge pull request #314 from serjflint/release/3.0.0\n\nchore(overlay): release 3.0.0",
          "timestamp": "2026-08-11T11:38:12+03:00",
          "tree_id": "4d5b841334b058eb4131cda9ac44bab317d410f3",
          "url": "https://github.com/serjflint/saitenka/commit/458cff989ca77abc254b0e6e6f79b90c2a64bfb2"
        },
        "date": 1786437788737,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
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
          "id": "5122ff6ac80352c29cc278928c50692e90926de4",
          "message": "Merge pull request #315 from serjflint/fix/pypi-publish-metadata-2.5\n\nci(release): bump gh-action-pypi-publish to v1.14.2 (metadata 2.5 upload fix)",
          "timestamp": "2026-08-11T11:54:41+03:00",
          "tree_id": "42bedf2d4909a3198cfd5e6733d8262af76e3302",
          "url": "https://github.com/serjflint/saitenka/commit/5122ff6ac80352c29cc278928c50692e90926de4"
        },
        "date": 1786438785068,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
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
          "id": "e07dbfa0b9e1e148aa7eab62edce771f764d4395",
          "message": "Merge pull request #316 from serjflint/fix/pypi-publish-correct-sha\n\nci(release): pin gh-action-pypi-publish v1.14.2 by commit SHA (fix manifest unknown)",
          "timestamp": "2026-08-11T12:09:33+03:00",
          "tree_id": "70670e99f855d7900f8ef1f5e612996fc41cb349",
          "url": "https://github.com/serjflint/saitenka/commit/e07dbfa0b9e1e148aa7eab62edce771f764d4395"
        },
        "date": 1786439672595,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
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
          "id": "8047cf0ebac27a6a4afb33d619613ca996cd0512",
          "message": "Merge pull request #321 from serjflint/release/3.1.0\n\nchore(overlay): release 3.1.0",
          "timestamp": "2026-08-11T15:16:10+03:00",
          "tree_id": "d763e81fa334fb573a307756b6be283f96953167",
          "url": "https://github.com/serjflint/saitenka/commit/8047cf0ebac27a6a4afb33d619613ca996cd0512"
        },
        "date": 1786450855585,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "unit": "frames"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "id": "c58e0eed36bcbd801e3164cd8e3f5817982183f5",
          "message": "test(perf): pin benchmark publish safety",
          "timestamp": "2026-08-13T09:47:51Z",
          "url": "https://github.com/serjflint/saitenka/commit/c58e0eed36bcbd801e3164cd8e3f5817982183f5"
        },
        "date": 1786614920257,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 204.025838,
            "range": "3 replicas; min 152.41; max 235.479; MAD 31.4531",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 0.476059,
            "range": "3 replicas; min 0.449087; max 0.482409; MAD 0.00635",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "382df36e01d30fb90d39a96c5bba4bc4dacb7b0e",
          "message": "Merge pull request #343 from serjflint/perf/continuous-benchmark-portfolio\n\nfeat(perf): add continuous benchmark portfolio",
          "timestamp": "2026-08-13T10:11:56Z",
          "url": "https://github.com/serjflint/saitenka/commit/382df36e01d30fb90d39a96c5bba4bc4dacb7b0e"
        },
        "date": 1786616132267,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 231.367552,
            "range": "3 replicas; min 179.455; max 272.802; MAD 41.434",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 50.645058,
            "range": "3 replicas; min 49.2805; max 56.5026; MAD 1.36454",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "948872c35e736621c514b9840619226c569087f7",
          "message": "Merge pull request #344 from serjflint/fix/windows-e2e-portability\n\nfix: make E2E regressions portable on Windows",
          "timestamp": "2026-08-13T10:36:40Z",
          "url": "https://github.com/serjflint/saitenka/commit/948872c35e736621c514b9840619226c569087f7"
        },
        "date": 1786617566243,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 231.732982,
            "range": "3 replicas; min 181.287; max 265.696; MAD 33.9635",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 54.509922,
            "range": "3 replicas; min 51.6769; max 56.3905; MAD 1.88053",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "id": "22c89758de25a68756e478c458f601141d8323a2",
          "message": "fix(perf): exercise live scroll rendering",
          "timestamp": "2026-08-13T09:58:05Z",
          "url": "https://github.com/serjflint/saitenka/commit/22c89758de25a68756e478c458f601141d8323a2"
        },
        "date": 1786618218741,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 182.210233,
            "range": "3 replicas; min 154.343; max 187.786; MAD 5.5753",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 61.760116,
            "range": "3 replicas; min 53.0534; max 62.2454; MAD 0.485242",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "213f0dffab19615787018b7f6398ab69b2dc7c1c",
          "message": "fix: preserve source-backed dictionary structure (#330)\n\n* fix: unwrap semantic dictionary glossaries\n\n* test: add dictionary structure oracle",
          "timestamp": "2026-08-12T19:05:18Z",
          "url": "https://github.com/serjflint/saitenka/commit/213f0dffab19615787018b7f6398ab69b2dc7c1c"
        },
        "date": 1786621949135,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 257.049066,
            "range": "3 replicas; min 214.13; max 273.104; MAD 16.0554",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 54.89461,
            "range": "3 replicas; min 51.12; max 63.2408; MAD 3.77465",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "6c0052f37f373ccdfc5d8eab70573c4d24acf581",
          "message": "refactor: modularize reader runtime and CLI (#332)\n\n* refactor(render): remove remaining layer backedges\n\n* refactor(reader): introduce explicit runtime seams\n\n* refactor(cli): split command domains\n\n* refactor(launch): isolate run orchestration\n\n* refactor(reader): centralize production composition\n\n* chore(launch): remove obsolete registry import\n\n* docs(launch): update provider registration boundary\n\n* refactor(cli): narrow command module imports\n\n* docs(render): update structured-content test reference\n\n* fix(launch): register subtitle providers explicitly\n\n* docs(architecture): align advisory and render boundaries\n\n* docs(render): fix worker-boundary wording",
          "timestamp": "2026-08-13T05:15:18Z",
          "url": "https://github.com/serjflint/saitenka/commit/6c0052f37f373ccdfc5d8eab70573c4d24acf581"
        },
        "date": 1786622028875,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 246.282255,
            "range": "3 replicas; min 220.417; max 248.059; MAD 1.77701",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 51.231728,
            "range": "3 replicas; min 50.8532; max 59.5832; MAD 0.378565",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "4cecefac4c5020bb85e154563199c3c3f753a103",
          "message": "refactor: establish modular Saitenka package boundaries (#329)\n\n* refactor: consolidate the saitenka source layout\n\n* refactor: remove legacy overlay project directory",
          "timestamp": "2026-08-12T18:48:40Z",
          "url": "https://github.com/serjflint/saitenka/commit/4cecefac4c5020bb85e154563199c3c3f753a103"
        },
        "date": 1786622045473,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 189.431858,
            "range": "3 replicas; min 176.217; max 410.436; MAD 13.2149",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 48.334446,
            "range": "3 replicas; min 41.4009; max 54.8081; MAD 6.47361",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6",
          "message": "refactor: organize panel subsystem package (#334)",
          "timestamp": "2026-08-13T05:57:24Z",
          "url": "https://github.com/serjflint/saitenka/commit/39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6"
        },
        "date": 1786622068317,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 202.161078,
            "range": "3 replicas; min 123.37; max 206.881; MAD 4.72025",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 48.820454,
            "range": "3 replicas; min 47.5145; max 49.496; MAD 0.675532",
            "unit": "ms"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "0551068f550609736f1e1d8abcb6e2db2fee560a",
          "message": "test(ipc): correlate Windows pipe reply (#386)",
          "timestamp": "2026-08-17T00:01:17Z",
          "url": "https://github.com/serjflint/saitenka/commit/0551068f550609736f1e1d8abcb6e2db2fee560a"
        },
        "date": 1786948670435,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "live jank: total dropped frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live jank: total delayed frames",
            "value": 0,
            "range": "3 replicas; min 0; max 0; MAD 0; worst 0",
            "unit": "frames"
          },
          {
            "name": "live: hover interaction latency",
            "value": 193.435604,
            "range": "3 replicas; min 126.946; max 290.216; MAD 66.4894",
            "unit": "ms"
          },
          {
            "name": "live: four-scroll interaction latency",
            "value": 52.62871,
            "range": "3 replicas; min 47.2249; max 116.596; MAD 5.40386",
            "unit": "ms"
          }
        ]
      }
    ],
    "Saitenka bounded-cache lifecycle": [
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "id": "c58e0eed36bcbd801e3164cd8e3f5817982183f5",
          "message": "test(perf): pin benchmark publish safety",
          "timestamp": "2026-08-13T09:47:51Z",
          "url": "https://github.com/serjflint/saitenka/commit/c58e0eed36bcbd801e3164cd8e3f5817982183f5"
        },
        "date": 1786614903247,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 49.723158,
            "range": "3 replicas; min 47.574; max 50.0567; MAD 0.333556; worst 50.0567",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 101.912975,
            "range": "3 replicas; min 52.0712; max 398.407; MAD 49.8418; worst 398.407",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 13.017088,
            "range": "3 replicas; min 12.9802; max 14.5449; MAD 0.036864",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "382df36e01d30fb90d39a96c5bba4bc4dacb7b0e",
          "message": "Merge pull request #343 from serjflint/perf/continuous-benchmark-portfolio\n\nfeat(perf): add continuous benchmark portfolio",
          "timestamp": "2026-08-13T10:11:56Z",
          "url": "https://github.com/serjflint/saitenka/commit/382df36e01d30fb90d39a96c5bba4bc4dacb7b0e"
        },
        "date": 1786616042437,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 127.8209,
            "range": "3 replicas; min 51.4205; max 235.132; MAD 76.4004; worst 235.132",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 203.27252,
            "range": "3 replicas; min 53.6387; max 470.56; MAD 149.634; worst 470.56",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 12.693504,
            "range": "3 replicas; min 12.3986; max 12.9802; MAD 0.28672",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "948872c35e736621c514b9840619226c569087f7",
          "message": "Merge pull request #344 from serjflint/fix/windows-e2e-portability\n\nfix: make E2E regressions portable on Windows",
          "timestamp": "2026-08-13T10:36:40Z",
          "url": "https://github.com/serjflint/saitenka/commit/948872c35e736621c514b9840619226c569087f7"
        },
        "date": 1786617487641,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 55.864234,
            "range": "3 replicas; min 51.7044; max 109.622; MAD 4.15979; worst 109.622",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 169.699467,
            "range": "3 replicas; min 57.485; max 451.725; MAD 112.214; worst 451.725",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 12.673024,
            "range": "3 replicas; min 11.7965; max 12.6935; MAD 0.02048",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "id": "22c89758de25a68756e478c458f601141d8323a2",
          "message": "fix(perf): exercise live scroll rendering",
          "timestamp": "2026-08-13T09:58:05Z",
          "url": "https://github.com/serjflint/saitenka/commit/22c89758de25a68756e478c458f601141d8323a2"
        },
        "date": 1786618202004,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 52.392893,
            "range": "3 replicas; min 48.768; max 54.6453; MAD 2.25244; worst 54.6453",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 374.263789,
            "range": "3 replicas; min 344.898; max 440.257; MAD 29.3654; worst 440.257",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 12.488704,
            "range": "3 replicas; min 12.1201; max 12.6607; MAD 0.172032",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "213f0dffab19615787018b7f6398ab69b2dc7c1c",
          "message": "fix: preserve source-backed dictionary structure (#330)\n\n* fix: unwrap semantic dictionary glossaries\n\n* test: add dictionary structure oracle",
          "timestamp": "2026-08-12T19:05:18Z",
          "url": "https://github.com/serjflint/saitenka/commit/213f0dffab19615787018b7f6398ab69b2dc7c1c"
        },
        "date": 1786621912370,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 51.172001,
            "range": "3 replicas; min 50.689; max 52.5332; MAD 0.483026; worst 52.5332",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 58.680598,
            "range": "3 replicas; min 53.0789; max 595.67; MAD 5.60165; worst 595.67",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 13.754368,
            "range": "3 replicas; min 13.1809; max 13.7748; MAD 0.02048",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "6c0052f37f373ccdfc5d8eab70573c4d24acf581",
          "message": "refactor: modularize reader runtime and CLI (#332)\n\n* refactor(render): remove remaining layer backedges\n\n* refactor(reader): introduce explicit runtime seams\n\n* refactor(cli): split command domains\n\n* refactor(launch): isolate run orchestration\n\n* refactor(reader): centralize production composition\n\n* chore(launch): remove obsolete registry import\n\n* docs(launch): update provider registration boundary\n\n* refactor(cli): narrow command module imports\n\n* docs(render): update structured-content test reference\n\n* fix(launch): register subtitle providers explicitly\n\n* docs(architecture): align advisory and render boundaries\n\n* docs(render): fix worker-boundary wording",
          "timestamp": "2026-08-13T05:15:18Z",
          "url": "https://github.com/serjflint/saitenka/commit/6c0052f37f373ccdfc5d8eab70573c4d24acf581"
        },
        "date": 1786621926901,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 52.414794,
            "range": "3 replicas; min 50.962; max 57.4863; MAD 1.45277; worst 57.4863",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 395.4071,
            "range": "3 replicas; min 52.4558; max 672.074; MAD 276.667; worst 672.074",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 13.98784,
            "range": "3 replicas; min 13.7994; max 14.0902; MAD 0.1024",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "4cecefac4c5020bb85e154563199c3c3f753a103",
          "message": "refactor: establish modular Saitenka package boundaries (#329)\n\n* refactor: consolidate the saitenka source layout\n\n* refactor: remove legacy overlay project directory",
          "timestamp": "2026-08-12T18:48:40Z",
          "url": "https://github.com/serjflint/saitenka/commit/4cecefac4c5020bb85e154563199c3c3f753a103"
        },
        "date": 1786621974984,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 52.113988,
            "range": "3 replicas; min 50.0927; max 53.5147; MAD 1.40067; worst 53.5147",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 315.681773,
            "range": "3 replicas; min 60.0653; max 390.801; MAD 75.1194; worst 390.801",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 13.512704,
            "range": "3 replicas; min 12.3904; max 14.1476; MAD 0.63488",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6",
          "message": "refactor: organize panel subsystem package (#334)",
          "timestamp": "2026-08-13T05:57:24Z",
          "url": "https://github.com/serjflint/saitenka/commit/39761eeef2d53cb8ca034b3a4f35b2d5f7d7d0b6"
        },
        "date": 1786621994691,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 54.536539,
            "range": "3 replicas; min 53.0276; max 56.8432; MAD 1.50897; worst 56.8432",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 339.78993,
            "range": "3 replicas; min 265.392; max 620.656; MAD 74.3977; worst 620.656",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 14.102528,
            "range": "3 replicas; min 13.5537; max 14.8972; MAD 0.548864",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "0551068f550609736f1e1d8abcb6e2db2fee560a",
          "message": "test(ipc): correlate Windows pipe reply (#386)",
          "timestamp": "2026-08-17T00:01:17Z",
          "url": "https://github.com/serjflint/saitenka/commit/0551068f550609736f1e1d8abcb6e2db2fee560a"
        },
        "date": 1786948606377,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 51.076268,
            "range": "3 replicas; min 50.7606; max 248.825; MAD 0.315684; worst 248.825",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 64.088072,
            "range": "3 replicas; min 54.7446; max 416.526; MAD 9.34351; worst 416.526",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 13.733888,
            "range": "3 replicas; min 13.5537; max 14.3073; MAD 0.180224",
            "unit": "MB"
          }
        ]
      },
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
          "id": "a3ad93abcc4f26b9548a585b52742aa9a9f83577",
          "message": "Merge pull request #419 from serjflint/chore/release-4.0.0\n\nchore(overlay): release 4.0.0",
          "timestamp": "2026-08-24T05:15:14+05:00",
          "tree_id": "b27a2ee638ff8d334e40b26ae613575326b9d942",
          "url": "https://github.com/serjflint/saitenka/commit/a3ad93abcc4f26b9548a585b52742aa9a9f83577"
        },
        "date": 1787530749652,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 3.814184,
            "range": "3 replicas; min 3.67658; max 4.08099; MAD 0.137599; worst 4.08099",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 5.190301,
            "range": "3 replicas; min 4.17243; max 9.91334; MAD 1.01787; worst 9.91334",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 0.909312,
            "range": "3 replicas; min 0.909312; max 0.909312; MAD 0",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "663188e33e1e8ec4ba4142a5a295c9637b4dc006",
          "message": "Merge pull request #421 from serjflint/refactor/tooltip-controller-pilot\n\nrefactor(app): give tooltip work a bounded owner",
          "timestamp": "2026-08-24T05:37:57Z",
          "url": "https://github.com/serjflint/saitenka/commit/663188e33e1e8ec4ba4142a5a295c9637b4dc006"
        },
        "date": 1787553597143,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 3.811045,
            "range": "3 replicas; min 3.62527; max 4.00899; MAD 0.185772; worst 4.00899",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 6.649479,
            "range": "3 replicas; min 4.48052; max 19.8325; MAD 2.16896; worst 19.8325",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 0.909312,
            "range": "3 replicas; min 0.909312; max 0.909312; MAD 0",
            "unit": "MB"
          }
        ]
      },
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
          "id": "b73b9a759e1c4f506e1c2fa04ca4ef2b3e7aa2ec",
          "message": "Merge pull request #433 from serjflint/chore/release-4.1.0\n\nchore(overlay): release 4.1.0",
          "timestamp": "2026-08-25T02:14:37+05:00",
          "tree_id": "16d7fa8cc975f0bd3d69b26dd8ddd32466df64e1",
          "url": "https://github.com/serjflint/saitenka/commit/b73b9a759e1c4f506e1c2fa04ca4ef2b3e7aa2ec"
        },
        "date": 1787606335436,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 4.026542,
            "range": "3 replicas; min 3.83189; max 4.61866; MAD 0.194653; worst 4.61866",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 4.979437,
            "range": "3 replicas; min 4.46567; max 18.9683; MAD 0.51377; worst 18.9683",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 0.913408,
            "range": "3 replicas; min 0.909312; max 1.66707; MAD 0.004096",
            "unit": "MB"
          }
        ]
      },
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
          "id": "84c6f1d2abcd9e69b4301495dee2c34c00d46c74",
          "message": "Merge pull request #455 from serjflint/docs/refresh-session-architecture-census\n\nchore: release Saitenka 5.0.0",
          "timestamp": "2026-08-29T21:58:57+05:00",
          "tree_id": "7262a816c9bc79002596965b95e2c9536b12da08",
          "url": "https://github.com/serjflint/saitenka/commit/84c6f1d2abcd9e69b4301495dee2c34c00d46c74"
        },
        "date": 1788022991192,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 24.102089,
            "range": "3 replicas; min 21.736; max 38.416; MAD 2.36613; worst 38.416",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 138.057237,
            "range": "3 replicas; min 23.3844; max 343.735; MAD 114.673; worst 343.735",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 6.77888,
            "range": "3 replicas; min 6.52902; max 8.59341; MAD 0.249856",
            "unit": "MB"
          }
        ]
      },
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
          "id": "c9c1637128ee9afd2e7c52461a889e1eab7c9d07",
          "message": "Merge pull request #456 from serjflint/chore/release-4.2.0\n\nfix: correct release version to 4.2.0",
          "timestamp": "2026-08-29T22:21:51+05:00",
          "tree_id": "a809cef8e1d2321992357f537215acb7da19a2c9",
          "url": "https://github.com/serjflint/saitenka/commit/c9c1637128ee9afd2e7c52461a889e1eab7c9d07"
        },
        "date": 1788024384594,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 22.110603,
            "range": "3 replicas; min 19.3621; max 22.5896; MAD 0.479023; worst 22.5896",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 24.081259,
            "range": "3 replicas; min 23.4839; max 130.617; MAD 0.597379; worst 130.617",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 6.795264,
            "range": "3 replicas; min 6.36518; max 6.91405; MAD 0.118784",
            "unit": "MB"
          }
        ]
      },
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
          "id": "8ff377aadd67c24be0a0718e10071fe10b9f6564",
          "message": "Merge pull request #461 from serjflint/release/4.3.0\n\nchore(overlay): release 4.3.0",
          "timestamp": "2026-08-30T16:02:29+05:00",
          "tree_id": "943ee3c5dfa1a3e5c2e372890f4710e95d613a3d",
          "url": "https://github.com/serjflint/saitenka/commit/8ff377aadd67c24be0a0718e10071fe10b9f6564"
        },
        "date": 1788088037812,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 21.695119,
            "range": "3 replicas; min 20.8938; max 142.74; MAD 0.801358; worst 142.74",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 79.275488,
            "range": "3 replicas; min 22.4712; max 792.005; MAD 56.8043; worst 792.005",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 7.073792,
            "range": "3 replicas; min 6.53312; max 7.18029; MAD 0.106496",
            "unit": "MB"
          }
        ]
      },
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
          "id": "fd0209426b30163bc5e53e6e3a69603ed2b84031",
          "message": "Merge pull request #464 from serjflint/release/4.3.1\n\nrelease/4.3.0 → 4.3.1",
          "timestamp": "2026-08-31T10:11:13+05:00",
          "tree_id": "2a1432f054369328ba0aca9e98b7d2554cec8d08",
          "url": "https://github.com/serjflint/saitenka/commit/fd0209426b30163bc5e53e6e3a69603ed2b84031"
        },
        "date": 1788153309575,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 22.07007,
            "range": "3 replicas; min 21.8422; max 22.2029; MAD 0.132829; worst 22.2029",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 28.405025,
            "range": "3 replicas; min 23.7414; max 37.4743; MAD 4.66367; worst 37.4743",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 6.721536,
            "range": "3 replicas; min 6.50854; max 7.15162; MAD 0.212992",
            "unit": "MB"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Sergei Iakhnitskii",
            "username": "serjflint",
            "email": "serjflint@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "06fc7accb918e4d5937343a1b81cdc317ba15ab1",
          "message": "Merge pull request #470 from serjflint/docs/jitenmpv-comparison\n\ndocs(comparisons): add JitenMPV, and refresh the rows that shipped since",
          "timestamp": "2026-08-31T09:46:02Z",
          "url": "https://github.com/serjflint/saitenka/commit/06fc7accb918e4d5937343a1b81cdc317ba15ab1"
        },
        "date": 1788178589323,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "lifecycle: frame p99",
            "value": 24.089333,
            "range": "3 replicas; min 22.5773; max 192.931; MAD 1.51199; worst 192.931",
            "unit": "ms"
          },
          {
            "name": "lifecycle: worst frame",
            "value": 45.768372,
            "range": "3 replicas; min 25.0959; max 340.524; MAD 20.6725; worst 340.524",
            "unit": "ms"
          },
          {
            "name": "lifecycle: RSS growth",
            "value": 9.158656,
            "range": "3 replicas; min 6.63552; max 9.80582; MAD 0.647168",
            "unit": "MB"
          }
        ]
      }
    ]
  }
}