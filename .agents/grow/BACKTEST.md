# Grow loop — backtest against the held-out gap corpus

Durable backtest contract: run the loop (dry-run) against the regressions that motivated it and
ask, per gap, *would Grow surface it, which gate arm proves the fix, and what outcome class?* **Recorded,
not tuned-to-pass** — a gap the loop would miss is written down as a miss, not engineered green.

## Method & honest limits

The loop has two halves: **deterministic** (triage → the four-arm gate → ledger) and **LLM** (the
scenario-map that turns a picked module into orphan gaps, and the author that writes the test). Only the
deterministic half runs here; the LLM half is exercised by the *shape* of the worked examples, not spawned
in this backtest. So the evidence tiers are:

- **empirical** — a landed guard on this branch that passes, plus a negative control proving it has teeth
  (the gate arm the loop would apply, demonstrated);
- **structural** — the gap maps cleanly onto an implemented arm + an existing worked example, so closure is
  demonstrated-by-analogy but the specific test isn't re-authored here;
- **open** — no guard on the branch; recorded as a live discovery target, deliberately not closed by
  hand-writing a test to pass this backtest.

Corpus = the branch's own motivating set: the free-threaded cache race, the scale-boundary rewrite, the
Session5b hit-test drift, the nested scroll-to-true-bottom guard, and the honorific-prefix bug.

## Results

| Gap | On branch? | Triage would surface | Gate arm | Outcome class | Evidence | Verdict |
|---|---|---|---|---|---|---|
| **Entry-cache eviction race** (942ca3c → 9c5bc75) | fix present (9c5bc75) | `app/dictionary.py` — race dimension | **4 concurrency** | coverage-only | **empirical**: `tests/test_cache_race.py` — regression passes, negative control (no-op lock) raises `KeyError` | **surfaced + closed** |
| **Scale-boundary / crisp path at scale>1** (#167–174) | yes | `app/tooltip.py` — dead-config `scale=2.0` | **3 context-delta** (+ **2**) | coverage-only | **empirical**: `tests/features/tooltip/test_scale_boundary.py` + `test_tooltip_statemachine.py` pass; the drift negative control mis-hits | **surfaced + closed** |
| **Session5b hit-test drift** (crisp draws native, hit read reference) | yes | `app/tooltip.py` — link/scan agreement across nav+nested | **1/2/3** (agreement oracle) | coverage-only | **structural + empirical**: the Stage-4 state machine drives navigate/open_nested and asserts `_tip_link_hit`/`_nest_link_hit`/`_scan_hit` agreement each step; teeth control present | **surfaced + closed** |
| **Nested popup scrolls to its true bottom** (dabfdd9) | **NO** | `app/tooltip.py` — nested-scroll clamp dimension | **3 context-delta** (+ **2**) | coverage-only | **open**: no true-bottom clamp guard on this branch; the clamp path is lit only when scrolling past the converging full-height estimate | **would surface — left OPEN** (not tuned) |
| **Honorific prefix** (お in お考え shows 御) | bug OPEN, no test | `app/tokenize.py`/fold seam — bare-prefix dimension | (red-on-pristine) | **2 latent bug** | **open**: documented-open defect; a grown test asserting the content word would go red on pristine | **would surface → files a product issue** (class 2) |

> **Correction (adversarial review, findings C1/C6).** An earlier version of this file claimed "3/5
> surfaced+closed with demonstrated teeth." That overstated the *deterministic* evidence. The render↔hit-test
> agreement oracle (scale-boundary, Session5b) is **self-consistent by construction** — it reads the panel
> from `hit_target` for both the drawn-centre and the hit-test, so it proves the inverse transform and
> cross-transition state coherence, NOT pixel-vs-panel two-panel drift. The two-panel regression is now
> guarded by the explicit **one-panel invariant** (`hit_target`'s panel *is* the drawn panel) added to the
> state machine — and is prevented structurally by the scale-boundary rewrite (one panel). And the race
> (arm-4) teeth come from the control's own falsifiable assertion, verified by running arm-2 liveness on the
> control, not from a gate that requires the control to *fail*. Honest tally below.

## Reading the results

- **Decisive deterministic teeth: ~1 of 5 (the race).** The race closes with a self-certifying negative
  control whose oracle is arm-2-live. The scale-boundary and Session5b closures are real *coverage +
  inverse-transform + one-panel-invariant* guards (genuine value), but their round-trip does not by itself
  catch two-panel wrap drift (C1) — that named regression is caught by the one-panel invariant + the
  architecture, not the round-trip. So count them as **surfaced + guarded**, not "teeth caught the exact
  historical bug."
- **The two genuinely-open items validate the loop's two non-trivial paths without cheating:**
  - the **nested scroll-to-true-bottom** gap is exactly a *covered-but-under-specified* dimension (nested
    scroll is exercised; scroll-to-the-converging-max is not) — the loop's core reframe — and arm-3
    (context-delta on the clamp path) is the deterministic proof it would demand. Left open on purpose: the
    plan says record, don't tune.
  - the **honorific** gap is a real defect, so a faithful grown test is **red on pristine** → the harness
    routes it to a *filed product issue* (outcome class 2), never massaged green. This is the loop working
    correctly, not a miss.
- **No gap in the corpus falls outside the four arms** — the arm set is sufficient for this history. That is
  a bound on *this* corpus, not a completeness claim; the invariant catalog stays open.

## Limits & follow-ups

- The LLM scenario-map/author are not spawned here, so "triage would surface" for the open items is a
  deterministic-signal argument (fan-in + under-spec + the dimension), not an executed discovery. A live
  `openPr=false` harness run against `app/tooltip.py` is the next validation — it should independently
  propose the nested-scroll dimension.
- Arm-1 (property-mutant) is demonstrated structurally, not empirically, in this corpus: the scale/hit-test
  gaps are proven via the agreement oracle + arm-3, and the race via arm-4; none required a cosmic-ray
  property mutant. A first live run on a `poe mutate --list` target will exercise arm-1 end-to-end.
