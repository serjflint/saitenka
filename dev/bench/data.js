window.BENCHMARK_DATA = {
  "lastUpdate": 1786438510978,
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
      }
    ]
  }
}