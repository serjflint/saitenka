# Saitenka

<p align="center">
  <em>Learn Japanese from the video you're already watching.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.13%2B-blue.svg" alt="Python 3.13+">
  <img src="https://img.shields.io/badge/built%20with-uv-de5fe9.svg" alt="Built with uv">
  <img src="https://img.shields.io/badge/platforms-Linux%20%C2%B7%20macOS%20%C2%B7%20Windows-blue.svg" alt="Platforms">
</p>

**Saitenka (再点火)** draws a Yomitan-style study overlay directly onto **mpv**'s video: Japanese
subtitles colored by what your Anki/FSRS memory already knows, a hover → multi-dictionary tooltip with
pitch and frequency, and **one-key sentence mining** into Anki — without ever leaving the player.

It's **local-first**: readings and pitch always come from *your* dictionaries, never from a language
model. It runs on Python 3.13+, including the free-threaded (no-GIL) build.

![Hover tooltip over a colored subtitle](screenshot-hover.jpg){ loading=lazy }

## What it does

<div class="grid cards" markdown>

-   :material-palette:{ .lg .middle } **FSRS subtitle coloring**

    ---

    Every word is colored by how well you know it — driven by your real Anki/FSRS state — so you see
    what's new at a glance instead of re-reading what you've mastered.

    [:octicons-arrow-right-24: Features](usage/features.md)

-   :material-book-open-variant:{ .lg .middle } **Hover → dictionary tooltip**

    ---

    Point at a word for a Yomitan-like popup: multiple dictionaries, pitch accent, frequency, and the
    de-inflected form — composited into mpv's OSD, no second window.

    [:octicons-arrow-right-24: Features](usage/features.md)

-   :material-cards:{ .lg .middle } **One-key Anki mining**

    ---

    A single keypress turns the current line into a card — sentence, audio, screenshot, definition —
    straight into Anki over AnkiConnect.

    [:octicons-arrow-right-24: Quickstart](start/quickstart.md)

-   :material-speedometer:{ .lg .middle } **Fast enough to live in your player**

    ---

    Warm hover in ~0.5 ms, cold first paint ~22 ms (on an M3 Pro). It stays out of the way of playback.

    [:octicons-arrow-right-24: Benchmarks](why/benchmarks.md)

</div>

## Get started

Saitenka installs as a [uv](https://docs.astral.sh/uv/) tool:

```bash
uv tool install "saitenka[full]"
saitenka setup   # installs mpv+ffmpeg, writes config, imports dictionaries
saitenka run <your-episode.mkv>
```

Then hover a colored word and press ++ctrl+m++ to mine it.

[:material-download: Install guide](start/install.md){ .md-button .md-button--primary }
[:material-rocket-launch-outline: Quickstart](start/quickstart.md){ .md-button }
[:material-scale-balance: Why Saitenka?](why/comparisons.md){ .md-button }

## Where to go next

- **[Install](start/install.md)** and **[Quickstart](start/quickstart.md)** — from zero to your first
  mined card.
- **[Usage](usage/features.md)** — features, [keyboard shortcuts](usage/shortcuts.md),
  [configuration](usage/configuration.md), and the [CLI reference](usage/cli.md).
- **[Why Saitenka](why/comparisons.md)** — how it compares to SubMiner and Autocards, and
  [how fast it is](why/benchmarks.md).
- **[Contributing](contributing/architecture.md)** — architecture, development, and the changelog.
  Future direction lives in [GitHub issues](https://github.com/serjflint/saitenka/issues).
