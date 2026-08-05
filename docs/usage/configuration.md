# Configuration

Saitenka reads its settings from a single TOML file, `overlay.toml`. The shipped
[`overlay.example.toml`](https://github.com/serjflint/saitenka/blob/main/overlay/overlay.example.toml)
is the exhaustive, self-documenting reference: **every** recognized key appears there at its built-in
default, commented, sshd_config style. This page is the curated tour of the handful of settings most
people actually touch — what each does and *why* you'd change it. For the full list, read the example
file.

## Where the config lives

`overlay.toml` sits in your platform config directory (`~/.config/saitenka/` on Linux,
`~/Library/Application Support/saitenka/` on macOS, `%APPDATA%\saitenka\` on Windows). You never have
to guess the exact path:

```console
$ saitenka doctor
```

prints it, along with mpv/ffmpeg, the dictionary cache, fonts, and AnkiConnect status. Point elsewhere
with `--config <path>` or the `$SAITENKA_CONFIG` environment variable.

!!! note "Precedence"
    Values resolve **built-in defaults → config file → explicit CLI flag** (Saitenka loads the TOML via
    cyclopts' `Toml` config). A flag like `--no-mine` always wins over the file for that one run; the
    file always wins over the shipped defaults. Anything you don't set stays at its default — you only
    write the keys you want to change.

## Coloring known vs. new words

Saitenka tints each word by how well you know it, using FSRS maturity state pulled from Anki. Two
tables shape that.

`[palette]` sets the band colors. Change them to match your theme or to widen the visual gap between
maturity levels:

```toml
[palette]
learning = "#eed49f"   # words still in learning
young    = "#8bd5ca"   # young (not yet mature) cards
```

`[fsrs]` optionally reads maturity from a **copy** of your Anki collection so coloring reflects real
review state. Set this when you want per-card maturity rather than a plain known/unknown split — and
point it at a *copy*, never the live `collection.anki2`:

```toml
[fsrs]
collection = "~/anki-copies/collection-copy.anki2"
```

!!! tip
    The known-word source itself lives in `[known]` — a map of Anki decks to the fields that hold the
    expression. The default is `"Saitenka::Known" = ["Expression"]`; change it to match the deck and
    field where your known vocabulary already lives.

## Mining to Anki

`[mine]` is the one-key card-maker. Mining is on whenever this table is present; ++ctrl+m++ mines the
hovered word into the configured deck. Edit it to target your own deck and note type, and to map onto
your note's fields:

```toml
[mine]
enabled = true
deck  = "Saitenka::Mining"   # destination deck
model = "Lapis"              # Anki note type / model
key       = "Ctrl+m"         # mine the hovered word
video_key = "Ctrl+Shift+m"   # mine the hovered word with an animated (motion) clip
all_key   = "Shift+m"        # bulk-mine the current line
preview_key = "p"            # replay the last card preview
preview = true               # auto-pop the card-preview panel after a mine (--no-mine-preview off)
normalize_audio = false      # −23 LUFS loudnorm on the mined clip (off by default)
animated_screenshot = false  # capture a motion clip as the card image instead of a still (off by default)
animated_format = "webp"     # "webp" (prefer WebP → GIF fallback) or "gif" (force universal GIF)
animated_height = 480        # clip height in px — the main quality↔storage lever
animated_fps = 12            # clip frame rate
animated_quality = 75        # WebP quality, 0–100 (ignored for GIF)
animated_max_secs = 4.0      # cap clip length so a long cue can't produce a huge file
```

Set `enabled = false` to keep the config but turn mining off (the `--no-mine` flag does the same for a
single run). `preview = false` (or `--no-mine-preview`) mines silently — a toast confirms instead of the
verify panel popping up; mining and the ⊕→✓ flip are unaffected. `normalize_audio` runs an EBU R128
`loudnorm` pass (−23 LUFS) so cards mined from quiet
and loud lines play back at an even volume; it adds one ffmpeg pass per mine, so it's off by default.

**Animated (motion) screenshots.** `animated_screenshot = true` captures a short animated clip of the
scene as the card image instead of a still frame — the scene reads better in motion and pairs with the cue
audio you already clip. It's opt-in (larger media + an extra ffmpeg pass). It prefers WebP (small and
sharp) and automatically falls back to an animated **GIF** where your ffmpeg lacks `libwebp` (Homebrew's
ffmpeg and the Windows "essentials" build often do) — GIF's encoder is native to every ffmpeg, so a clip is
produced out of the box on macOS, Linux, and Windows; `saitenka doctor` reports which. Force GIF with
`animated_format = "gif"`. `animated_height` (plus `animated_fps`/`animated_quality`/`animated_max_secs`)
trade clip quality against file size. You don't have to turn it on globally: ++ctrl+shift+m++ (`video_key`)
mines the hovered word with a clip for that one card. If your AnkiConnect endpoint isn't the stock
`127.0.0.1:8765`, override it in the separate `[anki]` table.

!!! note "Anki starts itself"
    When mining (or Anki-backed coloring) is on, Saitenka launches Anki for you if it's closed.

## Tooltip

`[tooltip]` geometry and feel actually live as **top-level** keys (above the first `[table]` header).
The ones worth touching:

- `dicts` / `freq` / `pitch` — the dictionaries shown, by **title** (not path), in tooltip order.
  These are the keys you edit most: list the definition, frequency, and pitch-accent dictionaries you
  imported. `saitenka import` fills them in; `saitenka doctor` shows what's available.
- `ui_scale` (default `1.0`) — overall size of help, sidebar, and analysis; raise it on a high-DPI
  display.
- `tip_height` (default `0.4`) — max tooltip height as a fraction of the video, if the default crops
  long entries.
- `pause_on_tooltip` (default `true`) — freeze the frame while a tooltip is open (the mining default);
  set `false` if you'd rather keep playing.

```toml
dicts = ["Bilingual Dict", "Monolingual Dict A"]
freq  = ["Frequency General"]
pitch = ["Pitch Accent"]
ui_scale = 1.0
```

## jimaku

`[jimaku]` fetches Japanese subtitles from jimaku.cc when a file has none embedded. It's safe to leave
enabled — `attach` prefers an embedded JP track and only falls back to jimaku on a miss:

```toml
[jimaku]
enabled = true
resync  = true   # auto-align fetched subs to the video (alass/ffsubsync)
```

The API key does **not** belong in this file in plaintext. Store it in your OS keychain instead:

```console
$ saitenka set-jimaku-key <your-key>
```

!!! tip "Key resolution order"
    The `[jimaku] key` value → `$JIMAKU_API_KEY` → OS keyring. `set-jimaku-key` writes to the keyring
    (or an owner-only `jimaku.key` beside the config on Linux without the `linux-keyring` extra), so a
    GUI-launched mpv — which can't inherit a shell-only variable — can still read it.

## Rebinding keys

Most in-player shortcuts are configurable — `translate_key`, `sub_next_key`, the `[mine]` keys, and
more take any mpv key name. Rather than list them here, see the
[Keyboard shortcuts](shortcuts.md) page, which has the full rebindable-key table with defaults.

## The full reference

!!! note "Everything is in the example file"
    Every recognized key, at its default, with an inline comment, is in the commented
    [`overlay.example.toml`](https://github.com/serjflint/saitenka/blob/main/overlay/overlay.example.toml).
    Uncomment a line only to change it. This page covers the common cases; that file is the source of
    truth for prefetch tuning, mpv coexistence, telemetry, stats, `[dictdb]`, and the rest.

For getting the file in place at all, see [Install](../start/install.md).
