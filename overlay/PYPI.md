# 再点火 (Saitenka) — learn Japanese from the video you're already watching

Saitenka turns **mpv** into an immersion workstation: Japanese subtitles get **FSRS-aware word
coloring**, hovering a word opens a **Yomitan-style multi-dictionary tooltip**, and one key **mines**
the sentence — audio, screenshot, reading, pitch, frequency — straight into **Anki**. Everything is
drawn into mpv's *own* video surface, so there's no second window and none of the Windows
overlay/fullscreen breakage that plagues external overlays.

![Saitenka's in-mpv overlay: the word 読む highlighted in a subtitle line with its multi-definition Yomitan-style tooltip open beside it.](https://raw.githubusercontent.com/serjflint/saitenka/main/docs/screenshot-hover.jpg)

- **再点火 = "re-ignition"** — built to make picking study back up frictionless after a long break.
- **Local-first and grounded:** readings and pitch always come from dictionaries, never a language model.

## What it does

- **FSRS-aware subtitle coloring** — an optional copy of Anki's database distinguishes learning, young,
  mature-known, and forgotten words (so "known" means *actually remembered right now*), and **N+1**
  sentences — exactly one unknown word, the ideal thing to mine — are highlighted.
- **Multi-dictionary tooltip** on hover — ordered definitions, ruby, frequency pills, pitch-accent,
  clickable cross-references, in-tooltip word scanning, wildcard search. Imports your **Yomitan**
  dictionaries.
- **One-key + bulk Anki mining** via AnkiConnect — sentence audio, clean screenshot, reading, glossary,
  frequency, with dedup and FSRS-aware tagging.
- **No second window** — the tooltip, colored subs, and mining UI composite into mpv's OSD over JSON-IPC:
  one surface, airspace-safe, fullscreen-safe, cross-platform (Linux · macOS · Windows).

## Install

The recommended install is the self-bootstrapping release bundle (it also sets up mpv + ffmpeg and the
auto-start mpv plugin) — see the **[Quick start](https://github.com/serjflint/saitenka#quick-start)**.

For a pip/uv-native install of just the Python package:

```bash
uv tool install saitenka          # or: pipx install saitenka
saitenka setup                    # installs mpv/ffmpeg + the auto-start plugin, writes config
saitenka run video.mkv            # hover a word → tooltip; Ctrl+m → mine
```

The optional inflection-chain display is a **GPL-3.0** add-on (derived from Yomitan); the core is
**Apache-2.0**. A `[full]` install is therefore GPL-3.0 — see
[LICENSING.md](https://github.com/serjflint/saitenka/blob/main/LICENSING.md).

## Full documentation

Everything — how it works, how it compares, where it fits in a media-server setup, requirements, and the
feature tour — lives in the **[full README on GitHub](https://github.com/serjflint/saitenka#readme)**.
