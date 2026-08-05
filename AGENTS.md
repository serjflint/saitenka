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
  `ln -s ../.agents/skills .claude/skills`. Full per-agent activation of the optional intelligence stack
  (repowise + Basic Memory MCP, pyrefly LSP, hooks) → the **`agent-setup`** skill; which tool answers
  what → **`agent-tooling`**.
- **`.agents/mcp/servers.json`** — canonical, agent-agnostic MCP server definitions (no shared cross-agent
  format exists); `agent-setup`'s `render.py` emits each agent's dialect. The generated `.mcp.json` is
  git-ignored.
- **`.agents/rules/`** — repo-local always-on rules (short, standing constraints). Currently
  `searching.md` — the shell-search ban + escape-recovery (fork-bomb why + the enforcing `PreToolUse`
  hook: **Tooling** below). Claude Code auto-loads these via a git-ignored `.claude/rules -> ../.agents/rules`
  symlink (per worktree, like skills; a no-`paths:` rule loads globally — verify with `/memory`); Codex
  reads them directly.

## Tooling — route by intent

Reach for the tool that answers the question, not the reflex. **Never shell-search**
(`grep`/`find`/`rg`/`pgrep`/`ag` fork-bomb here — a `PreToolUse` hook denies them; see
`.agents/rules/searching.md`). Which tool answers what → the **`agent-tooling`** skill; turning the
stack on → **`agent-setup`**.

If one *escapes* the hook it self-replicates and `pkill -9` loses the race (the count *grows*) — **freeze
before kill** (`SIGSTOP`); the full recovery drill is `.agents/rules/searching.md`.

| When you want to… | Use | Not |
| --- | --- | --- |
| find text / a filename | **Grep / Glob** tools, or `git grep` / `git ls-files` | `grep`/`find` in Bash |
| where defined · who-calls · references · call hierarchy | **LSP** tool (`findReferences`, `documentSymbol`, `incomingCalls`) | grep-and-read |
| what/why is this code · blast radius · cross-module design | **repowise** (`get_context`, `get_why`, `get_risk`, `search_codebase`) | guessing, or a wide blind read |
| "where does X happen" when the naming is unknown | **repowise `search_codebase`** (semantic) | grep (needs the exact term) |
| what did we decide · prior context · working notes | **Basic Memory** (`search_notes`, `read_note`) | re-deriving from scratch |
| read a known file | **Read** tool | `cat`/`head`/`tail` |
| process work | `pkill` / `killall` / `kill` | `ps \| grep` / `pgrep` |

repowise is a **grounded summary, not ground truth** — its synthesis can be stale or wrong (the
module/onboarding pages especially; a fresh fact-check found several mislabeled). Trust it for
orientation, *why*, and blast-radius; **confirm any correctness-critical claim against the code** (LSP /
Read). It only helps while the index is fresh (`repowise update`) and the MCP server is up.

## Python: always `uv`

`uv run` / `uvx` / `uv add` — never bare `python`/`pip`/`venv`/`pipx`. Commit `uv.lock`; standalone
scripts declare deps via PEP 723 inline metadata.

## Project conventions

- **Anki access:** read-only via a **copy** of `collection.anki2` (never the live DB while Anki is open) or
  via AnkiConnect; FSRS state (`s`/`d`) is in `cards.data`.
- **LLMs:** optional, **local-first**, and **grounded (RAG)** — operate on provided authoritative sources,
  never parametric facts (readings/pitch stay from dictionaries).
- **Tokenizer:** SudachiPy / MeCab+UniDic; mind the de-inflection matching trap. Goldens in `overlay/`
  encode `unidic-lite`'s tokenization — bumping it legitimately moves goldens; re-bless deliberately.
