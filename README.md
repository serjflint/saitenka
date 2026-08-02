# 再点火 (Saitenka) — learn Japanese from the video you're already watching

[![PyPI](https://img.shields.io/pypi/v/saitenka.svg)](https://pypi.org/project/saitenka/)
![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.13%20%7C%203.14%20%7C%203.15-blue.svg)
![Built with uv](https://img.shields.io/badge/built%20with-uv-de5fe9.svg)
![Platforms](https://img.shields.io/badge/platforms-Linux%20%C2%B7%20macOS%20%C2%B7%20Windows-blue.svg)
[![Docs](https://img.shields.io/badge/docs-saitenka.readthedocs.io-blue.svg)](https://saitenka.readthedocs.io)

Saitenka turns **mpv** into an immersion workstation: Japanese subtitles get **FSRS-aware word
coloring**, hovering a word opens a **Yomitan-style multi-dictionary tooltip**, and one key **mines**
the sentence — audio, screenshot, reading, pitch, frequency — straight into **Anki**. Everything is
drawn into mpv's *own* video surface, so there's no second window and none of the Windows
overlay/fullscreen breakage that plagues external overlays.

![Saitenka's in-mpv overlay over a frame of the 1917 film Namakura Gatana: the word 読む highlighted in the subtitle line, with its multi-definition Yomitan-style dictionary tooltip open beside it.](docs/screenshot-hover.jpg)

<sub>Saitenka running on a public-domain still — *[Namakura Gatana](https://archive.org/details/kouichi-junichi-namakura-gatana-1917-4-minute-restored-version)* (なまくら刀, 1917; dir. Kōuchi Jun'ichi), restored by the National Film Archive of Japan. Open-license dictionary only ([Jitendex](https://jitendex.org), CC BY-SA); the subtitle is a demo line.</sub>

📖 **Full documentation → [saitenka.readthedocs.io](https://saitenka.readthedocs.io)** — install,
configuration, keyboard shortcuts, the CLI reference, and how it compares.

- **再点火 = "re-ignition"** — built to make picking study back up frictionless after a long break.
- Local-first and grounded: readings and pitch always come from dictionaries, never a language model.

> **New here?** Jump to [Quick start](#quick-start) — one command installs everything and wires the
> overlay into every future mpv launch.

## Table of contents

- [Why](#why)
- [How it works](#how-it-works)
- [Features](#features)
- [How it compares](#how-it-compares)
- [Quick start](#quick-start)
- [What's in the repo](#whats-in-the-repo)
- [Requirements](#requirements)
- [Conventions](#conventions)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## Why

Sentence-mining from native video is the highest-leverage way to grow vocabulary, but the usual rig is
a fragile chain of a browser texthooker, a clipboard bridge, a separate overlay window, and manual card
assembly. The overlay-window approach in particular fights the OS: on Windows it flickers, loses hover
focus, and breaks in fullscreen because a second window can never share the video's airspace.

This practice isn't new, and Saitenka didn't invent it — the guides that popularized it are what taught
the author to mine from native video around the **N3** level.
**[TheMoeWay](https://learnjapanese.moe/animejp/)** makes the *why*: anime is authentic input made **by
natives, for natives** — scripted native speech across every register (via *yakuwarigo*, role language),
not textbook Japanese watered down for learners. **[Anacreon's original mpv script](https://anacreondjt.gitlab.io/docs/mpvscript/)**
and the **[Animecards "Mine from anime"](https://animecards.site/minefromanime/)** workflow make the
*how*: while you watch, capture the sentence, its audio, a screenshot, and the target word straight into
Anki — turning passive viewing into vocabulary you actually encountered. Saitenka is a direct descendant
of that loop; it keeps the method and removes the friction that was still in it.

Saitenka solves three problems:

- **No second window.** The dictionary tooltip, colored subtitles, and mining UI are composited into
  mpv's own OSD surface over its JSON-IPC connection — one surface, airspace-safe, fullscreen-safe.
- **No busywork loop.** Watch → colored subs → hover → dictionary → one-key mine (sentence audio +
  clean screenshot + reading/pitch/frequency, deduped, FSRS-tagged) happens without leaving the video.
- **Study you already forgot resurfaces.** Word coloring is sourced from your Anki/**FSRS** (Anki's
  spaced-repetition scheduler) review state, so "known" means *actually remembered right now*, and
  **N+1** sentences — those with exactly one unknown word, the ideal thing to mine — are highlighted.

## How it works

- **Renderer:** Python + [Pillow](https://python-pillow.org/) rasterizes the rich tooltip (structured
  content, ruby furigana, pitch/frequency pills, inflection chain) to a BGRA bitmap and bolts it into
  mpv via `overlay-add` over JSON IPC — no GL, no FFI, no second process drawing on screen.
- **Cross-platform IPC:** a background reader thread speaks mpv's JSON-IPC over a Unix socket
  (macOS/Linux) or a Windows **named pipe**, and *joins* a shared socket so it coexists with other mpv
  scripts.
- **Plugin mode (full-auto):** an installed `saitenka.lua` user-script makes **every** mpv launch
  auto-start the overlay in attach mode — open any video in mpv and the overlay is just there.
- **Language pipeline:** [fugashi](https://github.com/polm/fugashi) + UniDic tokenize; a Yomitan-derived
  deinflector recovers dictionary forms; lookups hit an on-disk **SQLite** index built once from your
  Yomitan dictionaries (low, near-constant RAM even for large monolingual dictionaries).
- **Free-threaded (optional):** on a free-threaded Python **3.14t** build the renderer parallelizes
  across cores; it also runs fine on a standard build (single-threaded rendering). The minimum is 3.13,
  and `uv` fetches the right interpreter for you.

## Features

- FSRS-aware subtitle **word coloring** + JLPT underlines + N+1 targeting. An optional copy of Anki's
  database distinguishes learning, young, mature-known, and forgotten words without touching the live
  collection.
- Hover → **multi-dictionary tooltip**: ordered definitions, ruby, frequency pills, pitch-accent,
  clickable cross-references, in-tooltip word scanning, wildcard search.
- **One-key + bulk mining** to Anki (Lapis-style cards — a popular community Anki note type): sentence
  audio, clean screenshot, reading, glossary, frequency, structured provenance tags — with dedup and a
  post-mine card preview.
- **Watch-party controls:** toggle hover auto-pause, switch between JP-only, EN-only, and JP+EN, or
  fall back to English immediately while Japanese providers search without pausing or switching tracks.
- A whole-episode **subtitle panel**, move-safe deferred-capture backlog, playback-neutral episode
  analysis, and opt-in local session history. Press `F1` for the effective shortcut reference.
- Background subtitle fetch from **jimaku.cc** and the opt-in **TsukiHime** provider, with an explicit
  non-switching retry shortcut.
- Import dictionaries from **Yomitan** — both standard dictionary `.zip`s and a full Yomitan database
  export (streamed, so a multi-GB export never loads into memory).
- A `doctor` command that checks the whole environment and a one-command `setup` wizard.

## How it compares

Saitenka, **[SubMiner](https://github.com/ksyasuda/SubMiner)**, and
**[Autocards](https://learnjapanese.moe/autocards/)** all get Japanese vocabulary from video into Anki,
from three different angles: Saitenka is a **grounded, FSRS-driven engine composited inside mpv's own
surface**; SubMiner is a **feature-broad Electron app** with turn-key desktop installers and a large
integration surface; Autocards is a **retroactive back-filler** that attaches media to text-only cards
you made elsewhere. It's a map of trade-offs across different jobs, not a scoreboard.

**Why reach for Saitenka:** a fast, single-surface engine that draws straight into mpv — no second window,
fullscreen/airspace-safe; **live FSRS review-state coloring** so forgotten words resurface and **N+1**
sentences are highlighted; a multi-dictionary Yomitan tooltip; and one-key + bulk mining, all grounded
(readings/pitch from dictionaries, never an LLM). Reach for **SubMiner** for the widest set of turn-key
integrations and a packaged desktop app; reach for **Autocards** to bulk-attach media to cards after the
fact (Windows-only). The workflows compose.

📊 **Full capability matrix, and where Saitenka fits in a media-server / watch-tracking rig →
[Why Saitenka → How it compares](https://saitenka.readthedocs.io/en/latest/why/comparisons/).**

## Quick start

**1. Install.** The standalone installer bootstraps [`uv`](https://docs.astral.sh/uv/), installs
`saitenka[full]` from PyPI, and runs the `setup` wizard — no clone, no prerequisites:

```sh
# macOS / Linux
curl --proto '=https' --tlsv1.2 -LsSf https://serjflint.github.io/saitenka/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://serjflint.github.io/saitenka/install.ps1 | iex"
```

> Prefer to read it first? Download and inspect before running:
> `curl --proto '=https' --tlsv1.2 -LsSf https://serjflint.github.io/saitenka/install.sh -o install.sh`,
> then `less install.sh && sh install.sh`.

Already have `uv` (or `pipx`)? Skip the script and install from **PyPI** directly:

```sh
uv tool install "saitenka[full]"        # or: pipx install "saitenka[full]"
saitenka setup                          # mpv + ffmpeg, config, and the auto-start mpv plugin
```

The `setup` wizard installs **mpv + ffmpeg** (or prints your distro's install command) and the mpv
plugin so **every future mpv launch auto-starts the overlay**.

**2. Watch.** Open any video in mpv — the overlay attaches automatically. Or launch a file directly:

```sh
saitenka run video.mkv          # hover a word → tooltip; Ctrl+m → mine
```

**3. Update & maintain.** `update` upgrades to the latest, keeping your extras (like `uv self update`):

```sh
saitenka update                 # upgrade to the latest, extras preserved (wraps uv tool upgrade)
saitenka doctor                 # re-check the whole environment any time
saitenka setup                  # re-run the setup wizard (mpv/ffmpeg, config, plugin)
saitenka install-plugin         # (re)install just the auto-start mpv plugin
```

**Feature extras.** `[full]` installs everything below; `update` keeps whatever you have. To change the
set, `uv tool install --reinstall "saitenka[<extra>]"`:

| Extra | Adds | License |
|------|------|--------|
| *(none)* / `[minimal]` | the bare overlay — bring your own Yomitan dictionaries | Apache-2.0 |
| `[jmdict]` | the JMdict English fallback (hover + mined-card glosses when a word isn't in your dicts) | Apache-2.0 |
| `[deinflect]` | the 🧩 inflection-chain display (Yomitan-derived) | **GPL-3.0** |
| `[linux-keyring]` | Linux Secret Service storage for the jimaku key on Python 3.15+ | Apache-2.0 |
| `[full]` | all portable features above (`linux-keyring` remains explicit) | **GPL-3.0** |

Mining prefers *your* dictionaries, so `[jmdict]` is only a fallback. `[deinflect]`/`[full]` pull the
GPL-3.0 add-on — a `[full]` install is therefore GPL-3.0 (see [LICENSING.md](LICENSING.md)). On Linux,
Python 3.13/3.14 install Secret Service support by default; Python 3.15+ uses `JIMAKU_API_KEY` or an
owner-only `$XDG_CONFIG_HOME/saitenka/jimaku.key` unless `[linux-keyring]` is installed, avoiding its
`cryptography` dependency.

Full docs: **[saitenka.readthedocs.io](https://saitenka.readthedocs.io)** (install, usage,
[development](https://saitenka.readthedocs.io/en/latest/contributing/development/)). Renderer design:
**[`overlay/README.md`](overlay/README.md)**.

## What's in the repo

- **[`overlay/`](overlay/)** — the in-mpv overlay (`saitenka`): colored subtitles, hover
  tooltip, mining, English reveal, jimaku fetch, dictionary import, `doctor`/`setup`.
- **[`tools/`](tools/)** — the Anki/FSRS deck engine: FSRS-based dictionary ranking, field
  normalization, provenance annotation, deck building, refile-by-review-state, anime chooser.
  Frequency dictionaries are user-supplied (`tools/freq/` or `--freq-dir` / `$SAITENKA_FREQ_DIR`).
- **[`install/`](install/)** — the cross-platform install scripts (`overlay-install.{sh,ps1}`, served
  from GitHub Pages) and the release helper (`release.py`).
- **[`deinflect/`](deinflect/)** — *optional* **GPL-3.0** add-on (`saitenka-deinflect`): the
  Yomitan-derived inflection-chain display (🧩 `-て « -いる « -た`). Kept separate so the core stays
  Apache-2.0; the overlay runs fine without it. See [LICENSING.md](LICENSING.md).

## Requirements

- **mpv** ≥ 0.37 and **ffmpeg** — `setup` installs these for you (Homebrew / winget); they don't need to
  be on `PATH` beforehand.
- **[uv](https://docs.astral.sh/uv/)** — provides the Python interpreter and dependencies.
- Optional: **[Anki](https://apps.ankiweb.net/)** + the **[AnkiConnect](https://ankiweb.net/shared/info/2055492159)**
  add-on — for FSRS-aware coloring and mining.
- Optional: **[Yomitan](https://github.com/yomidevs/yomitan)** dictionaries — import your `.zip`s (or a
  full database export) and point `overlay.toml` at them.

Every path (config, data, cache, dictionaries, the mpv binary and socket) is overridable in
`overlay.toml` or via environment variables, and resolves to platform-native locations by default.

## Conventions

Python is standardized on **`uv`** (never bare `python`/`pip`/`venv`). LLM use is optional, local-first,
and grounded — readings and pitch always come from dictionaries, never a model. There's no CI; the
pre-push gate is `uv run poe all` in `overlay/` (lint, types, tests, coverage floor 85%). See
**[AGENTS.md](AGENTS.md)** for full contributor / AI-agent guidance.

## License

**[Apache-2.0](LICENSE)** for the core (`overlay/`, `tools/`, `install/`). The optional `deinflect/`
add-on is **GPL-3.0** (derived from Yomitan) — installing it makes the *combined* work GPL-3.0. Full
map: **[LICENSING.md](LICENSING.md)**. Vendored fonts are SIL OFL; frequency and definition
dictionaries are user-supplied (not shipped).

## Acknowledgments

Saitenka stands on a lot of excellent open-source work:

- **[mpv](https://mpv.io/)** — the player, its JSON-IPC protocol, and `overlay-add`, which make the
  single-surface overlay possible.
- **[Yomitan](https://github.com/yomidevs/yomitan)** — the dictionary format, the popup UX this overlay
  reproduces, and the inflection-transform rules the optional deinflector derives from.
- **[Anki](https://apps.ankiweb.net/)** + **[AnkiConnect](https://ankiweb.net/shared/info/2055492159)**,
  and **[FSRS](https://github.com/open-spaced-repetition/fsrs4anki)** by the open-spaced-repetition
  project — the spaced-repetition backbone behind the review-state coloring.
- **[jimaku.cc](https://jimaku.cc/)** — community Japanese subtitles.
- **[fugashi](https://github.com/polm/fugashi)** + **[UniDic](https://clrd.ninjal.ac.jp/unidic/)** for
  tokenization, **[Pillow](https://python-pillow.org/)** for rendering, and
  **[JMdict/KANJIDIC](https://www.edrdg.org/)** (EDRDG) as the built-in fallback dictionary.
- Prior art that shaped the design: **[SubMiner](https://github.com/ksyasuda/SubMiner)** and the
  **[Animecards](https://animecards.site/)** workflow with **[mpv_websocket](https://github.com/kuroahna/mpv_websocket)**.
  The immersion guides that inspired the project are credited in [Why](#why).
