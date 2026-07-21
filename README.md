# 再点火 (Saitenka) — immersion tooling for Japanese

Open-source tooling for sentence-mining Japanese from video with **mpv + Anki + Yomitan**:
an in-mpv dictionary/mining overlay plus an Anki/FSRS deck engine. Extracted from a personal
post-JLPT-N2 study project; the personal notes/vault live in a separate private repo.

## What's here

- **`overlay/`** — the in-mpv **Yomitan-style overlay** (`saitenka-overlay`). Japanese subtitles with
  FSRS-aware word coloring, hover → multi-dictionary tooltip, **one-key + bulk mining** to Anki
  (Lapis cards with sentence audio + screenshot), on-demand English reveal, and jimaku subtitle
  fetch — all composited into mpv's *own* OSD surface (one surface, no second window → no Windows
  airspace/fullscreen bugs). Python + Pillow renderer; see `overlay/README.md`.
- **`tools/`** — the **Anki / FSRS engine**: FSRS-based dictionary ranking, field normalization,
  provenance annotation, deck building, refile-by-review-state, and an anime chooser. Frequency dicts
  are user-supplied — drop them in `tools/freq/` or point `--freq-dir` / `$SAITENKA_FREQ_DIR` at them.
- **`install/`** — cross-platform installers (macOS / Windows / Linux) + a `doctor` health check and
  `make_bundle.py`, which builds a single self-contained zip you can hand to a friend.
- **`deinflect/`** — *optional* **GPL-3.0** add-on (`saitenka-overlay-deinflect`): the Yomitan-derived
  inflection-chain display (🧩 `-て « -いる « -た`). Kept separate so the core stays Apache-2.0; the
  overlay runs fine without it. See [LICENSING.md](LICENSING.md).

## Quick start

The overlay installs as a `uv` tool with its own Python and dependencies:

```bash
git clone https://github.com/serjflint/saitenka.git
cd saitenka
bash install/install-macos.sh        # macOS  (Windows: install/install-windows.ps1)
```

Or run it straight from the checkout while hacking on it:

```bash
cd overlay
uv sync
uv run python examples/mpv_reader.py            # smoke run: generated clip + hover tooltip
uv run python examples/mpv_reader.py video.mkv --color --mine   # real episode
```

Full run/test walkthrough: **`overlay/RUNNING.md`**. Feature tour: **`overlay/README.md`**.

## Requirements

- **mpv** and **ffmpeg** on `PATH`
- **[uv](https://docs.astral.sh/uv/)** (provides the Python interpreter + deps — no system Python needed)
- Optional: **Anki** with the **AnkiConnect** add-on (for FSRS-aware coloring and mining)
- Optional: **Yomitan** term-bank dictionaries (import in your browser; point `overlay.toml` at the zips)

## Project conventions

Python is standardized on **`uv`** (never bare `python`/`pip`/`venv`). LLM use is optional, local-first,
and grounded (readings/pitch always come from dictionaries, never a model). See **`AGENTS.md`** for the
full guidance for contributors and AI agents. No CI — `uv run poe all` in `overlay/` is the pre-push gate
(lint, types, 400+ tests, coverage floor 85%).

## License

**[Apache-2.0](LICENSE)** for the core (`overlay/`, `tools/`, `install/`). The optional `deinflect/`
add-on is **GPL-3.0** (derived from Yomitan) — installing it makes the *combined* work GPL-3.0. Full
map: **[LICENSING.md](LICENSING.md)**. Vendored fonts are SIL OFL; frequency dicts are user-supplied
(not shipped).
