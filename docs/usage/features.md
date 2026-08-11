# Features

A tour of what Saitenka does once it's running inside mpv — colored subtitles, a
multi-dictionary tooltip on hover, one-key mining into Anki, and the surrounding study
aids. Everything is composited into mpv's *own* OSD surface, so there's no second window
and nothing to alt-tab to.

!!! tip "First run"
    If you haven't installed yet, start with the [Quickstart](../start/quickstart.md). Once
    the overlay is wired in, open any video in mpv (plugin mode) or run
    `saitenka run video.mkv` directly. Every keypress below is pressed **inside the mpv
    window** — see the full [keyboard shortcuts](shortcuts.md) for the complete list, and
    [configuration](configuration.md) for the settings each feature reads.

## FSRS subtitle coloring

Saitenka hides mpv's native Japanese subtitle and redraws it with each word colored by how
well you actually know it. "Known" isn't a static list — it comes from your Anki review
state, so a word you learned but recently forgot resurfaces as *not* known.

- **Known set** comes from Anki over AnkiConnect (decks → fields, SubMiner-style) or a
  manual `--known "私,本,経"` set.
- An optional **FSRS** path to a *copied* `collection.anki2` refines that binary set into
  distinct **learning**, **young**, **mature-known**, and **forgotten** bands. Saitenka
  never opens the live Anki database.
- **N+1 targeting:** a sentence with exactly one unknown content word — the ideal thing to
  mine — is highlighted, and remaining words fall back to a frequency-band color.
- **JLPT underline:** words carry a level underline; frequency comes from a user-supplied
  Yomitan frequency dictionary, JLPT from the vendored wordlist.

```console
# color from your real Anki/FSRS deck (config supplies the rest)
saitenka run episode.mkv --color \
  --anki-decks '{"Saitenka::Known":["Entry","Expression","Word"]}' \
  --freq "Frequency General"
```

The learning and young colors live under `[palette]` in your config; see
[configuration](configuration.md).

## Hover → multi-dictionary tooltip

Move the mouse over any word and a Yomitan-style tooltip opens above it. Each word has its
own hitbox, and lookups run through a de-inflector so an inflected form resolves to its
dictionary entry.

- **Multiple dictionaries, stacked in order.** Import your Yomitan term-banks once
  (`saitenka import ~/yomitan-dicts`); they build into a single on-disk database and open
  instantly at play time. Each dictionary shows as its own section with a name pill and rich
  structured content (ruby examples, notes, cross-refs).
- **Pitch accent and frequency pills** sit under the headword — one green pill per frequency
  dictionary, plus a purple pitch pill (e.g. `ほんめい [0]`) that marks devoiced (○) and nasal
  (゜) mora from NHK/Kanjium data.
- **Longest-match on hover.** A phrase the tokenizer over-splits still resolves as one entry:
  hovering 数 in *数ある* looks up the whole 数ある and stacks it **above** the bare 数
  (longest match first, like Yomitan), with the underline spanning the matched phrase. A leading
  honorific folds in too, so hovering 休み in *お休み* surfaces お休み.
- **De-inflected form** and the inflection chain (🧩 `-て « -いる « -た`) display when the
  optional GPL deinflect add-on is installed.
- **Nested lookups:** cross-references inside a definition are themselves scannable, and the
  tooltip supports wildcard search and in-tooltip word scanning.

A bilingual entry can be long, so **scroll the mouse wheel over the tooltip** to reach the
lower (monolingual) sections. With no dictionary configured, Saitenka falls back to JMdict.

```console
saitenka import ~/yomitan-dicts        # once: build the DB, register titles in config
saitenka run episode.mkv --color \
  --dict "Bilingual Dict" --dict "Monolingual Dict A" --dict "Monolingual Dict B"
```

- **Kanji panel.** With a tooltip open, press ++k++ (or click a headword kanji) for a
  Yomitan-parity kanji entry: meanings, on/kun readings, and sectioned KANJIDIC stats
  (Statistics / Classifications / Codepoints / Dictionary Indices). The big headword glyph is
  drawn in a numbered **stroke-order** font (on by default; `[tooltip] kanji_stroke_order`).
