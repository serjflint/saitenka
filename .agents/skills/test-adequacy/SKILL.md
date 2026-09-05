---
name: test-adequacy
description: >-
  Run and interpret saitenka's opt-in test-adequacy tools for the pure core — mutation (`poe mutate`,
  cosmic-ray), coverage-guided fuzzing (`poe fuzz`, atheris), symbolic execution (`poe crosshair`, z3) —
  and harden what they find. Use when asked to "run mutation testing / poe mutate", "a mutant survived /
  kill this survivor", "fuzz the parser / poe fuzz", "poe crosshair / symbolic check", "is the pure core
  adequately tested", or "harden this with a property". Covers the allowlist (TARGETS in tools/mutate/run.py),
  what earns a target, survivors to a Hypothesis property + @example, the crash-repro workflow, the 3.13-env
  pinning, and why HypoFuzz is not adopted. All three are NOT in `poe all`. NOT for authoring an ordinary
  test (use write-test); NOT for running the pre-push gate (use dev-gate); NOT for the Sharpen loop's design
  (.agents/sharpen/).
metadata:
  project: saitenka
---

# test-adequacy

Three opt-in techniques harden the **pure core** past pass/coverage, each finding a different bug class,
each **NOT in `poe all`** (slow / 3.13-pinned). Run each through its poe task — `[tool.poe.tasks]` is the
SSOT for *how*; never hand-roll the invocation.

| Tool | Task | Finds |
|---|---|---|
| Mutation (cosmic-ray) | `poe mutate [module]` | a test that doesn't catch a real change |
| Coverage-guided fuzz (atheris) | `poe fuzz` | inputs a structured generator won't reach |
| Symbolic (z3, via CrossHair) | `poe crosshair` | exact-boundary counterexamples random search misses |

**The one principle:** a survivor / crasher / counterexample is a **coordinate to harden**, never a score
to maximise (equivalent mutants make 100% unreachable). Kill the *class* with a Hypothesis property —
boundary / round-trip / spec-oracle — and pin the shrunk input as `@example` so the kill is deterministic
on rerun (`tests/test_sub_index_properties.py`). Then re-run the tool to confirm the score moved.

## Mutation — `poe mutate`

- **Allowlist:** `TARGETS` in `tools/mutate/run.py` is the SSOT (`poe mutate --list`). A module joins only
  when it is pure/algorithmic, has focused unit+property tests, and a human has run and triaged an initial
  campaign. The add/remove *policy* is the `run.py` docstring (human judgement); `tests/test_mutate_targets.py`
  rot-guards that every listed path still resolves.
- **Glue is excluded** (controller / mpvio): I/O-bound, floods equivalent mutants.
- git-guarded, minutes/module; a complete campaign is reused, `--force` rebuilds.
- **cosmic-ray re-enables the GIL** via SQLAlchemy in its own harness — expected, not a regression (the
  test subprocess still runs free-threaded).
- The **Sharpen loop** consumes mutation as its Efficacy input (`.agents/sharpen/SPEC.md`).

## Grow/Sharpen handoff

The loops consume a **complete existing artifact**; they never launch these slow campaigns inline. A
missing or partial mutation DB is an out-of-band prerequisite, not permission to improvise a run. Route
the coordinate back as data: survivor, minimized crasher, or symbolic counterexample. The test authoring
step then uses the `write-test` decision tree to kill the bug class with a property plus pinned `@example`,
and this skill reruns the originating instrument to verify the result.

When invoked by `assurance-pipeline`, return the coordinate, regression proof, and canonical instrument
replay. Re-enfolding decides whether that pure-core proof contributes to the package invariant.

## Fuzz — `poe fuzz`

atheris / libFuzzer byte-mutation of the subtitle parser. **Contract:** `parse_cues` is robust — any input
returns a possibly-empty list, never raises. A crasher drops a `crash-*` repro (gitignored) → shrink it →
add as a regression golden / `@example` → fix.

## Symbolic — `poe crosshair`

CrossHair runs the existing Hypothesis property tests under a **z3 symbolic backend** (the `crosshair`
Hypothesis backend, registered in `tests/conftest.py` only when installed). An SMT solver finds
exact-boundary counterexamples random search misses. Slow (~15 s/property) → opt-in.

**HypoFuzz is deliberately NOT adopted** — its licence is `LicenseRef-HypoFuzz` (custom / source-available,
not FOSS), unfit to commit into this Apache-2.0-clean repo; atheris + crosshair already cover the pure-core
adequacy angle.

## 3.13 env

`fuzz` / `crosshair` (and `invariants-taint`) pin **CPython 3.13** — atheris and z3 are C-extensions that
can't load under the free-threaded 3.14t default. **Never `uv run --python 3.13` against the default env**
(it recreates `.venv` as 3.13 and clobbers the 3.14t build) — run each through its poe task, which pins its
own env; `[tool.poe.tasks]` is the SSOT for how. (General env traps: the `dev-gate` skill.)

## Verify

`bash scripts/smoke.sh` (grep-free).
