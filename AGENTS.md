# AGENTS.md — Saitenka (Japanese immersion tooling)

Guidance for AI agents and developers working in this repo. Feature docs: `overlay/README.md` (renderer
+ reader tour) and `overlay/RUNNING.md` (run/test walkthrough).

## Planning artifacts

Shipped changes go in **`CHANGELOG.md`** ([Keep a Changelog](https://keepachangelog.com/) format,
curated for readers). Future direction lives in **`ROADMAP.md`**; granular, trackable work goes in
issues/milestones. Any local planning scratch belongs in `vibe/`, which is **git-ignored** — it is not
a published artifact.

**Commits.** Commit **frequently** — small, focused [Conventional Commits](https://www.conventionalcommits.org)
(`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, …), one logical change each; don't batch unrelated work.
No tool-attribution trailers.

**Changelog.** Drafted from history with [git-cliff](https://git-cliff.org/) (config: `cliff.toml`;
`uv run poe changelog` previews the unreleased section) and then **hand-reviewed** for readers — never
shipped raw. `CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/).

## Python: always use `uv`

This repo standardizes on **[uv](https://docs.astral.sh/uv/)** for everything Python. Do **not** use bare
`python`, `pip`, `venv`, `virtualenv`, or `pipx`.

- **Run scripts:** `uv run script.py` — never `python script.py`.
- **One-off CLIs:** `uvx <tool>`; persistent CLIs: `uv tool install <tool>`.
- **Dependencies:** `uv add <pkg>` / `uv add --dev <pkg>`; lock via `uv.lock`; `uv sync` to install.
- **Environments:** `uv venv` (auto-managed by `uv run`) — never `python -m venv`.
- **Standalone scripts:** declare deps with PEP 723 inline metadata (`# /// script … # ///`) and run with
  `uv run script.py`, so the script is self-contained.
- **Python version:** pin with `uv python pin <version>`; uv provides the interpreter (no system Python).

Rationale: uv is fast, reproducible (lockfile), and isolates environments — no global-pip pollution.

## Project conventions

- **Anki access:** read-only via a **copy** of `collection.anki2` (never the live DB while Anki is open) or
  via AnkiConnect; FSRS state (`s`/`d`) is in `cards.data`.
- **LLMs:** optional, **local-first**, and **grounded (RAG)** — operate on provided authoritative sources,
  never parametric facts (readings/pitch stay from dictionaries).
- **Tokenizer:** SudachiPy / MeCab+UniDic; mind the de-inflection matching trap. Goldens in `overlay/`
  encode `unidic-lite`'s tokenization — bumping it legitimately moves goldens; re-bless deliberately.
- **Dev gate (no CI):** `uv run poe all` — lint (ruff), types (mypy + pyright blocking, pyrefly + ty
  advisory), tests (incl. free-threaded), coverage floor 85%. Run it before pushing. The real tasks
  live in `overlay/`; the repo-root `pyproject.toml` is a non-package poe shim that delegates there, so
  `uv run poe <task>` (all, test, bench, smoke-live, …) works from **either the repo root or `overlay/`**.