- **Inline dictionary images.** Structured-content images — including `<text>`-based SVG gaiji
  (rare/embedded glyphs) — render inline when the optional `images` extra is installed; an
  unresolved glyph falls back to a ▢ box rather than vanishing.

!!! note "Not selectable text"
    The tooltip is drawn, not a text widget, so you can't select it. Press ++c++ to copy
    the hovered word + reading (++shift+c++ copies the whole cue), right-click to copy the
    word under the pointer, or ++a++ to hear it (Japanese TTS).

## Official-translation reveal

The default is **JP primary, EN secondary** — the anti-crutch setup. Saitenka auto-selects
the Japanese track for reading and mining and leases the embedded English track only when
you ask for it.

- ++t++ toggles the **English line** for the current cue, drawn above the Japanese subtitle
  — the professional translation on demand, not by default.
- ++alt+t++ switches the primary subtitle between **Japanese-only** and **English-only**.

## One-key & bulk mining

Hover a word and mine it straight into Anki — sentence, audio, and a clean screenshot — all
without leaving the video. Mining needs **Anki open** with the **AnkiConnect** add-on.

- ++ctrl+m++ mines the **hovered** word into a Lapis-style card: Expression / reading /
  **Sentence** (mined word bolded) / **Glossary** / **Picture** (clean frame) /
  **SentenceAudio** (the subtitle's audio span as mp3) / provenance / JMdict ID. Dedup
  checks the deck first, so there are no silent duplicates.
- ++ctrl+shift+m++ mines the hovered word with an **animated (motion) clip** instead of a still
  frame — a short GIF/WebP of the moment, for cards that read better in motion.
- ++shift+m++ **bulk-mines** every unknown content word in the current cue, all sharing one
  screenshot and audio clip; a toast reports `mined N · M dup`.
- **Post-mine preview:** a fixed-layout panel shows the card (status, headword + reading, the
  sentence, meaning, the *actual captured frame*, and `▶ Ns` audio that auto-plays) so you
  can verify it without alt-tabbing. Mining an existing word previews the card already in
  Anki. ++p++ replays the last preview.
- **Word-pronunciation audio (optional).** With a local yomichan/yomitan audio pack configured
  (`[mine] word_audio_enabled`, `word_audio_pack_dir`), mined cards also get a `WordAudio` clip
  resolved offline from the expression + reading — grounded from the pack, never synthesized.

```console
saitenka run episode.mkv --color --mine \
  --anki-decks '{"Saitenka::Known":["Entry"]}' \
  --mine-deck "Saitenka::Mining" --mine-model Lapis
# then hover a word and press Ctrl+m
```

!!! warning "Mining writes real cards"
    Mined cards go to `Saitenka::Mining` tagged `saitenka` and are kept by default. Review
    or remove them in Anki via **Browse → `tag:saitenka`**.

## Reading profiles & second languages

Saitenka reads through a **reading profile** — the target language, the tokenizer strategy, and the
"known"/translation second language. The default profile is Japanese (`unidic` tokenizer, English
second), but the engine is language-agnostic: a **French** profile ships today (Latin tokenizer +
French de-inflection), and the tokenizer is a separate, user-set field so one strategy can serve a
family of languages.

- Manage profiles from the CLI: `saitenka profile list` / `show` / `add` / `use <name>` / `remove`,
  or select one for a single run with `saitenka run … --profile <name>`.
- A non-Japanese profile **picks its own subtitle track and font** automatically, and swaps in that
  language's dictionaries (a profile's `dicts` replace the base set; its `[mine]` overrides merge).
- ++alt+shift+p++ cycles the active profile live inside the player (a no-op with a single profile).

Profiles live under `[profiles.<name>]` in your config with a top-level `active_profile`; see
[configuration](configuration.md).

## Subtitle sidebar & analysis

Two playback-neutral study views, both toggled from the player:

