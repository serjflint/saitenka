# AGENTS.md — Saitenka (Japanese immersion tooling)

Guidance for AI agents and developers working in this repo. Feature docs: the docs site
(`docs/`, `mkdocs.yml` → [saitenka.readthedocs.io](https://saitenka.readthedocs.io); run/usage/dev live
there), `overlay/README.md` (renderer design), and `overlay/ARCHITECTURE.md` (module map + data flow).

## Planning artifacts

- **`CHANGELOG.md`** ([Keep a Changelog](https://keepachangelog.com/)) — shipped changes; drafted with
  [git-cliff](https://git-cliff.org/) (`uv run poe changelog`) then **hand-reviewed**, never shipped raw.
  Future direction and trackable work → GitHub issues/milestones. Scratch (drafts, working
  notes, to-be-filed issue bodies) → `vibe/` (git-ignored) — **never `.agents/`, which is durable-only**.
- **Commits:** frequent, small, focused [Conventional Commits](https://www.conventionalcommits.org)
  (`feat:`/`fix:`/`docs:`/…), one logical change each. No tool-attribution trailers.
- **`.agents/skills/`** — repo-local agent skills; each is a
  `SKILL.md` (procedure the always-on rules here defer to) plus a `scripts/smoke.sh` rot-guard. Codex
  discovers this directory directly. For Claude Code auto-discovery, create a **local** symlink
  (`.claude/` is git-ignored, never committed):
  `ln -s ../.agents/skills .claude/skills`.
- **`.agents/rules/`** — repo-local always-on rules (short, standing constraints). Currently
  `searching.md` (don't `find … | xargs grep`; prefer Grep/Glob or `git grep`).

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
- **Dev gate (no CI):** `uv run poe all` is the pre-push gate — its task list is `all` in
  `[tool.poe.tasks]` (the source of truth: static checks + the suite + supply-chain). `cov` runs `-n auto`
  with a superset marker set, so it is the functional run too — the standalone `test` stays the inner loop,
  not a third suite run. Run it before
  pushing. The task-by-task runbook, how to read each failure, the advisory `poe hygiene` tier, and the
  free-threaded / 3.13-pinned-env traps live in the **dev-gate skill** (`.agents/skills/dev-gate/`) — consult it. The real tasks live
  in `overlay/`; the repo-root `pyproject.toml` is a poe shim, so `uv run poe <task>` works from the repo
  root or `overlay/`. Standing constraints while editing (don't relitigate these): `lint` is an
  **explicit** ruff select (never `ALL`) with flake8-bandit `S` folded in — justify each `# noqa: S…` and
  each `ignore`; `complexity` is ratcheted against `overlay/complexipy-snapshot.json` — regenerate only
  after a deliberate refactor, never to silence a regression; the only copyleft allowed in the graph is
  our own GPL `deinflect`; new advisory tools are test-driven on THIS repo first (`vibe/quality-growth-plan.md`),
  preferring standalone out-of-process binaries (free-threading-safe).
- **Inner loop (not a gate):** `uv run poe affected` runs only the tests a change can touch (ruff
  dependency-graph reverse-closure + full-run fallback on blind spots) — seconds instead of the ~32s full
  `poe test`, for the edit→feedback cycle. It over-approximates, never under-selects; `poe all`/`poe
  test-ft` stays the correctness net before push. `--base origin/main` to check a committed branch.

## Refactoring

- **Navigate by symbols, not text sweeps or research subagents.** Use the `LSP` tool (basedpyright) —
  `findReferences` / `incomingCalls` / `documentSymbol` — to map callers before touching a symbol. It's
  exact and far cheaper than grep-and-read.
- **Mechanical edits go through a codemod.** For repo-wide renames/moves or splitting a big module
  (`app/controller.py` is the standing example), author a **LibCST** or **ast-grep** codemod and apply it
  rather than hand-rewriting a large file — formatting, comments, and goldens survive untouched. LibCST
  lives in the opt-in `codemod` dependency group (its pyo3 build has no free-threaded 3.15t wheel, so it's
  kept out of the default `dev` env): run codemods with `uv run --group codemod <script>`.
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
  justify it.
- **No process scars.** No `(plan R4)`, `Stage N`, "as discussed".
- **Not a gate.** "Echoes the code" / "too verbose" is semantic, not AST-matchable — a review
  discipline, not a `poe` check.

## Documentation

Same over-explaining trend as **Comments** above, one layer up: duplicated specifics rot faster than
prose bloat does, because two copies of a fact drift independently and one goes stale silently.

- **One canonical source per fact.** A task list, a command, a version number — state it once, where
  it's authoritative (`pyproject.toml`'s `[tool.poe.tasks]` for what runs; `.agents/skills/` for the
  procedure; the code itself for behavior), and *point* to it everywhere else. Never re-describe steps
  another doc or skill already owns.
- **Information delta only, distilled.** Same test as comments: does this sentence tell the reader
  something the heading/command name doesn't already? Cut the paragraph restating the heading.
- **No process scars.** No `(Stage N)` section tags — same rule as comments, same reason.
- **High-level over step-by-step, when a canonical walkthrough exists.** README explains *what* and
  *why*; the docs site (or a skill) owns the *how* in full detail. A second copy of the steps is the bug,
  not the fix.
- **Test it like a reader, not the author.** Before calling a doc done, hand it to a fresh agent — no
  conversation context — with the questions a real reader would ask. A doc that makes a fresh reader
  invent something is the doc's bug, not the reader's.
- **Not a gate.** Same as comments — a review discipline, not a `poe` check.

## Testing

Forward-looking discipline: every **new** test follows these; existing tests migrate opportunistically
when you touch them, never in a big-bang sweep. Tiers run via `poe test` (fast: `-n auto`, excludes
`slow`/`integration`/`requires_display`/`e2e`) and `poe test-ft` (`PYTHON_GIL=0`, whole-suite
free-threaded — the FT check is the *suite run*, not a per-test assert). Adequacy beyond the unit suite
is its own contract: see **Mutation auditing** and **Fuzzing & symbolic checks** below.

The rules below are the *invariants*; the step-by-step procedure for authoring one test — decision tree,
recipes against the real fakes, when-not-to-test — is the **write-test skill** (`.agents/skills/write-test/`).
Consult it when adding or rewriting a test.

- **Test the seam, not the shell.** Pure core (`sub_index`, `tokenize`, `scoring`, `render.flow`,
  `anki.build_note`, deinflect behind the chokepoint) gets fast default-tier tests against constructed
  objects; anything crossing a subprocess/socket/display is `integration` or `live` and goes through
  the existing fakes (`FakeMpvServer`, `FakeTransport`, `FakeAnki`, `Driver`) — **never a real `mpv`**
  outside the single `live`/`smoke-live` gate. Humble-object: don't unit-test I/O glue (it floods
  equivalent mutants and duplicates the integration job).
- **Assert observable behaviour.** Return value, emitted IPC message, written note dict — never a
  private attribute or a mock call-count. This repo is already classicist (≈1500 plain asserts, ~9
  mocks); keep it that way.
- **`monkeypatch` is the sanctioned seam, `mock` is not.** Injecting a fake or repointing an extracted
  symbol via `monkeypatch.setattr` is correct and normal here (see **Refactoring**). Do **not** reach
  for `unittest.mock`/`MagicMock` to fake *internal* behaviour — construct the real collaborator or use
  a `Fake*`. Mocking an out-of-process boundary you don't own (rare) is the only exception.
- **Construct over fixture; function scope only.** Instantiate in the test body (or a small local
  `Fake*`); reserve `@pytest.fixture` for resource lifecycle/override/parametrised clients. No
  `scope="module"|"session"` — cross-test shared mutable state is a no-GIL data-race foot-gun (`deque`
  et al. are unsafe under free-threading).
- **One act per test; name for the scenario.** One trigger + one assertion chain; split multi-act
  tests. Match the existing descriptive style (`test_doctor_warns_when_subminer_running`), not a rigid
  template.
- **Assertion ladder.** Plain `assert` by default → `dirty-equals` (`IsPartialDict`, `IsStr(regex=…)`,
  `Contains`) when a dict/IPC/Anki-note payload has keys you don't care about or volatile
  ids/timestamps → an existing **golden** for large structured output. A golden diff **is** the
  behaviour review — re-bless deliberately (e.g. a `unidic-lite` bump), never regenerate to green.
- **Determinism.** No wall-clock, ambient `random`, or unrestored env in a test; `pytest-randomly`
  stays on to surface order-coupling. Inject the clock at the seam (the suite already does this via
  `monkeypatch`; reach for `time-machine` only if a real clock-flake appears).
- **Timeouts on anything that can hang.** New `integration`/`live` tests touching a socket/subprocess
  carry `@pytest.mark.timeout(5)` (30 for `live`). Opt-in per test — there is no global default, so
  legitimately-slow tests are unaffected.
- **Don't add a plugin when a pattern exists.** Data → Hypothesis strategies; flexible asserts →
  `dirty-equals`; IPC → the transport `Fake*`. A new test dependency is a `quality-growth` decision
  (test-drive it on this repo first), not a reflex.

## Mutation auditing

- **The pure core is mutation-audited.** The canonical allowlist is `TARGETS` in `tools/mutate/run.py`
  (query it with `uv run poe mutate --list`) — a module joins only when it is pure/algorithmic, has
  focused unit+property tests, and a human has run and triaged an initial campaign (the add/remove
  procedure is the `run.py` docstring; a rot-guard test keeps the paths honest). `uv run poe mutate
  [module]` (cosmic-ray, git-guarded, opt-in — minutes/module, NOT in `poe all`; a complete campaign is
  reused, `--force` rebuilds). Glue (controller/mpvio) is excluded: I/O-bound, floods equivalent mutants.
- **Survivors → Hypothesis, not a score.** A surviving mutant is a coordinate to harden, not a number
  to maximise (equivalent mutants make 100% unreachable). Kill the *class* with a property —
  boundary / round-trip / spec-oracle — and pin the shrunk input as `@example` so the kill is
  deterministic on every rerun (see `tests/test_sub_index_properties.py`). Re-run `poe mutate` to
  confirm the score moved.
- **cosmic-ray on 3.14t re-enables the GIL** via SQLAlchemy (harness only — the test subprocess still
  runs free-threaded). Expected, not a regression.
- **The Sharpen loop** consumes this: an idle-time, one-module-per-run process that *sharpens the existing
  tests* (fixes bugs in the tests) using mutation as its Efficacy axis + a `poe test-lint` conformance
  linter, proposes via an isolated author→skeptic→judge review (two independent UPHOLDs to ship), and
  never merges. Design + reader's guide: `.agents/sharpen/GUIDE.md` (+ `SPEC.md`, `harness.js`, the
  committed `.ledger.sharpen.jsonl`). Not part of `poe all`.

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
  Do **not** adopt HypoFuzz — its licence is `LicenseRef-HypoFuzz` (custom/source-available, not FOSS),
  unfit to commit here, and the trio already covers pure-core adequacy.
- **`fuzz`/`crosshair` need CPython 3.13** — atheris (libFuzzer) and z3 are C-extensions that can't load
  under the free-threaded 3.14t default; the pure-Python target runs fine on 3.13. **Never run `uv run
  --python 3.13` against the default project env** (it recreates the project `.venv` as 3.13). How each
  task pins its own 3.13 env is defined in the poe task itself (`pyproject.toml [tool.poe.tasks]`) — the
  single source of truth; don't duplicate the invocation here.
