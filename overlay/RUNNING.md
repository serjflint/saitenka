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

## 1a. Import your dictionaries (do this once)

Dictionaries are built **once** into a consolidated database
(`~/.local/share/saitenka/dictionaries.sqlite`) — the Yomitan model. Point `import` at the folder(s)
holding your Yomitan `.zip` dictionaries; it classifies each (definition / frequency / pitch), imports
it, and fills `dicts`/`freq`/`pitch` in your config with their **titles**:

```bash
saitenka import ~/yomitan-dicts        # build the DB + register the titles in the config
```

The source zips are read **in place** — no copy is kept, so you can delete or move them afterwards.
(Have a Yomitan settings export instead? `import-settings <export.json> --scan-dir ~/yomitan-dicts`.
Only a multi-GB dexie DB backup? `import-dictionaries <export.json>`.) Your known-decks and mine target
live alongside the titles in **`~/.config/saitenka/overlay.toml`** (see `overlay.example.toml`).

With that in place the full run is just the video path — every `--dict/--freq/--pitch/--anki-decks`
default comes from the config (an explicit CLI flag still overrides it). Nothing is rebuilt at play
time; `run`/`attach` only open the DB. `saitenka doctor` lists what's imported.

### Migrating from an older (zip-path) config

Earlier versions listed dictionary **zip paths** in `dicts`/`freq`/`pitch` and rebuilt a per-zip cache
on every launch (the `copy-dicts` era). To move to the consolidated DB, just run `import` once against
the folder those zips already live in:

```bash
saitenka import ~/yomitan-dicts    # builds the DB and rewrites the config to titles
```

That's the whole migration — `import` overwrites the old path entries with the imported **titles**, so
you don't edit the config by hand. Afterwards `doctor` flags the now-unused pre-consolidation files
(the old per-zip cache and any copied zips) as safe to delete; nothing is removed automatically, and
your original `~/yomitan-dicts` zips are never touched. The `copy-dicts` command is gone — runtime no
longer reads the zips, so there's nothing to relocate out of a protected folder.

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
- If Japanese is unavailable, English is shown immediately without Japanese tokenization or mining.
  Enabled providers run in the background (Jimaku, then TsukiHime) and announce an added track
  without selecting it.
- Dictionaries are already imported into the DB (§1a), so `run`/`attach` open it instantly — no build
  at play time. (Importing a brand-new dict is the only slow step, and it happens under `import`.)
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
| **Alt+t** | switch the primary subtitle between Japanese-only and English-only |
| **Ctrl+Shift+T** | retry enabled Japanese subtitle providers for the current media |
| **Alt+p** | toggle whether opening a tooltip automatically pauses mpv |
| **Alt+b** | save/archive the active cue for later review without pausing or seeking |
| **\\** | toggle the whole-track subtitle and deferred-capture sidebar |
| **backquote** | toggle whole-track Japanese subtitle analysis and difficulty statistics |
| **Alt+a** | toggle full and hover-only learning annotations |
| **F1** | toggle the compact in-player shortcut reference; `Esc` closes it |
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
# (--dict takes an imported dictionary TITLE; run `saitenka import <dir>` first)
uv run python examples/mpv_reader.py /path/to/anime.mkv --color --mine \
  --dict "Your Dictionary Title"

# external subs
uv run python examples/mpv_reader.py video.mkv --sub-file jp.srt --color --mine

# no JP subs on the file → fetch from jimaku.cc (key via --jimaku-key or $JIMAKU_API_KEY)
uv run python examples/mpv_reader.py show.mkv --jimaku --jimaku-key YOUR_KEY --color --mine
```

Useful flags: `--fullscreen`, `--no-audio-play` (don't auto-play the mined clip),
`--known "私,本,経"` (manual known set instead of Anki), `--mine-deck` / `--mine-model`,
`--use-config` (load your real mpv config instead of the isolated `--no-config` default),
`--mpv-arg` (repeatable — pass a raw extra mpv flag through, e.g. `--mpv-arg --volume=80`; wins over
our own overridable defaults like `--slang`/`--osd-level`, but never over the IPC socket/log-file/
anti-duplicate script-opts flags we always set last).

## 7. Cleanup (mining writes **real** cards)

Mined cards go to **`Saitenka::Mining`** (Lapis) tagged **`saitenka`** — they're real, kept by
default. To review or remove test cards: Anki → Browse → search `tag:saitenka`. To wipe them
from a terminal:

```bash
curl -s 127.0.0.1:8765 -d '{"action":"guiBrowse","version":6,"params":{"query":"tag:saitenka"}}'
# …then delete in the Browser, or scripted:
IDS=$(curl -s 127.0.0.1:8765 -d '{"action":"findNotes","version":6,"params":{"query":"tag:saitenka"}}' | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin)['result']))")
curl -s 127.0.0.1:8765 -d "{\"action\":\"deleteNotes\",\"version\":6,\"params\":{\"notes\":$IDS}}"
```

## 7a. mpv coexistence — attach & plugin modes

The overlay does not need to own mpv. It can **join** an already-running mpv, sharing the IPC socket
with mpv_websocket / animecards (mpv accepts many concurrent IPC clients — we join, we don't take
over, which sidesteps the SubMiner-vs-animecards socket fight).

```bash
# attach to a running mpv (its mpv.conf has input-ipc-server=/tmp/mpv-socket, or pass the path):
uv run saitenka attach /tmp/mpv-socket

