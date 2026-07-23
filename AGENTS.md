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
  regression), tests (incl. free-threaded), coverage floor 85%. Run it before pushing. `poe
  arch-report` (pyscn) is a separate, non-gating coupling/complexity report guiding the `controller.py`
  split. The real tasks live in `overlay/`; the repo-root `pyproject.toml` is a non-package poe shim
  that delegates there, so `uv run poe <task>` (all, test, bench, smoke-live, …) works from **either
  the repo root or `overlay/`**.

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