- ++backslash++ opens the **whole-track subtitle sidebar** — every cue of the episode, with
  active-cue tracking and manual scroll. N+1/N+2 badges appear once episode analysis
  finishes, and cues you bookmark (++alt+b++, save a cue for later without pausing) survive
  reopening the same file elsewhere.
- ++grave++ (backquote) opens the **whole-track analysis**: sentence and content-token
  totals, unique lemmas and kanji, unknown vocabulary, known coverage, N+1/N+2 counts, and
  optional JLPT/frequency distributions. It runs in the background and is cached per subtitle
  + vocabulary snapshot, so it opens without pausing playback.

Opt-in local **session history** (`[stats]` in your config) records watch time, cues,
lookups, deferred captures, and cards mined without telemetry; `saitenka stats` lists recent
sessions.

## jimaku fetch

When the file has no Japanese subtitle, Saitenka fetches one in the background from
**jimaku.cc** — the title and episode are parsed from the filename. Fetched Japanese is
added without switching tracks; if Japanese is unavailable it shows English immediately
rather than blocking.

```console
# no JP subs on the file → fetch from jimaku (key via config, --jimaku-key, or $JIMAKU_API_KEY)
saitenka run show.mkv --jimaku --color
```

- ++ctrl+shift+t++ retries the enabled provider chain (jimaku, then opt-in TsukiHime) for the
  current media without switching tracks.
- ++ctrl+j++ opens the **jimaku download picker** — browse and choose which subtitle file to
  pull for the current media, rather than taking the automatic best match.
- Store the key once with `saitenka set-jimaku-key`; `saitenka jimaku-check` diagnoses it
  without launching a video.

## Subtitle resync

Mistimed subtitles are auto-aligned (alass) so the redrawn line matches the audio without
manual nudging. When automatic alignment isn't enough, tune it live:

- ++ctrl+z++ **anchors** the subtitles to the current playhead — re-syncs the track from here.
- ++ctrl+shift+t++ re-times the subtitles from the current position (and fetches a track if the
  file has none).
- mpv's own ++z++ / ++shift+z++ / ++x++ still nudge and reset sub-delay by ∓0.1 s (passed
  straight through, not intercepted).

## mpv coexistence

Saitenka doesn't need to own mpv — it *joins* the player over IPC and coexists with other
mpv scripts (mpv_websocket, animecards) on the same socket. Two modes:

=== "Attach mode"

    Join an already-running mpv by pointing at its IPC socket (`input-ipc-server` in
    `mpv.conf`, or pass the path). Good for a one-off session over a player you started
    yourself.

    ```console
    saitenka attach /tmp/mpv-socket
    ```

    On Windows, bare `attach` uses mpv.net's `\\.\pipe\mpvsocket` default. `saitenka doctor`
    reports whether `mpv.conf` sets `input-ipc-server` and which known tool uses that socket.

=== "Plugin mode"

    Install a one-file user-script so **any** mpv launch auto-spawns the overlay in attach
    mode — open a video in mpv and the overlay is just there.

    ```console
    saitenka install-plugin      # writes ~/.config/mpv/scripts/saitenka.lua (backs up first)
    saitenka uninstall-plugin    # removes it (backs up first)
    ```

!!! note "If another script owns the OSD"
    If another mpv script already uses OSD overlay ids 1–6, set `overlay_id_base` in your
    config to shift Saitenka's ids. ++alt+o++ hides/shows the overlay and restores mpv's
    native subtitles and OSD while hidden.

## Watch-party controls

Saitenka runs alongside **Syncplay** — it attaches to each participant's *local* mpv, so it
doesn't fight the synchronized playback. Turn hover auto-pause off (++alt+p++) and the
study actions stay room-safe: opening tooltips, switching JP/EN, retrying providers,
bookmarking, and opening analysis/help never pause or seek the room. Subtitle navigation
stays available too:

- ++alt+left++ / ++alt+right++ — jump to the previous / next subtitle line.
- ++alt+down++ — replay the current line from its start.

An explicit sidebar seek is an ordinary synchronized seek, so the party stays aligned. For
the full mapping, see the [keyboard shortcuts](shortcuts.md).