- **Dev gate:** `uv run poe all` is the fast pre-push gate (CI mirrors it — `.github/workflows/ci.yml`
  runs `poe all` on PRs + pushes to main); `uv run poe pre-release` is the slower pre-tag superset (adds
  the supply-chain, installer, network-link, real-mpv, and bench checks; `release.py` gates on it). Both are defined in `[tool.poe.tasks]` (the source of truth). `cov` is the
  functional run too (a superset marker set), so the standalone `test` stays the inner loop, not a third
  suite run. Run the gate before
  pushing. How to read each failure, the advisory tiers, and the
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
- **Releasing:** `RELEASING.md` is canonical. Locally: curate `RELEASE_NOTES.md` (or lift `##
  [Unreleased]` verbatim) → `poe release-prepare` → merge PR. Then **pushing the `vX.Y.Z` tag is the
  whole publish** — `.github/workflows/release.yml` builds once and ships the GitHub Release **and** PyPI
  (Trusted Publishing, no token). Do NOT run `release.py publish` / `uv publish` — CI owns both; they're
  a CI-down fallback only. Tag the *merged* commit by SHA (works from a worktree, where `main` is
  checked out elsewhere).

## Refactoring

- **Navigate by symbols, not text sweeps or research subagents.** Use the `LSP` tool (pyrefly) —
  `findReferences` / `incomingCalls` / `documentSymbol` — to map callers before touching a symbol. It's
  exact and far cheaper than grep-and-read. pyrefly here is the **nav backend only** — which type-checkers
  the gate runs is `poe types` (SSOT), separate from this. Register the LSP via the `pyrefly-lsp` skill.
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
- **High-level over step-by-step, when a canonical walkthrough exists.** README explains *what* and
  *why*; the docs site (or a skill) owns the *how* in full detail. A second copy of the steps is the bug,
  not the fix.
- **Test it like a reader, not the author.** Before calling a doc done, hand it to a fresh agent — no
  conversation context — with the questions a real reader would ask. A doc that makes a fresh reader
  invent something is the doc's bug, not the reader's.
- **Not a gate.** Same as comments — a review discipline, not a `poe` check.

## Testing

Forward-looking discipline: every **new** test follows these; existing tests migrate opportunistically
when you touch them, never in a big-bang sweep. Tiers run via `poe test` (the fast inner loop) and
`poe test-ft` (the free-threaded whole-suite check — the FT check is the *suite run*, not a per-test
assert); their exact flags and marker exclusions live in `[tool.poe.tasks]` (SSOT). Adequacy beyond the
unit suite is its own contract: see **Mutation auditing** and **Fuzzing & symbolic checks** below.

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
- **Adequacy is not coverage — assert the invariant, not the pixels.** Green + 100% line is a lower
  bound; the shipped bug lives in *covered-but-under-specified* code (a config / feature-combination the
  line ran, but no oracle checked — both motivating regressions were here). Prefer a metamorphic /
  invariant oracle (platform-independent) to a FreeType pixel golden (not), paired with a negative control
  that proves it can fail. Family menu, canonical examples, the config matrix, and the `poe test-live`
  liveness check — the **write-test skill's
  [`references/oracle-catalog.md`](.agents/skills/write-test/references/oracle-catalog.md)** owns them.
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

- **The pure core is mutation-audited** (`poe mutate`, cosmic-ray, opt-in, **NOT in `poe all`**). A
  survivor is a coordinate to harden, **not a score to max** (equivalent mutants make 100% unreachable):
  kill the *class* with a Hypothesis property + a pinned `@example` (`tests/test_sub_index_properties.py`),
  then re-run. The **Sharpen loop** consumes this as its Efficacy axis (`.agents/sharpen/GUIDE.md`).
- The allowlist (`TARGETS` in `tools/mutate/run.py`), what earns a target, glue exclusion, and the
  cosmic-ray/GIL detail → the **`test-adequacy` skill** (`.agents/skills/test-adequacy/`).

## Fuzzing & symbolic checks

Two more opt-in adequacy tools beyond mutation, both **NOT in `poe all`** and 3.13-pinned: `poe fuzz`
(atheris coverage-guided bytes — reaches paths a generator won't; `parse_cues` must never raise) and
`poe crosshair` (z3 symbolic — exact-boundary counterexamples). A crasher / counterexample becomes a
regression `@example`, not a one-off fix. Contracts, the crash-repro workflow, the 3.13-env pinning, and the
HypoFuzz-licence call → the **`test-adequacy` skill**. Never `uv run --python 3.13` against the default env
(it clobbers the 3.14t `.venv`) — run these through their poe task.
