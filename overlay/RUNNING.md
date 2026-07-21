# Running the overlay MVP — manual test guide

The in-mpv reader: Japanese subs with FSRS-aware coloring, hover → multi-dictionary tooltip, one-key
(and bulk) mining to Anki with a post-mine preview, and an on-demand English translation — all inside
mpv's single surface.

## 0. Prerequisites

- **mpv** and **ffmpeg** on `PATH` (`mpv --version`, `ffmpeg -version`).
- **uv** (`uv --version`).
- **Anki open** with the **AnkiConnect** add-on (only needed for coloring-from-Anki and mining).
  Check: `curl -s 127.0.0.1:8765 -d '{"action":"version","version":6}'` → `{"result":6,...}`.

## 1. Clone + install

```bash
git clone https://github.com/serjflint/saitenka.git
cd saitenka/overlay
uv sync            # installs pillow, fugashi+unidic-lite, jamdict, numpy…
uv run pytest -q   # sanity: should print "X passed"
```

## 1a. Settings (persist your dictionaries — do this once)

Your dictionary list, frequency/pitch dicts, known-decks, and mine target live in
**`~/.config/saitenka/overlay.toml`** (its own dir — not mixed into mpv.conf). Copy the template once:

```bash
mkdir -p ~/.config/saitenka
cp overlay.example.toml ~/.config/saitenka/overlay.toml   # then edit it to point at your Yomitan dictionaries
```

With that in place the full run is just the video path — every `--dict/--freq/--pitch/--anki-decks`
default comes from the file (an explicit CLI flag still overrides it). Built dict indexes cache at
`~/.cache/saitenka-overlay/dicts/` and persist between runs (rebuilt only when a zip changes).

## 2. Quick smoke run (no Anki, generated clip)

```bash
uv run python examples/mpv_reader.py
```

A 1080p test clip opens with a Japanese line. **Move the mouse over a word** → a JMdict tooltip appears
above it. Press **`q`** to quit. (This uses no Anki and no external dicts — just proves subs + hover.)

## 3. Full run on the test episode

With §1a done, the video path is the only required argument (dicts/freq/pitch/known come from the config):

```bash
cd saitenka/overlay
uv run python examples/mpv_reader.py \
  "/path/to/anime.mkv" \
  --color --mine --start 600
```

Or spell everything out on the CLI (overrides the config), e.g. `--dict … --freq … --pitch … --anki-decks '{"Saitenka::Known":["Expression"]}'`.

- The embedded **Japanese** track is auto-selected and re-drawn by the overlay (mpv's own subs hidden);
  the embedded **English** track is loaded as the hidden secondary (for the `t` reveal).
- Dictionaries are cached (`~/.cache/saitenka-overlay/dicts/`); already built here, so load is instant.
  (A brand-new dict zip would take ~30–60 s to index on first use.)
- `--start 600` jumps ~10 min in (past the OP, into dialogue). Press **space** to pause on a line with
  words to scan.

## 4. Keys (press inside the mpv window)

| Key | Action |
|---|---|
| move mouse over a word | show the multi-dictionary tooltip |
| **mouse wheel** (over the tooltip) | **scroll** — reach the lower dictionary sections |
| **Alt+←** | jump to the **previous** subtitle line (sub-seek -1) |
| **Alt+→** | jump to the **next** subtitle line (sub-seek +1) |
| **Alt+↓** | **replay** the current subtitle line from its start (sub-seek 0) |
| **z** / **Z** | sub-delay −0.1 s / +0.1 s (nudge timing to fix out-of-sync subs) |
| **x** | reset sub-delay to 0 |
| **a** | speak the hovered word (Japanese TTS) |
| **left-click** the tooltip | also speaks the word |
| **c** | copy the hovered word + reading to the clipboard |
| **Ctrl+m** | mine the **hovered** word → Anki card + **preview** (auto-plays the clip) |
| **Shift+m** | **bulk-mine** every unknown word in the current line |
| **t** | toggle the **English** translation of the current line |
| **p** | replay the last card preview + its audio |
| space / f / ← → / q | mpv: pause / fullscreen / seek / quit |

> The tooltip stacks all dictionaries in `--dict` order; a bilingual dict's entries can be long, so
> **scroll** to reach the monolingual sections. Text isn't selectable (the tooltip is drawn, not a
> text widget) — use
> **c** to copy the word, or **a** / click to hear it.

## 5. What to verify (checklist)

- [ ] **Subtitles**: the JP line is drawn by the overlay (SubMiner-style box), multi-line wraps, and
      names with baked-in furigana (e.g. `龍門光英…`) are clean (reading stripped).
- [ ] **Coloring**: known words (from `Saitenka::Known`) are **green**; the single unknown word in a
      sentence is **mauve** (N+1); others take a frequency-band color; JLPT words get an underline;
      particles stay plain.
- [ ] **Tooltip**: hovering a word shows the dictionaries stacked in config order, with ruby examples.
      The tooltip anchors **above the hovered word's line**, and re-hovering a word is instant (cached).
- [ ] **Frequency pills**: under the headword, a green row (one pill per freq dict, in config order)
      and a purple pitch pill (`ほんめい [0]`).
- [ ] **Grammar tags**: `noun` / `no-adj` / `suru` render as filled gray pills (not empty boxes).
- [ ] **Mine** (Ctrl+m): a green `✚ mined …` preview appears top-left with the word, reading, sentence
      (mined word bolded), meaning, the **actual frame**, and `▶ Ns` — and **you hear the clip**.
- [ ] **Dedup**: mine the same word again → the preview shows the **existing** card (`✓ in deck`) with
      its image + audio, and no duplicate is created.