# plugin mode: install a one-file user-script so ANY mpv launch spawns the overlay automatically
uv run saitenka install-plugin     # writes ~/.config/mpv/scripts/saitenka.lua (backs up first)
uv run saitenka uninstall-plugin   # removes it (backs up first)
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

## 9. Developer workflow (local task runner, no CI)

Everything runs locally via [poethepoet](https://poethepoet.natn.io/); `uv run poe all` is the
pre-push gate. The full task-by-task breakdown, how to read a failure, the advisory `poe hygiene`
tier, and the free-threaded / 3.13-pinned-env traps live in the `dev-gate` skill
(`.agents/skills/dev-gate/`) — consult it rather than this table, which drifts.

Logs: the overlay writes a rotating **JSON-lines** debug log to `~/.cache/saitenka/overlay.log`
(DEBUG in the file, human-readable WARNING+ to stderr) — silent failures land there, not in a black
hole. Each line is a redacted JSON object (`{"event": "...", "level": "...", "timestamp": "..."}`),
so it's `jq`-able; `doctor`'s "recent errors" section and `report`'s bundle both read it as-is.

### Telemetry (optional, off by default)

Runtime tracing/metrics via OpenTelemetry — **not installed or enabled unless you opt in**. Two
independent switches: the `telemetry` extra (the OTel SDK) and the `[telemetry] enabled` config flag.

Install the extra, then flip the flag with the CLI (no hand-editing needed):

```
uv tool install --reinstall 'saitenka[telemetry]'   # or add to an existing [full] install
saitenka telemetry enable      # sets [telemetry] enabled = true (backs up your config)
saitenka telemetry status      # both switches + where the trace lands + last trace
saitenka telemetry disable     # flips it back off
```

`enable` warns you (with the exact install command) if the extra is missing, since the flag alone
won't record without it. Equivalent manual edit in `overlay.toml` if you prefer:

```toml
[telemetry]
enabled = true
# export_dir = "~/custom/telemetry"  # default: ~/.cache/saitenka/telemetry
```

Each session with telemetry enabled writes its own Chrome Trace Format file to
`~/.cache/saitenka/telemetry/trace-<UTC>.json` (the newest 10 are kept) — open it directly in
`chrome://tracing` or [Perfetto](https://ui.perfetto.dev/). Every span carries a `cpu_ms` attribute:
`wall ≫ cpu_ms` means the thread was stalled (GIL/lock/IO), not working. Metrics
(render/upload/hit-test/dict-SQL/IPC/sub-seek histograms, cache hit-miss counters, `gil_enabled`) are
pull-based and process-local — they don't persist to disk on their own; `doctor` and `telemetry
status` report the newest trace's presence/size, and `report` bundles it (redacted) if one exists. `$OTEL_SDK_DISABLED=true` force-disables telemetry even if the config
says `enabled = true` (the standard OTel kill switch). See the "Telemetry" section of
[ROADMAP.md](../ROADMAP.md) for the full design — non-goal: standing up a backend, the default path
is local-file-only, no gRPC/OTLP.

> **Linguistic-data pin:** the golden images encode **unidic-lite's tokenization** (word
> boundaries, readings) and the bundled fonts' rasterization. Bumping `unidic-lite` (or Pillow /
> the fonts) can legitimately move goldens — inspect the diff and re-bless deliberately
> (`SAITENKA_UPDATE_GOLDEN=1`), never "fix" goldens blindly to make a bump pass.
