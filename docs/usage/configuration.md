# Configuration

Saitenka reads its settings from a single TOML file, `overlay.toml`. The shipped
[`overlay.example.toml`](https://github.com/serjflint/saitenka/blob/main/overlay.example.toml)
is the exhaustive, self-documenting reference: **every** recognized key appears there at its built-in
default, commented, sshd_config style. This page is the curated tour of the handful of settings most
people actually touch — what each does and *why* you'd change it. For the full list, read the example
file.

## Where the config lives

`overlay.toml` sits in your platform config directory (`~/.config/saitenka/` on Linux,
`~/Library/Application Support/saitenka/` on macOS, `%LOCALAPPDATA%\saitenka\` on Windows). You never
have to guess the exact path:

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
model = "Lapis"              # Anki note type / model (must already exist)
preset = "Lapis"             # "Lapis" or "Kiku" — fills the field map + card_kind for a known type
card_kind = "word-and-sentence"  # card template marked: word-and-sentence · sentence · audio · click · none
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

**Note type, card kind, and field map.** `model` is your Anki note type — it must already exist (a
deck is auto-created on the first mine, a note type can't be). `preset` (`"Lapis"` or `"Kiku"`) fills
the field map and card kind for those known types, so you don't spell them out; the two share field
names, differing only in card template. `card_kind` picks which template the card is marked as —
`word-and-sentence` (the Lapis/Kiku default), `sentence` (Saitenka's historical marker), `audio`,
`click`, or `none` — setting exactly one `Is…Card` flag. For any other note type, override the
logical→real field map in a `[mine.fields]` sub-table (only mapped fields are written):

```toml
[mine.fields]              # e.g. an Animecards note type
expression = "Word"
reading    = "Reading"
```

`saitenka doctor` validates the effective field map against the note type and warns about names it
lacks (those values would silently go nowhere); unknown fields are also dropped at mine time so the
note still adds.

**Field templates (`[mine.card_format]`).** For full control — Yomitan's model — give each note field a
template of `{marker}` tokens instead of a single entity. When present it **wins wholesale** over
`[mine.fields]` (only the fields you list here are written; the entity map is ignored), so one field can
combine markers and one marker can fill several fields:

```toml
[mine.card_format]
Word     = "{expression}"
Reading  = "{furigana}"
Sentence = "{cloze-prefix}<b>{cloze-body}</b>{cloze-suffix}"
Pitch    = "{pitch-accents}"
Image    = "{screenshot}"
```

All markers are filled from real data — nothing fabricated. `saitenka doctor` warns about a `{marker}`
Saitenka can't fill (it would render empty) and about a field name the note type lacks.

{%
  include-markdown "./_card_format_markers.md"
%}

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

**Word-pronunciation audio.** `word_audio_enabled = true` + `word_audio_pack_dir = "..."` attaches a
clean word-pronunciation clip alongside the mined scene audio, resolved offline from a local
yomichan/yomitan audio pack — a directory with an `index.json` mapping expression → reading → audio
file(s). Lookup is keyed on the expression **and** the reading mining already resolved (homograph-safe),
so a miss (pack doesn't have the word, or that reading) just leaves `word_audio_field` (default
`WordAudio`) empty — never a bad guess. No online fallback (JPod101/TTS): not grounded/local-first, so
not adopted (issue #93).

```toml
word_audio_enabled = true
word_audio_pack_dir = "~/audio-packs/nhk16"
word_audio_field = "WordAudio"   # must exist on the note type
```

!!! note "Anki starts itself"
    When mining (or Anki-backed coloring) is on, Saitenka launches Anki for you if it's closed.

**Kanji Study deep-link ID.** The default field map's `id -> ID` fills from the JMdict `ent_seq`, which
makes `kanjistudy://word?id={{ID}}` deep-link into Android's Kanji Study app from a mined card. It needs
one of two sources: the optional `jmdict` extra (jamdict), or an imported JMdict-derived dictionary
(JMdict itself, Jitendex, …) with `[dictdb] persist_seq = true` — re-import the dict after enabling it so
its `seq` gets persisted. `saitenka doctor` reports which source (if any) is active. Neither source
touches cards mined before it existed; backfill an existing deck with
`uv run --extra full python tools/backfill_deeplink_id.py --apply` (dry-run by default — it enumerates
the `[mine] deck`, resolves each empty `ID` with the same source config uses, and never overwrites a
filled field). `tools/anki_normalize_fields.py` is an alternative that fills from a JMdict Yomitan zip.

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
- `kanji_stroke_order` (default `true`) — draw the kanji panel's big headword in a numbered
  stroke-order font; set `false` for a plain glyph.

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

    After saving, it runs a one-shot test search against jimaku.cc and reports whether the key works
    (`--no-verify` to skip). This catches a wrong-but-full-length key that would otherwise fail silently
    mid-video; run the same check any time with `saitenka jimaku-check`.

!!! warning "Windows: antivirus flagging the Credential Locker"
    If your AV blocks the first Credential Locker read, store the key in the owner-only file instead and
    skip the keyring on both write and read: `saitenka set-jimaku-key --file <your-key>` (it persists
    `[jimaku] keyring = false`). A one-off `SAITENKA_JIMAKU_KEYRING=0` does the same for a single run.

## Rebinding keys

Most in-player shortcuts are configurable — every key lives under the `[keys]` table (`mine_key`,
`translate_key`, `sub_next_key`, `profile_cycle_key`, and more), each taking any mpv key name. Rather
than list them here, see the [Keyboard shortcuts](shortcuts.md) page, which has the full
rebindable-key table with defaults.

## Experimental mpv-native subtitles

`[subtitle_geometry].native_visible = true` keeps an external authored `.ass` track rendered by mpv
and uses an optional system `libass` only for hover geometry. Saitenka draws the tooltip and a small
focus box, not a replacement subtitle raster. Unsupported syntax, missing source bytes, or a provider
failure leaves the mpv subtitle visible and disables interaction for that cue. Run `saitenka doctor`
to verify `libasslite` and the selected library; `overlay.example.toml` owns the settings and defaults.

Install the system-library wrapper with `uv tool install 'saitenka[subtitle-geometry]'`, or use
`saitenka[subtitle-geometry-bundle]` for the separate self-contained native runtime. An explicit
`LIBASSLITE_LIBRARY` path always wins; `LIBASSLITE_BUNDLE=0` skips an installed bundle and restores
system discovery. This first experiment accepts UTF-8 external ASS and only the render envelope proved by the parity gate: no mpv
ASS overrides/window scaling, scale 1, position 100, no video margins, embedded fonts disabled, the
automatic native font provider, full video-data forwarding, and no custom subtitle font directory.
`saitenka run` sets that profile when the mode is enabled. For `saitenka attach`, set the equivalent
mpv options yourself; a mismatched running player reports `subtitle-render-input-unsupported` and
keeps its native subtitle visible without interaction.

```conf
sub-ass-override=no
sub-ass-scale-with-window=no
sub-scale=1
sub-pos=100
sub-use-margins=yes
sub-ass-use-video-data=all
sub-ass-style-overrides=
sub-font-provider=auto
embeddedfonts=no
sub-fonts-dir=
```
Other encodings, attached/custom fonts, letterboxing, or subtitle style overrides stay visible through
mpv but are deliberately noninteractive.

## Reading profiles (a non-Japanese main language)

A **profile** bundles what's being read — the main/second language codes, the tokenizer strategy, and
its own dictionaries and mining target — so a single config can hold a Japanese setup and, say, a French
one side by side. With no profile, everything behaves exactly as before (Japanese, `unidic`).

Define one as `[profiles.<name>]` and activate it with `active_profile = "<name>"` (or `--profile <name>`
for one launch). A profile's `dicts`/`freq`/`pitch` **replace** the top-level lists (a French profile
consults only French dictionaries), and a `[profiles.<name>.mine]` table **merges** over `[mine]` so a
profile can override just the deck and inherit the rest. A non-Japanese language must name its
`tokenizer` explicitly — there is no silent fallback. The commented `[profiles.french]` block in the
example file is a complete template.

## The full reference

!!! note "Everything is in the example file"
    Every recognized key, at its default, with an inline comment, is in the commented
    [`overlay.example.toml`](https://github.com/serjflint/saitenka/blob/main/overlay.example.toml).
    Uncomment a line only to change it. This page covers the common cases; that file is the source of
    truth for prefetch tuning, mpv coexistence, telemetry, stats, `[dictdb]`, the `[tsukihime]`
    subtitle fallback, `[watch]` playback continuity, and the rest.

!!! tip "Edit it interactively"
    `saitenka config` opens an in-terminal editor over `overlay.toml` with per-option help — it
    preserves your comments and formatting, so you don't have to hand-edit the file.

For getting the file in place at all, see [Install](../start/install.md).