- [ ] **Bulk** (Shift+m): toast reads `mined N · M dup`; check `Saitenka::Mining` gets N new cards.
- [ ] **Translation** (t): the English line appears above the JP subtitle; press `t` again to hide.
- [ ] **Fullscreen** (f): the subtitle, tooltip, preview all stay correctly placed (airspace test).

## 6. Playing your own episodes

```bash
# embedded JP subs (most anime rips): the file path is the positional arg
uv run python examples/mpv_reader.py /path/to/anime.mkv --color --mine \
  --dict "/path/to/jitendex-yomitan.zip"

# external subs
uv run python examples/mpv_reader.py video.mkv --sub-file jp.srt --color --mine

# no JP subs on the file → fetch from jimaku.cc (key via --jimaku-key or $JIMAKU_API_KEY)
uv run python examples/mpv_reader.py show.mkv --jimaku --jimaku-key YOUR_KEY --color --mine
```

Useful flags: `--fullscreen`, `--no-audio-play` (don't auto-play the mined clip),
`--known "私,本,経"` (manual known set instead of Anki), `--mine-deck` / `--mine-model`,
`--use-config` (load your real mpv config instead of the isolated `--no-config` default).

## 7. Cleanup (mining writes **real** cards)

Mined cards go to **`Saitenka::Mining`** (Lapis) tagged **`saitenka-overlay`** — they're real, kept by
default. To review or remove test cards: Anki → Browse → search `tag:saitenka-overlay`. To wipe them
from a terminal:

```bash
curl -s 127.0.0.1:8765 -d '{"action":"guiBrowse","version":6,"params":{"query":"tag:saitenka-overlay"}}'
# …then delete in the Browser, or scripted:
IDS=$(curl -s 127.0.0.1:8765 -d '{"action":"findNotes","version":6,"params":{"query":"tag:saitenka-overlay"}}' | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin)['result']))")
curl -s 127.0.0.1:8765 -d "{\"action\":\"deleteNotes\",\"version\":6,\"params\":{\"notes\":$IDS}}"
```

## 7a. mpv coexistence — attach & plugin modes (Stage 16)

The overlay does not need to own mpv. It can **join** an already-running mpv, sharing the IPC socket
with mpv_websocket / animecards (mpv accepts many concurrent IPC clients — we join, we don't take
over, which sidesteps the SubMiner-vs-animecards socket fight).

```bash
# attach to a running mpv (its mpv.conf has input-ipc-server=/tmp/mpv-socket, or pass the path):
uv run saitenka-overlay attach /tmp/mpv-socket

# plugin mode: install a one-file user-script so ANY mpv launch spawns the overlay automatically
uv run saitenka-overlay install-plugin     # writes ~/.config/mpv/scripts/saitenka.lua (backs up first)
uv run saitenka-overlay uninstall-plugin   # removes it (backs up first)
```

`doctor` reports whether `mpv.conf` sets `input-ipc-server` and which known tool uses that socket.
If another mpv script already owns OSD overlay ids 1–6, set `overlay_id_base` in the config to shift
ours. mpv discovery order: `mpv_path` config → `PATH` → `/Applications/mpv.app` / Homebrew / scoop /
choco / winget shims.

## 8. Troubleshooting

- **Tooltip appears on the wrong word / offset** — hover hit-testing maps `mouse-pos` to the overlay in
  OSD-pixel space. On a HiDPI display the mapping may need a scale factor; note it and we'll calibrate.
  (The forced-hover demo path is exact: add `--demo-word 明日 --screenshot /tmp/x.png` to bypass the mouse.)
- **`AnkiConnect unreachable`** — open Anki; ensure the AnkiConnect add-on is installed and Anki is the
  foreground app at least once. Or drop `--mine`/`--anki-decks` and use `--color --known "…"`.
- **No dictionary in the tooltip** — check the `--dict` paths (the monolingual zips have spaces/brackets,
  so keep the quotes). First use of a new zip indexes for ~30–60 s.
- **No sound on mine** — the clip plays via `afplay` (macOS); confirm the mkv's audio track exists. The
  card's `SentenceAudio` should read `[sound:saitenka_….mp3]`.
- **Keys do nothing** — the mpv window must have focus; the flags print the active keys at launch.

## 9. Developer workflow (Stage 8b — local task runner, no CI)

Everything runs locally via [poethepoet](https://poethepoet.natn.io/):

| Task | What it runs |
|---|---|
| `uv run poe lint` | `ruff check --fix` + `ruff format` (B/SIM/TRY/PERF/RUF/UP/C4 hardened) |
| `uv run poe types` | mypy + pyright (blocking) · pyrefly + ty (advisory, pre-1.0) |
| `uv run poe test` | pytest, parallel (`-n auto`, pytest-randomly seeds each run) |
| `uv run poe test-ft` | the suite under `PYTHON_GIL=0` + a GIL-stays-off assertion |
| `uv run poe cov` | coverage with an **85% floor** (`--cov-fail-under=85`) |
| `uv run poe bench` | the pathological cold-first-paint benchmark |
| `uv run poe all` | the pre-push gate: lint → types → test → test-ft → cov |

Logs: the overlay writes a rotating debug log to `~/.cache/saitenka-overlay/overlay.log`
(DEBUG in the file, WARNING+ to stderr) — silent failures land there, not in a black hole.

> **Linguistic-data pin:** the golden images encode **unidic-lite's tokenization** (word
> boundaries, readings) and the bundled fonts' rasterization. Bumping `unidic-lite` (or Pillow /
> the fonts) can legitimately move goldens — inspect the diff and re-bless deliberately
> (`SAITENKA_UPDATE_GOLDEN=1`), never "fix" goldens blindly to make a bump pass.
