# AGENTS.md — Saitenka (Japanese immersion tooling)

Guidance for AI agents and developers working in this repo. Feature docs: `overlay/README.md` (renderer
+ reader tour) and `overlay/RUNNING.md` (run/test walkthrough).

## Planning artifacts

- **`CHANGELOG.md`** ([Keep a Changelog](https://keepachangelog.com/)) — shipped changes; drafted with
  [git-cliff](https://git-cliff.org/) (`uv run poe changelog`) then **hand-reviewed**, never shipped raw.
  **`ROADMAP.md`** — future direction. Trackable work → issues/milestones. Scratch → `vibe/` (git-ignored).
- **Commits:** frequent, small, focused [Conventional Commits](https://www.conventionalcommits.org)
  (`feat:`/`fix:`/`docs:`/…), one logical change each. No tool-attribution trailers.

## Python: always `uv`

`uv run` / `uvx` / `uv add` — never bare `python`/`pip`/`venv`/`pipx`. Commit `uv.lock`; standalone
scripts declare deps via PEP 723 inline metadata. (Full details: the `uv-python` skill.)

## Project conventions

- **Anki access:** read-only via a **copy** of `collection.anki2` (never the live DB while Anki is open) or
  via AnkiConnect; FSRS state (`s`/`d`) is in `cards.data`.
- **LLMs:** optional, **local-first**, and **grounded (RAG)** — operate on provided authoritative sources,
  never parametric facts (readings/pitch stay from dictionaries).
- **Tokenizer:** SudachiPy / MeCab+UniDic; mind the de-inflection matching trap. Goldens in `overlay/`
  encode `unidic-lite`'s tokenization — bumping it legitimately moves goldens; re-bless deliberately.
- **Dev gate (no CI):** `uv run poe all` — lint (ruff), types (mypy + basedpyright blocking, pyrefly + ty
  advisory), arch (import-linter — no cycles, PIL-agnostic core, GPL chokepoint; `.importlinter`),
  complexity (complexipy, ratcheted against a checked-in `overlay/complexipy-snapshot.json` baseline —
  regenerate with `poe complexity-baseline` after a deliberate refactor, never to silence a real
  regression), tests (incl. free-threaded), coverage floor 85%, plus **supply-chain & hygiene** —
  `audit` (`uv audit` vuln scan over `uv.lock`), `deps` (deptry — unused/missing/misplaced deps),
  `licenses` (pip-licenses — the only copyleft allowed in the graph is our own GPL `deinflect` add-on;
  a dependency-boundary guard next to the import-linter code boundary), `spell` (typos; allowlist in
  root `_typos.toml`), `links` (lychee `--offline` local-link integrity), `shell` (shellcheck over the
  `install/*.sh` user-facing installers). Run it before pushing. `poe
  arch-report` (pyscn) is a separate, non-gating coupling/complexity report guiding the `controller.py`
  split. The real tasks live in `overlay/`; the repo-root `pyproject.toml` is a non-package poe shim
  that delegates there, so `uv run poe <task>` (all, test, bench, smoke-live, …) works from **either
  the repo root or `overlay/`**.
  The `lint` step runs a broad **explicit** ruff select (not `ALL` — that breaks unpredictably on ruff
  upgrades), favouring auto-fixable categories; **flake8-bandit `S` (SAST) is folded in**, so security
  linting is gated here (per-site `# noqa: S…` with reasons at the legit subprocess/urlopen/SQL sites,
  four categorically-safe rules in `ignore`). Every `ignore` / per-file entry carries its reason.
- **Advisory tier — `poe hygiene` (NOT in `all`; nightly / pre-release / agent-triage):** `deadcode`
  (vulture + `.vulture_whitelist.py`), `dup` (jscpd copy-paste, `--threshold` ceiling). Standalone
  advisory not in the bundle: `ps1` (PSScriptAnalyzer over the `.ps1` installers — needs `pwsh`),
  `links-net` (network link crawl), `perf-risk` (repowise's static, no-LLM I/O-in-loop / N+1 /
  blocking-in-async check — metric that informs, not a contract), `mutate` / `fuzz` / `crosshair`
  (adequacy — see below). Each
  tool was chosen by test-driving it on THIS repo (see `vibe/quality-growth-plan.md`); prefer
  standalone Rust/Go binaries (out-of-process → free-threading-safe). These emit file:line / JSON
  findings — feed them to the repowise-indexed navigator for agent-driven fixes rather than fixing blind.

## Refactoring

- **Navigate by symbols, not text sweeps or research subagents.** Use the `LSP` tool (basedpyright) —
  `findReferences` / `incomingCalls` / `documentSymbol` — to map callers before touching a symbol. It's
  exact and far cheaper than grep-and-read.
- **Mechanical edits go through a codemod.** For repo-wide renames/moves or splitting a big module
  (`app/controller.py` is the standing example), author a **LibCST** or **ast-grep** codemod and apply it
  rather than hand-rewriting a large file — formatting, comments, and goldens survive untouched.
- **Extract behind a stable seam.** Move logic into a new module as functions taking the host
  (`def f(reader: Reader)`) and leave thin delegating methods, so the public API is unchanged and both
  mypy and basedpyright stay green (a `self: Subclass` mixin trips mypy's supertype rule). Repoint any
  `monkeypatch.setattr` to the symbol's new lookup site, or tests raise `AttributeError`.

## Comments

Agentic from day one → comments trend to LLM over-explaining. Treat comment bloat like code cognitive
complexity: something to cut, not preserve. The dense multi-clause comments already in the tree are the
target, not the model.

- **Information delta only.** Comment the *why*, a gotcha, a constraint, a bug/PR ref — never the
  *what* (`# loop over the dicts`). Delete zero-delta echoes.
- **Distill to the irreducible signal.** Keep the delta, cut the words — no teaching tone, no
  narrative, no hedging. One tight clause beats a paragraph. A long comment is a smell: compress it or
  justify it. The `ipc.py` Windows-pipe lesson is one line ("single-threaded `pump()` was a no-op on
  the named pipe → reader thread"), not an essay.
- **No process scars.** No `(plan R4)`, `Stage N`, "as discussed".
- **Not a gate.** "Echoes the code" / "too verbose" is semantic, not AST-matchable — a review
  discipline, not a `poe` check.

## Mutation auditing

- **The pure core is mutation-audited** — `sub_index`, `fsrs`, and (as they gain focused tests)
  `scoring`, `tokenize`, `render.flow`, `deinflect`. `uv run poe mutate [module]` (cosmic-ray,
  git-guarded, opt-in — minutes/module, NOT in `poe all`). Glue (controller/mpvio) is excluded:
  I/O-bound, floods equivalent mutants.
- **Survivors → Hypothesis, not a score.** A surviving mutant is a coordinate to harden, not a number
  to maximise (equivalent mutants make 100% unreachable). Kill the *class* with a property —
  boundary / round-trip / spec-oracle — and pin the shrunk input as `@example` so the kill is
  deterministic on every rerun (see `tests/test_sub_index_properties.py`). Re-run `poe mutate` to
  confirm the score moved.
- **cosmic-ray on 3.14t re-enables the GIL** via SQLAlchemy (harness only — the test subprocess still
  runs free-threaded). Expected, not a regression.

## Fuzzing & symbolic checks

Three test-adequacy techniques beyond the unit/property suite, each opt-in and NOT in `poe all`. They
attack the pure core from different angles — mutation (does a test catch the change?), coverage-guided
random bytes, and symbolic solving — so they find different classes of bug.

- **`poe mutate`** — mutation auditing (cosmic-ray). See "Mutation auditing" above.
- **`poe fuzz`** — coverage-guided fuzzing of the subtitle parser (atheris/libFuzzer): byte-mutation
  reaches paths a structured generator won't. Contract: `parse_cues` is robust — any input returns a
  possibly-empty list, never raises. A crasher drops a `crash-*` repro (gitignored) → shrink, add as a
  regression golden / `@example`, fix.
- **`poe crosshair`** — CrossHair runs the existing Hypothesis property tests under a **z3 symbolic
  backend** (via the `crosshair` Hypothesis backend, registered in `conftest.py` only when installed):
  an SMT solver finds exact-boundary counterexamples random search misses. Slow (~15 s/property) → opt-in.
  HypoFuzz was **test-driven** (ran clean, found nothing atheris/CrossHair didn't) and **not adopted** —
  its licence is `LicenseRef-HypoFuzz` (custom/source-available, not FOSS), unfit to commit here, and the
  trio already covers pure-core adequacy.
- **Both `fuzz` and `crosshair` are pinned to CPython 3.13 in SEPARATE envs** (`.venv-fuzz` / `.venv-cx`
  via `UV_PROJECT_ENVIRONMENT`): atheris (libFuzzer) and z3 are C-extensions that can't load under the
  free-threaded 3.14t default — the same out-of-process 3.13 crutch as `invariants-taint`. The target
  code is pure-Python and runs fine on 3.13. **Never run `uv run --python 3.13` against the default
  env** — it recreates `.venv` as 3.13; always set `UV_PROJECT_ENVIRONMENT=.venv-{fuzz,cx}`.
