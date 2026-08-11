# Quickstart

This tutorial walks you from a fresh install to your first mined Anki card. It assumes Saitenka is
already installed — if not, start with [Installation](install.md).

!!! info "Prerequisites"
    **mpv** and **ffmpeg** installed (`saitenka setup` handles this, and mpv need not be on `PATH`).
    For mining (Step 5), **Anki must be open** with the **AnkiConnect** add-on running.

## Step 1 — Set up (once)

Run the one-time setup. It builds your consolidated dictionary database from your Yomitan `.zip`
dictionaries and registers their titles in your config, so later runs need only a video path.

```bash
saitenka setup
```

!!! tip
    Run `saitenka doctor` any time to see what's imported and whether mpv, ffmpeg, and AnkiConnect
    are reachable.

## Step 2 — Launch an episode

Point Saitenka at any Japanese video file. It opens **mpv** with the study overlay: your subtitles,
FSRS-aware coloring, hover tooltips, and one-key mining — all on mpv's single surface.

```bash
saitenka run episode.mkv
```

mpv opens and starts playing. When a Japanese subtitle line appears, the overlay takes over.

## Step 3 — Read the colors

The subtitle line is tokenized word by word and each word is **colored by your FSRS knowledge**:
words you already know look different from words that are new or due. At a glance you can see which
parts of the line are worth mining.

## Step 4 — Hover for the dictionary

Move your mouse over a colored word. A **tooltip** pops up above it with definitions from your
dictionaries — meaning, reading, and pitch — merged across every dictionary you imported.

!!! tip
    Readings and pitch always come straight from your dictionaries, never guessed.

## Step 5 — Mine your first card

With Anki open (and AnkiConnect running), press ++ctrl+m++ to mine the **current subtitle line**.
Saitenka builds a note — the sentence, the target word with its readings and glosses, and the audio
snippet — and sends it to Anki, showing a quick preview.

That's it: switch to Anki and you'll find a brand-new card waiting in your target deck. **You just
mined your first card from real immersion.**

!!! tip
    ++ctrl+m++ mines one line; there's a bulk-mining key too — see the shortcuts page below.

## Play your own episodes

Most anime rips carry **embedded Japanese subtitles** — just pass the file and Saitenka uses them:

```bash
saitenka run /path/to/anime.mkv
```

Got subtitles as a separate file? Point at them with `--sub-file`:

```bash
saitenka run video.mkv --sub-file jp.srt
```

No Japanese subs on the file at all? Fetch them from **jimaku.cc** with `--jimaku` (supply your key
via `--jimaku-key` or the `$JIMAKU_API_KEY` environment variable):

```bash
saitenka run show.mkv --jimaku --jimaku-key YOUR_KEY
```

## Next

- **[Keyboard shortcuts](../usage/shortcuts.md)** — every key in the overlay, including bulk mining.
- **[Features](../usage/features.md)** — coloring, tooltips, translation, and mining in depth.
