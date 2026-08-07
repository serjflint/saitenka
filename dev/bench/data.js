window.BENCHMARK_DATA = {
  "lastUpdate": 1786090548562,
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
      }
    ]
  }
}