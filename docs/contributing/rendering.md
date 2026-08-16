# Rendering the in-mpv dictionary panel

> This is the renderer's design-rationale readme. **Using Saitenka** (install, features, shortcuts) →
> the docs site: [saitenka.readthedocs.io](https://saitenka.readthedocs.io).

Renders Yomitan `structured-content` (styled, wrapping CJK text with **ruby/furigana**) into a
panel **image**, so it can be composited over mpv video in a **single surface** (no second
top-level window → no Windows airspace/MPO/fullscreen bugs).

## Why Python + Pillow first

Per the "simplest tool first, escalate on limits" rule: Pillow is the simplest thing that can
rasterize styled CJK text + custom ruby positioning, and mpv's `overlay-add` IPC command can push
a rendered RGBA image straight into mpv's own OSD surface — airspace-safe, no GL/FFI. We nail the
**visual design** here (matching the real 読む popup) before escalating to Rust + cosmic-text +
the libmpv render API if/when Pillow hits a wall. The optional PyO3/libass subtitle-geometry path is
separate: it recovers hit boxes for mpv-rendered ASS and does not rasterize dictionary panels.

## Layout

- `src/saitenka/` — the library (`fonts`, `render/`, `model`, `sc/`, `panel`, `draw/`).
- `src/saitenka/assets/fonts/` — **vendored** Noto Sans JP (variable) + Noto Sans, so golden images reproduce
  across machines (macOS / Windows / Linux).
- `tests/golden/` — golden PNGs; `tests/fixtures/` — structured-content JSON.
- `examples/render_png.py` — CLI: render a string or a fixture JSON to a PNG.

Module-by-module map and the hover→lookup→render→mine data flow:
[ARCHITECTURE.md](https://github.com/serjflint/saitenka/blob/main/ARCHITECTURE.md).

For structured-content interpretation bugs, use the opt-in `dictionary-structure-oracle` documented in
[`saitenka-dict/ORACLE.md`](https://github.com/serjflint/saitenka/blob/main/saitenka-dict/ORACLE.md). It compares Yomitan's generated DOM with
Saitenka's blocks for the same selected glossary immediately before layout, so glued lines, lost
markers, and wrong nesting can be diagnosed independently of font metrics and rasterization. It does
not independently validate dictionary import or glossary selection.

## Usage

```bash
uv sync
uv run python examples/render_png.py --text "Saitenka" -o out.png
uv run python examples/render_png.py --entry tests/fixtures/yomu.json --bg white -o panel.png
uv run pytest            # golden tests
SAITENKA_UPDATE_GOLDEN=1 uv run pytest   # regenerate goldens (inspect the diff first!)
uv run python examples/bench_responsiveness.py   # UI latency vs the saved baseline (BENCHMARKS.md)
```

Responsiveness KPIs, targets, and the saved baseline live in
[`BENCHMARKS.md`](https://github.com/serjflint/saitenka/blob/main/BENCHMARKS.md).

## Bolt to mpv (the airspace-safe cure)

The panel is pushed into mpv's **own OSD surface** via the `overlay-add` JSON-IPC command — one
surface, no second window, so it survives fullscreen (the Electron overlay bug can't recur). The
panel path needs no GL or FFI; the optional native-visible subtitle mode uses FFI only for offscreen
geometry.

```bash
# play a file and show the 読む panel top-left; press 'f' in mpv to test fullscreen
uv run python examples/mpv_overlay.py /path/to/video.mkv

# no file: generate a test clip, screenshot the composited mpv window, quit
uv run python examples/mpv_overlay.py --screenshot /tmp/shot.png --seconds 2
```

A real mpv-window screenshot proving the composite is at `tests/artifacts/mpv_live_screenshot.png`.

## MVP reader — subtitles + hover tooltip

The default reader hides native subs and draws a SubMiner-style subtitle with per-word hitboxes. The
opt-in native-visible experiment instead leaves authored external ASS rendering with mpv/libass and
derives hit boxes on a bounded background worker; Pillow remains authoritative by default. On hover,
the reader looks the word up and draws its tooltip in mpv's OSD surface.

The visible-vs-shadow ownership, stale-result guards, lookahead caches, and optional package boundary
are diagrammed in [Native-visible subtitle architecture](architecture.md#native-visible-subtitle-architecture).

```bash
uv run python examples/mpv_reader.py video.mkv --sub-file jp.srt   # hover words with the mouse
uv run python examples/mpv_reader.py                                # test clip + generated JP line
uv run python examples/mpv_reader.py --demo-word 読む --screenshot /tmp/reader.png  # screenshot demo
```

Result (real mpv window): `tests/artifacts/mvp_reader_hover.png`. The dictionary adapter
(`app/lookup.py`) emits the same `Entry` the renderer already draws, so a monolingual / structured-
content dictionary can be swapped in behind it later without touching the renderer.

### Word coloring (SubMiner-parity, FSRS-aware)

`app/scoring.py` colors each subtitle word: **N+1 > forgotten > mature-known > learning > young >
frequency-band > base** text color, plus a JLPT-level **underline**. Known words come from Anki
(`app/wordlists.py::KnownWords.from_ankiconnect`, decks→fields like SubMiner) or a static set. An
optional `[fsrs]` path to a copied `collection.anki2` refines that binary set; Saitenka never opens the
live Anki database. Learning and young colors are configurable under `[palette]`;
`overlay.example.toml` owns the settings and defaults. Frequency comes from a user-supplied Yomitan
freq dictionary; JLPT comes from the vendored `assets/wordlists/jlpt.zip`.

Backquote opens a playback-neutral whole-track analysis with sentence/content-token totals, unique
lemmas and kanji, unknown vocabulary, known coverage, N+1/N+2 counts, and optional JLPT/frequency
distributions. Analysis runs in the background and is cached for the subtitle and vocabulary snapshot.

Optional local session history records watch time, cues, lookups, deferred captures, cards mined,
and subtitle-language usage without telemetry. Enable `[stats]` in `overlay.toml`; `saitenka stats`
shows recent complete and interrupted sessions. Stored difficulty snapshots reuse the analysis above.

```bash
uv run python examples/mpv_reader.py --sub-file jp.srt --color \
  --known "私,本,経" --freq "Frequency General"
# or pull the known-set from Anki:
uv run python examples/mpv_reader.py --sub-file jp.srt --color \
  --anki-decks '{"Kaishi 1.5k":["Word"]}'
```

### Subtitle source: embedded / providers / file

An explicit `--sub-file` or `--jimaku` can override startup. Otherwise the reader selects embedded
Japanese, immediately falls back to English when needed, then tries configured providers in the
background: Jimaku first, followed by opt-in TsukiHime. Fetched Japanese is added without switching
tracks; configurable `Ctrl+Shift+T` retries that chain for the current media. Amazon-style **inline
furigana** baked into ASS (`龍門光英りゅうもんみつひで`) is stripped
before tokenizing (`app/tokenize.py::strip_inline_furigana`).

```bash
# real anime with an embedded JP track + your real Anki known-set (FSRS deck):
uv run python examples/mpv_reader.py "Nippon Sangoku - 10 [...MultiSub...].mkv" \
  --color --anki-decks '{"Saitenka::Known":["Entry","Expression","Word"]}' \
  --freq "Frequency General"

# a file with no JP subs → fetch from jimaku (title/episode parsed from the filename):
uv run python examples/mpv_reader.py show.mkv --jimaku --color
```

### One-key mining (on one surface)

`--mine` enables mining: hover a word, press the mine key (default `Ctrl+m`) → a **Lapis** card is
created via AnkiConnect with Expression / reading / **Sentence** (mined word bolded) / **Glossary** /
**Picture** (clean frame) / **SentenceAudio** (the subtitle's audio span, ffmpeg mp3) / provenance /
JMdict ID. Dedup checks the deck first (no silent duplicates); a toast confirms (`✚ mined …` /
`● already have …` / `× …`). All inside mpv's surface — no texthooker, no second window.

```bash
uv run python examples/mpv_reader.py episode.mkv --color \
  --anki-decks '{"Saitenka::Known":["Entry","Expression","Word"]}' \
  --freq "Frequency General" \
  --mine --mine-deck "Saitenka::Mining" --mine-model Lapis
# then hover a word in mpv and press Ctrl+m
```

Verified end-to-end on a real episode: the card is built with a real frame jpg + subtitle mp3,
deduped, then cleaned up.

- **Bulk mining** — `Shift+m` mines every unknown content word in the current cue, all sharing one
  screenshot + audio clip; toast reports `mined N · M dup`.
- **Card preview (verify without alt-tab)** — after mining, a **fixed-layout** panel shows the card so
  you can check it's right: status, headword + reading, the sentence (mined word bolded), the meaning,
  the **actual captured frame**, and the audio (`▶ Ns`, which **auto-plays** so you hear the clip). Mining
  an already-present word previews the **existing** card instead (image + audio pulled from Anki). `p`
  replays the last preview + audio. It's composed from our own primitives — no card CSS — purely to
  verify correctness / image / sound.

### Multi-dictionary tooltip (Yomitan-style, ordered)

Import any **Yomitan term-bank** dictionaries (bilingual and/or monolingual — whichever you have) once
with `saitenka import <dir>`; they build into a single **consolidated database**
(`~/.local/share/saitenka/dictionaries.sqlite`, the Yomitan model). Then `--dict "Title A" --dict
"Title B" …` (or the config lists) shows the word across all of them, **in order**, each as its own
section with the dict-name pill and rich structured content (ruby examples, notes, cross-refs). Runtime
only opens the DB — nothing is rebuilt at play time, and RAM stays low. Falls back to JMdict/jamdict when
no dictionary is configured.

### Official-translation reveal (anti-crutch)

Default is **JP primary (mining) + EN secondary**: the reader auto-selects the JP sub track (`--slang
ja,jpn,jp`) and the embedded EN track as mpv's *secondary* sub (hidden). Press `t` to toggle the EN
line for the current cue above the JP subtitle — the professional translation on demand, not by default.

```bash
saitenka import ~/yomitan-dicts   # once: build the DB, register the titles in the config
uv run python examples/mpv_reader.py episode.mkv --color \
  --anki-decks '{"Saitenka::Known":["Entry"]}' --mine \
  --dict "Bilingual Dict" \
  --dict "Monolingual Dict A" \
  --dict "Monolingual Dict B"
# hover a word; Ctrl+m mine · Shift+m mine-all · t translation · Alt+p hover auto-pause
```

## Escalation ladder (simplest tool first)

Pillow + `overlay-add` is the simplest thing that renders this and gets it onto mpv. If it hits a
wall — per-frame animation, huge panels, GPU scaling, live interactivity — escalate to Rust +
cosmic-text + the libmpv render API (see [Future cosmic-text raster backend](native-renderer.md)). The
renderer here (walker, ruby, chrome, goldens) is the spec that escalation must match.

## Installing

Published on PyPI — install without cloning, via the one-line script or `uv`/`pipx` directly:

```sh
curl --proto '=https' --tlsv1.2 -LsSf https://serjflint.github.io/saitenka/install.sh | sh
# …or, if you already have uv:
uv tool install "saitenka[full]" && saitenka setup
```

`saitenka setup` inventories the box, installs mpv + ffmpeg (macOS `brew`; Windows
winget→choco→scoop; Linux prints copy-paste hints), runs `doctor`, writes the config (`init`), and
offers `import-settings` + `install-plugin`. Confirm-first, `--yes`/`--dry-run` honoured, resumable.
Upgrade = `saitenka update` (keeps your extras). The installer scripts live in
[`install/`](https://github.com/serjflint/saitenka/tree/main/install) and are served from GitHub Pages
via `poe pages`.

## Development

`uv run poe all` is the local pre-push gate and CI mirrors it. Full task-by-task breakdown and traps:
the `dev-gate` skill (`.agents/skills/dev-gate/`) / the
[Development](https://saitenka.readthedocs.io/en/latest/contributing/development/) docs.
