---
name: dev-gate
description: >-
  Run and interpret the Saitenka pre-push quality gate (`poe all`) and the advisory
  `poe hygiene` tier. Use before pushing or when asked to "run the gate", "run poe all",
  "check it passes / is it green", "lint/types/arch/tests before commit", or when a
  gate task fails and you need to read the failure. Explains the 14-task bundle, the
  advisory tier, and the free-threaded / 3.13-pinned-env traps that bite agents. NOT for
  authoring a test (use the write-test skill); NOT for release/publish (see RELEASING.md);
  NOT for mutation/fuzz/crosshair adequacy (AGENTS.md "Fuzzing & symbolic checks").
metadata:
  project: saitenka
---

# dev-gate

The repo has **no CI** — `uv run poe all` is the gate. Run it from the repo root or
`overlay/` (a root shim delegates). Run it before every push.

## `poe all` — 14 tasks, in order

`lint · types · arch · invariants · complexity · test · test-ft · cov · audit · deps ·
licenses · spell · links · shell`

| Task | What it gates | Read a failure as |
|---|---|---|
| `lint` | ruff (explicit select, **not** `ALL`) + flake8-bandit `S` SAST | style / a security smell → fix, or `# noqa: S… # reason` at a legit site |
| `types` | mypy + basedpyright + pyrefly (blocking); ty (advisory) | a real type error — don't `# type: ignore` without a reason |
| `arch` | import-linter: no cycles, PIL-agnostic core, **GPL chokepoint** | a forbidden import — move the code, don't relax the contract |
| `invariants` | ast-grep call-level anti-pattern gate | a banned call shape → rewrite |
| `complexity` | complexipy, ratcheted vs `overlay/complexipy-snapshot.json` | a function got more complex → simplify; **regenerate the baseline only after a deliberate refactor** with `poe complexity-baseline`, never to silence a regression |
| `test` | fast tier, `-n auto`, excludes `slow/integration/requires_display/e2e` | a real failure |
| `test-ft` | whole suite under `PYTHON_GIL=0` (free-threaded) | a C-ext re-enabled the GIL, or a no-GIL race |
| `cov` | coverage floor **85%** | add a behaviour test, not a coverage-painting one |
| `audit` | `uv audit` vuln scan over `uv.lock` | a CVE → bump |
| `deps` | deptry: unused / missing / misplaced deps | fix `pyproject` |
| `licenses` | pip-licenses — only allowed copyleft is our own GPL `deinflect` | a new copyleft dep slipped in → drop it |
| `spell` | typos (allowlist in root `_typos.toml`) | real typo or add to allowlist with cause |
| `links` | lychee `--offline` local-link integrity | a broken relative link |
| `shell` | shellcheck over `install/*.sh` | installer bug |

## Advisory tier — NOT in `all`

`poe hygiene` = `deadcode` (vulture) + `dup` (jscpd). Standalone advisory:
`perf-risk` (repowise I/O-in-loop / N+1), `ps1` (PSScriptAnalyzer, needs `pwsh`),
`links-net` (network crawl). Run nightly / pre-release / on triage. These emit
file:line / JSON — feed findings to the repowise navigator, don't fix blind.

## Env traps (these bite)

- **Never `uv run --python 3.13` against the default env** — it recreates `.venv` as 3.13
  and clobbers the free-threaded 3.14t build. The 3.13-pinned tasks (`invariants-taint`,
  `fuzz`, `crosshair`) set `UV_PROJECT_ENVIRONMENT=.venv-{fuzz,cx}` for exactly this reason.
- **`cosmic-ray` re-enables the GIL** via SQLAlchemy in its own harness — expected, not a
  regression (the test subprocess still runs free-threaded).
- **Don't run `complexipy <narrow-path>`** for a spot check — it silently overwrites the
  full-repo snapshot baseline scoped to just those paths. Use `poe complexity`.

## Verify

`uv run poe all` exits 0. If a single task is red, run it alone (e.g. `uv run poe types`)
to iterate, then re-run `poe all`.
