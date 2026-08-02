---
name: dev-gate
description: >-
  Run and interpret the Saitenka quality gates — the fast pre-push `poe all` and the slower
  pre-tag `poe pre-release`. Use before pushing or when asked to "run the gate", "run poe all",
  "check it passes / is it green", "lint/types/arch/tests before commit", "pre-release gate", or
  when a gate task fails and you need to read the failure. Explains both bundles, the advisory
  tier, and the free-threaded / 3.13-pinned-env traps that bite agents. NOT for authoring a test
  (use the write-test skill); the release flow itself is RELEASING.md; NOT for
  mutation/fuzz/crosshair adequacy (AGENTS.md "Fuzzing & symbolic checks").
metadata:
  project: saitenka
---

# dev-gate

The repo has **no CI**. Two gates, both defined in `overlay/pyproject.toml` `[tool.poe.tasks]` — that
is the source of truth for what each runs; `uv run poe --dry-run <gate>` prints the exact chain. Run
either from the repo root or `overlay/` (a root shim delegates).

- **`poe all`** — the fast pre-push PR gate. Run before every push.
- **`poe pre-release`** — the slower superset run once before tagging; `release.py` gates on this, not
  `all`. Adds the supply-chain + installer checks and the heavier real-mpv / network smokes; needs
  **mpv + network**, so not for PR iteration. The human-triage advisory tools (`hygiene` / `ps1` /
  `perf-risk`) are deliberately **not** in it — run those by hand.

## Reading a failure (the non-obvious ones)

- `types` — a real type error; don't `# type: ignore` without a reason.
- `arch` (import-linter) — a forbidden import (cycle, PIL-in-core, GPL chokepoint): move the code,
  don't relax the contract.
- `complexity` — a function got more complex: simplify. Regenerate the baseline
  (`poe complexity-baseline`) only after a deliberate refactor, never to silence a regression.
- `cov` — floor 85%: add a behaviour test, not a coverage-painting one.
- `test-ft` — a C-ext re-enabled the GIL, or a genuine no-GIL race.
- `licenses` — a copyleft dep leaked in (only our own GPL `deinflect` is allowed): drop it.
- `bench` — a **crash** is API rot; the printed numbers are informational, not pass/fail.
- `smoke-live` — overlay↔mpv breakage the fakes can't catch.
- `hygiene` / `perf-risk` — advisory: triage via the repowise navigator, don't fix blind.

## Env traps (these bite)

- **Never `uv run --python 3.13` against the default env** — it recreates `.venv` as 3.13
  and clobbers the free-threaded 3.14t build. The 3.13-pinned tasks (`invariants-taint`,
  `fuzz`, `crosshair`) set `UV_PROJECT_ENVIRONMENT=.venv-{fuzz,cx}` for exactly this reason.
- **`cosmic-ray` re-enables the GIL** via SQLAlchemy in its own harness — expected, not a
  regression (the test subprocess still runs free-threaded).
- **Don't run `complexipy <narrow-path>`** for a spot check — it silently overwrites the
  full-repo snapshot baseline scoped to just those paths. Use `poe complexity`.

## Verify

The gate exits 0. If a single task is red, run it alone (e.g. `uv run poe types`) to iterate, then
re-run the gate.
