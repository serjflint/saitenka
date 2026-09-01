# Changelog

All notable changes to Saitenka are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/). Entries are curated for readers — they are not raw commit
logs.

## [Unreleased]

### Fixed

- **Pitch accents and frequency pills now reach a dictionary entry keyed under its kana reading.**
  A kanji-written word never matched such a row, so for NHK 2016 that silenced pitch on 12,228
  headwords that had an accent all along. The row is admitted per `(dictionary, reading)`, so a
  reading shared by several words does not hand all of them the same accent.
- **A reading spelled in a different script now matches.** Frequency dictionaries write readings
  their own way — BCCWJ has `ぷれえやあ` for `プレーヤー`, Jiten has `ゴミいれ` — and a literal
  comparison dropped the row, taking that dictionary out of the word's pill row and out of its
  blended rank. Comparison folds katakana to hiragana and expands `ー`; over the sampled headwords
  that carry any frequency row, 1,444 recover a dictionary they had been losing.
- **Dictionary lookups were a full table scan on every install created before 4.3.1.**
  `CREATE INDEX IF NOT EXISTS` matches on the index *name*, so a corrected column order never
  reached an existing database and reported no error. Indexes are now compared against their
  intended definition and rebuilt when they differ — no re-import needed.
- The frequency pill again shortens a dictionary's name (`Saitenka Known` → `Known`), and a word
  no dictionary has again shows its inflection chain — which, in a second-language profile, is the
  only thing that can explain it.

### Changed

- **Four more capabilities are separate distributions:** `saitenka-tokenize`, `saitenka-wordstate`,
  `saitenka-subtitles` and `saitenka-card`, joining `saitenka-dict` and `ankiconnect-client`. They
  install automatically with `saitenka` and need no action; each is usable on its own, without a
  player or a running Anki. `pysubs2` is consequently no longer a direct dependency of `saitenka` —
  it belongs to `saitenka-subtitles`.

## [4.3.1] - 2026-08-30

### Fixed

- **Frequency pills, dictionary-name pills, pitch-accent graphs and bullet markers now raster at the
  display scale** instead of being stretched from a reference-size sprite, so they are as crisp as the
  text beside them. Visible wherever the tooltip renders above 1.05×, and on every render when
  `tip_scale` is pinned in `overlay.toml`. Layout is unchanged — a pill still fills exactly the box
  reserved for it at 1×, so nothing moves under the hit-test.
- **`saitenka attach` could lose word scanning and subtitle overpaint for a whole session.** A
  subtitle selection the session made itself raced mpv's `sid` echo and left the track holding its
  cues with no geometry source at all. `saitenka run` was never affected.
- **Native subtitle geometry no longer accepts a converted SubRip track it cannot reproduce.** mpv
  applies `sub-scale` and `sub-pos` to converted tracks regardless of `--sub-ass-override`, so
  widening on the override alone placed interaction boxes silently wrong instead of declining the
  track. `sub-ass-justify` on an authored track under `scale` is refused for the same reason, and
  episode advance again applies the profile's language guard when re-slotting.

## [4.3.0] - 2026-08-30

### Fixed

- Subtitle-picker downloads remain clickable after the active cue disappears, without admitting
  stale cue actions on other surfaces. Interactive panels now share an explicit paint order and stay
  above native subtitle overpaint.
- Subtitle re-timing now fails visibly without replacing the current track when its local alignment is
  incoherent. The former whole-file fallback could overwrite a cached ASS file with SRT content and
  leave subtitles visible but no longer interactive.
- Alt+O now confirms both the hidden and restored overlay states.
- `saitenka report` captures native doctor diagnostics in the bundle instead of printing them beside
  the archive path.
- SQLite owner finalizers no longer attempt thread-affine closes during interpreter shutdown.

## [4.2.0] - 2026-08-29

### Fixed

- **Overlay publication could fail on Windows with `PermissionError` (WinError 5).** Each frame was
  published by renaming onto one stable path per overlay, and Windows refuses to replace a file
  another process still holds open — which mpv does, since it reads the frame *inside* `overlay-add`.
  Frames are now published at a fresh path and retired once nothing names them, so no rename ever
  lands on a live file. The same oversight affected deletion: retiring a frame mpv still held raised
  rather than being retried. Measured at 0.3 ms per publish for a 1.1 MB frame, against 0.5 ms for
  the path it replaces.

### Changed

- **Native-visible subtitle geometry now requires mpv ≥ 0.40, up from ≥ 0.39.** The old floor was a
  claim nothing could check: no distribution ever shipped an mpv 0.39 (Ubuntu went 0.37 → 0.40,
  Debian 0.35 → 0.40), so there was no binary to test the declared minimum against. 0.40 is the
  earliest release CI can pin and prove. The regular overlay path is unaffected and still supports
  mpv ≥ 0.37.

### Development

- Completed the reading-session ownership migration. `SessionController` now retains only ordered
  owner-thread turns and lifecycle boundaries; feature policy, mutable state, background work, and
  presentation belong to composed feature owners, with structural checks preventing authority from
  leaking back into the shell.

## [4.1.0] - 2026-08-24

### Changed

- **`saitenka[full]` now installs the portable native add-ons** — the taffy layout engine, the SVG
  gaiji rasterizer, and native subtitle geometry — instead of leaving each as a separate opt-in. All
  three publish prebuilt wheels across every interpreter Saitenka supports, and all three are
  permissively licensed, so they change what notices a redistributor ships and nothing about what
  `full` already was. `subtitle-geometry-bundle` deliberately stays out: it ships a native runtime
  closure carrying its own notices and relinking obligations, so installing it remains a separate,
  deliberate choice — see [LICENSING.md](LICENSING.md).
- **The `images` extra now installs [`resvg-py`](https://github.com/baseplate-admin/resvg-py) instead
  of the in-tree `resvglite`.** Upstream now covers the same wheel matrix and error contract Saitenka
  needed the local binding for, so there is no reason to keep maintaining a second one. Gaiji render
  slightly heavier: `resvg-py` embeds resvg 0.48, which applies variable-font weights the 0.45 build
  ignored, so the bundled Noto draws at its real weight instead of thin. `resvglite/` is retained
  in-tree for now but is no longer installed by any extra.

### Fixed

- **`saitenka install-plugin` baked whichever `saitenka` led `PATH`, not the one that was running.**
  Under `uv run` that is the project's cache environment, so an mpv started from Finder ran a copy
  nobody chose — and `saitenka doctor` reported the mismatch on a clean editable install. The console
  script beside the running interpreter now wins, with the `PATH` lookup kept as the fallback for
  `python -m saitenka` out of a bare venv.
- **Dictionary connections opened by background workers were never closed.** A thread-local store's
  `close()` could only reach the calling thread's connection, so every prefetch and prewarm worker's
  stayed open until the interpreter exited.
- **No `saitenka report` bundle had ever carried a session summary.** The startup banner and the
  end-of-session summary bypassed logging to reach a terminal at all, so neither was redacted and
  the summary never reached `overlay.log`. Both now go through a console channel that renders them
  plainly and still writes to the log file. The banner moves to stderr, beside every other line the
  overlay emits — redirecting stdout no longer captures it.

### Development

- The reading session's largest object is decomposing: `Reader` is now `SessionController`, and
  reading profiles and tooltip work each moved behind a bounded owner of their own.

## [4.0.0] - 2026-08-24

### Added

- **mpv draws the subtitles; Saitenka colors them.** The native subtitle renderer stops replacing
  mpv's subtitle layer and starts annotating it: the cue you see is mpv's own libass render, with its
  authored typesetting, fonts and effects intact, and each word tinted by its reading state on top.
  Scanning, hover, tooltips and mining work on those words exactly as before. Enable it with
  `native_visible = true` under `[subtitle_geometry]`; `Ctrl+Shift+L` asks for the previous renderer
  for an episode, and `saitenka doctor` reports whether a compatible libass can be initialized.
- **SubRip tracks are interactive too.** mpv never renders a `.srt` — libavcodec converts it to ASS
  and mpv renders that, through a branch of styling it applies to no authored track. Saitenka
  reconstructs that conversion around the cue rows mpv reports, so a converted track gets the same
  hit boxes as an authored one. `native_formats = "all"` takes them; the default stays authored-ASS.
  Your `--sub-font`, `--sub-font-size`, margins, alignment, justification, blur and colors are read
  from mpv and reproduced, so the boxes land on the words wherever your own settings put them.
- **Cue lookahead on converted tracks**, by predicting the events libavcodec will produce for the
  cues ahead. A wrong prediction cannot put a box in the wrong place — the cache is keyed on the
  event rows, so a mispredicted cue simply misses and is measured on arrival.
- **A color ladder, so a cue degrades instead of going dark.** Tokens mpv's OSD library can redraw
  as text take that path; the rest are tinted as a raster over the glyphs already on screen; the JLPT
  level keeps its own underline beside either. A cue that cannot be measured — karaoke, animation, a
  vector drawing — keeps mpv's rendering and loses only its interaction, without the renderer
  switching under you.
- **`--blend-subtitles=yes` is reproduced** rather than refused, and Retina and letterboxed windows
  are followed instead of rejected.
- **`saitenka doctor` flags an `mpv.conf` that sets `sub-font` more than once.** mpv takes only the
  last one; the earlier lines look like a fallback chain and are not one.

### Changed

- The subtitle renderer is now a choice rather than an implementation detail: the standard renderer
  remains the default, `native_visible` opts into mpv's pixels, and both are compared at the level a
  user sees. `subtitle-report` and `trace-report` name which one owns the frame and why.

### Fixed

- **Every converted cue was measured against mpv's default style**, so a customized `--sub-margin-y`
  or `--sub-font-size` put the boxes where somebody else's subtitles would have been.
- **A geometry gate row refused native subtitles on every mpv that had the option it named**
  (`sub-ass-vsfilter-aspect-compat`), and passed vacuously on the builds that removed it.
- **Reading a container's font attachments transcoded the whole episode** — 42s on a 24-minute file
  against 0.06s — which held up keybind registration for the whole of it.
- **Global shortcuts were registered weakly** and lost to a user's own `input.conf`; F1 opened mpv's
  OSD instead of Saitenka's help.
- **`Alt+o` left the token colors and JLPT underlines on screen** until the next cue.
- **Converted-track styles were fully transparent**, so no cue on a converted track had any boxes.
- `native_formats` was never read out of `overlay.toml`.
- **Exiting Saitenka reliably restores mpv's own subtitle visibility.** Teardown writes are flushed to
  the socket before the connection is dropped, so the setting a session found is the setting it leaves —
  previously the last write could still be queued when the transport went away.
- **Hovering and tooltip scrolling no longer wait on optional services or mpv IPC.** Phrase/mining
  metadata, Anki and TTS capability checks, tooltip raster preparation, file staging, and transport
  writes now run behind bounded session workers. Rapid hover and wheel input is newest-wins, while
  visible-paint acknowledgements and geometry provenance make delayed interaction diagnosable with
  `saitenka trace-report`.
- **Startup remains interactive while dictionaries and cue annotations finish.** Publishing loaded
  dependencies no longer runs tokenization, dictionary attestation, or scoring on the mpv event-loop
  thread. Current cues preempt episode warming, stale results cannot restore old hit boxes, and the
  owned `saitenka starting...` breadcrumb clears only after a complete operable poll. Correlated IPC
  replies and `saitenka trace-report` make the startup critical path diagnosable without subtitle text.
- **Native subtitle scanning no longer crashes on sparse hit boxes.** Tooltip and kanji anchors now
  resolve the box by token identity when punctuation or whitespace has no interactive geometry.
- **Native subtitle styling no longer flashes back to the standard renderer.** mpv pixel ownership now
  survives cue transitions, cache misses, delay changes, unsupported geometry, and provider failures;
  unproved hit boxes disappear and return asynchronously without changing subtitle style. Only a current
  assert-true/readback-false visibility transaction can authorize catastrophic legacy recovery, with
  bounded retries and text-free ownership telemetry in `saitenka subtitle-report`.
- **Native subtitle interaction now follows mpv subtitle delay.** Delay adjustments no longer discard
  lookahead geometry, and lookahead follows authored start and end transitions so upcoming static ASS
  frames can publish their prepared hit maps at presentation time.
- **Native subtitle hit geometry now handles simultaneous ASS events and non-painting tokens.** Active
  rows are matched as one event-aware frame, while whitespace/control-only tokens no longer require
  libass pixels. Related mpv property updates are evaluated as one batch, and bounded diagnostics make
  geometry decisions visible in `saitenka subtitle-report` without recording subtitle text.
- **Experimental native subtitle interaction now follows Retina and letterboxed windows.** The hidden
  geometry render mirrors mpv's full frame margins and authored-ASS margin policy instead of rejecting
  any display whose video does not fill the OSD frame.
- **Structured Jitendex entries keep their document shape.** Nested senses, examples, translations,
  cross-references, forms, and attribution now render on separate lines with the dictionary's own list
  markers instead of being glued into one paragraph, including lookups through the semantic
  `saitenka-dict` adapter.
- **Episode re-slotting now distinguishes identical filenames in different folders.** Moving between
  seasons that both contain `Episode.mkv` no longer leaves the overlay attached to the previous file.

### Development

- **A renderer now outlives the cue it draws.** libass keeps the library, the renderer's glyph cache
  and the track on three different lifetimes; building all three per cue meant a library init and a
  font-directory rescan for every subtitle, and a renderer cache that could never hit. `libasslite`
  grows `set_document`, and the geometry backend keys renderers on the font environment.
- A live tier asks mpv what it actually does — which options exist, what the launch profile resolves
  to, and whether our keybinds outrank the user's — instead of asserting our beliefs about it.
- The gate declares the native extras it renders against, and its libass oracle now runs in CI rather
  than silently skipping.
- Extracted provider-neutral subtitle geometry and event identity seams, with generation/request
  ordering that rejects stale background results without discarding a freshly sought cue. Added the
  independently publishable experimental `libasslite` package for copied libass 0.17.x image layers;
  default subtitle rendering is unchanged while parity and performance work continues in #350.

- Continuous benchmark points now aggregate three independent GitHub runners, validate metric schemas,
  summarize results on pull requests, and serialize Pages writes. The core suite covers rendering,
  subtitle scanning, generated-dictionary import/query, and click/store work; weekly jobs cover bounded
  cache churn and live-mpv jank.
- Extracted renderer-neutral Yomitan dictionary semantics into the independently testable
  `saitenka-dict` package and AnkiConnect transport/protocol handling into `ankiconnect-client`.
  Saitenka now has a swappable lookup-source adapter, a dictionary-independent known-word cache, and a
  batched headless-Yomitan differential report covering all term modes, metadata, structured content,
  and kanji. An opt-in structure oracle also compares Yomitan's generated DOM with Saitenka's blocks
  immediately before layout for an entry in the installed database.

## [3.1.0] - 2026-08-11

### Added

- **`[tooltip] sub_background_opacity`** (0–255, default 150) sets the alpha of the box behind the
  subtitle text; `0` makes it fully transparent.

### Changed

- **Any subtitle mpv renders itself is now center-aligned** (`--sub-align-x`/`--sub-justify`/
  `--sub-ass-justify` at launch), so a fallback/native track — one the overlay doesn't take over — is
  never left-aligned. This is horizontal only; the vertical position is unchanged.

### Fixed

- **Live profile cycle (++alt+shift+p++) is now a full switch.** Cycling to a second-language profile
  mid-playback used to swap only the reading engine, leaving mpv on the original (e.g. Japanese) subtitle
  track — so lookups ran the new language against the old track's text and missed, and a manual track
  pick was classified as the plain, unscannable "known-language" secondary. The cycle now re-selects that
  profile's own subtitle track (the same path a `--profile` launch uses), so it colors and scans at once;
  a language the file has no track for keeps the current track and warns.

## [3.0.0] - 2026-08-11

### Added

- **Inline dictionary images — SVG gaiji (#283).** Yomitan structured-content `img` nodes now render:
  the optional `images` extra (a pure-Rust resvg binding, `resvglite`) rasterizes SVG gaiji — including
  `<text>` glyphs like 大辞林's 漢/呉 reading badges, which get the bundled font so they draw the kanji
  instead of an empty box. Anything unrenderable falls back to the ▢ placeholder; a default install is
  unchanged.
- **Kanji panel — Yomitan parity (#99).** The kanji entry (hover a word and press `k`, or click a
  headword kanji) now matches Yomitan: KANJIDIC stats are **labeled and sectioned** (Statistics /
  Classifications / Codepoints / Dictionary Indices) instead of cryptic truncated codes, and the big
  headword is drawn in a numbered **stroke-order font** (on by default — `[tooltip] kanji_stroke_order`).
  Every headword kanji is a click-to-open link to its own kanji entry, navigated in place.
- **Pitch-accent detail — devoiced (○) and nasal (゜) mora markers (#305)** rendered from NHK/Kanjium
  data on the pitch graph.
- **Structured content — superscript / subscript and strike-through (#303)** now render as small
  raised/lowered annotations, matching more monolingual dictionaries.

### Changed

- The optional `taffylite` (layout) and `resvglite` (images) Rust extensions now resolve from **PyPI**
  rather than editable in-tree sources; `taffylite` moves to a pyo3 0.29 abi3 + abi3t wheel matrix (#307).

### Fixed

- **Importing a settings file with nested `[profiles.*]` tables no longer crashes** (#309); inline-image
  media is now preloaded for stacked (multi-reading) entries too, so their gaiji render (#283).

### Notes

- The richer kanji-stat labels come from each dictionary's own metadata; **re-import your kanji
  dictionaries** (`saitenka import …`) to populate them — until then the stats show as bare codes.

### Development

- **Tooltip rasterization moved off the interactive thread**: cold nested scan popups and clicked/keyed
  opens + cross-reference navigation are deferred (tier-3), and the render cache does its IO off the main
  thread — removing raster stalls from the hover/scroll path.
- Conformance & adequacy: a French differential deinflection corpus, a `(cue, hover-position) → Entry`
  pipeline oracle over the real run assembly, a corpus-drift guard, and a commit-time test-kinds advisory.
- An unattended py-spy benchmark runner with per-span percentiles + speedscope output.

## [2.4.0] - 2026-08-10

### Added

- **Second-language reading engine — French (#254).** Reading profiles no longer just *declare* a
  non-Japanese language, they make it work end-to-end: a Latin-script tokenizer segments the text, the
  optional `deinflect` add-on reduces an inflected word to its dictionary form (Yomitan-derived French
  rules), scoped Yomitan dictionaries are looked up, and mining writes a French note type. Proven on
  French anime subtitles — hover `parapluies` and you get the entry for `parapluie` ("umbrella"), not a
  bare "plural of" redirect. Manage profiles with the new `saitenka profile` subcommand
  (`list`/`show`/`add`/`use`/`remove`); `saitenka doctor` validates each profile's language, tokenizer,
  and scoped dictionaries.
- **Profiles pick their own subtitle track and font.** A non-Japanese profile now selects its language's
  subtitle track automatically (instead of grabbing the Japanese one or a stale fetched subtitle), and
  renders Latin / Cyrillic / Greek text in NotoSans for proper letterforms and word spacing.

### Changed

- The `deinflect` and `full` extras now require `saitenka-deinflect>=0.2.0` (the French rule set and the
  per-language deinflection API).

## [2.3.0] - 2026-08-09

### Added

- **`doctor` warns when the deep-link `ID` field can't be filled.** Mined cards carry a JMdict `ent_seq`
  in the `ID` field so Kanji Study `kanjistudy://word?id={{ID}}` deep-links work — but the value only
  comes from the optional `jmdict` extra, so on a default install it wrote empty with no feedback.
  `saitenka doctor` now flags this when the field map targets `id`/`ID` (or an `{ent-seq}` marker) and no
  id source is installed.
- **Profiles / configurable main language (#254).** `[profiles.*]` config plus `--profile` at the CLI:
  each profile bundles a language (any BCP-47 code, not just Japanese), a per-language tokenizer chosen
  via a strategy seam (unidic for Japanese), a second language (defaults to English), its own dictionary
  set, and its own Anki mine target. A live in-overlay profile switcher (++alt+shift+p++) swaps the whole
  bundle mid-session, and subtitle providers are gated to the active language.
- **Offline word-pronunciation audio (#93).** Mined cards can now pull word audio from a local
  yomichan/yomitan audio pack — grounded and offline, no TTS or network call — via new
  `[mine].word_audio_*` config.
- **Offline deep-link IDs (#255).** The JMdict `ent_seq` behind the `ID` field can now be sourced from an
  already-imported dictionary (`[dictdb] persist_seq`), so deep-links work without the `jmdict` extra.
  `doctor` verifies the source has real data, not just that the setting is on, and a new
  `tools/backfill_deeplink_id.py` backfills the `ID` field on cards mined before this was configured
  (dry-run by default; `--deck`/`--field`/`--apply` to write).
- **Sidebar "Mine" tab (#253).** Browse the current episode's mined cards from the sidebar; a durable
  per-card store links each Anki note back to its episode and cue.
- **`saitenka config` (#257).** An interactive TUI editor over the config, with per-option help.

### Fixed

- **Card-preview audio no longer keeps playing after you close the preview.** The ▶ clip was a
  fire-and-forget player whose handle was discarded, so dismissing the preview (✕, ++esc++, a new
  subtitle cue, or ++p++) left the audio running. The player handle is now retained and stopped on
  every dismiss path, and a second ++▶++ press replaces the clip instead of stacking a second one.
- **The sidebar bookmark (B) button no longer silently does nothing.** It re-checked that the clicked
  row was still the active cue at click time, so if playback advanced a cue between the sidebar redraw
  and your click, the click was dropped with no feedback (while ++alt+b++ on the same cue worked). The
  button now bookmarks unconditionally, matching the keybind.

### Development

- **taffylite ships to PyPI** via its own release workflow (#231), independent of saitenka's release.
- **New ast-grep rule** flags call sites with 3+ positional args, nudging toward keyword args (#225).
- **CI** now caches `uv` per interpreter and runs a parallel free-threaded test job alongside a GIL-on
  control.
- **Type-checking** widened to `examples/`, `tools/`, `install/`, and `.agents/`.
- **The repo is agent-agnostic**: `CLAUDE.md` is untracked per-agent config; `AGENTS.md` is the one
  canonical, committed source of agent guidance.

## [2.2.0] - 2026-08-09

### Added

- **`set-jimaku-key` verifies the key as you save it.** Right after storing a key, saitenka runs one
  probe query against jimaku and reports whether it works — so a mis-pasted key is caught at save time
  instead of on the first subtitle fetch. This targets the Windows PowerShell paste trap, where a plain
  ++ctrl+v++ in the hidden secret field stores a single character; the immediate check turns that into a
  clear "bad key" instead of a silent later failure. `--no-verify` skips the probe (best-effort — a
  network hiccup never blocks the save).
- **Force file-based jimaku-key storage (`--file` / `[jimaku].keyring = false`).** The OS keyring
  (Windows Credential Locker) can trip an antivirus prompt on the first jimaku call; you can now opt out
  of the keyring and keep the key in the config file instead. `saitenka doctor` points at this on Windows
  when no key is found.
- **`saitenka setup` now prompts for the mining field map.** The wizard walks the note type's fields and
  asks which logical value (expression / reading / sentence / audio / picture / …) fills each, so a
  non-preset note type is configured interactively instead of by hand-editing `[mine.fields]`. On
  re-setup the current mapping is offered as the default for every field.
- **`doctor`/`report` dump the full mining field map.** The effective `logical → real` field mapping is
  now shown in `saitenka doctor` and bundled in `report`, and it warns when a mapping targets a logical
  entity it doesn't recognise or leaves the note type's first field unwritten — the failure mode behind an
  "Anki won't add the card" report (an empty note is rejected).

### Fixed

- **Mining keybinds now always register.** In attach / plugin mode the mine keys (++ctrl+m++,
  ++ctrl+shift+m++, ++shift+m++, and any rebind) did nothing, because their registration was gated on Anki
  being ready — and Anki loads asynchronously after the keys are bound, with no second pass. They now
  register unconditionally; the handler checks Anki at press time and shows a toast if it isn't available,
  instead of the key silently never existing.
- **Telemetry trace writing self-heals.** When the CTF `trace.json` (or its directory) was removed
  mid-session by a cache cleanup or rotation, the writer retried the dead append every tick and logged the
  failure hundreds of times a run. It now recreates the file and continues, logging only a genuinely
  unwritable target.

### Development

- **Session mode is in the logs.** `run` and `attach` now each emit a `session: mode=…` line at startup,
  so a diagnostics bundle says which mode produced it.
- **Mining-keybind observability.** Keybind registration logs its count and whether Anki was live, warns
  when mpv rejects a bind, logs each dispatched `script-message`, and tags the mine span with whether it
  captured an animated screenshot — enough to explain a "nothing happened" mine from a report alone.

## [2.1.0] - 2026-08-08

### Added

- **`doctor`/`report` now check the subtitle-resync toolchain.** A new `resync` check resolves
  `ffprobe`/`ffmpeg` the way a GUI-launched attach does and **probes** them (`-version`), so a
  present-but-crashing binary is flagged instead of silently reading "ok" — the failure mode that hid
  behind a working shell PATH. It also reports whether an aligner (`alass`/`alass-cli` or the `uvx
  ffsubsync` fallback) is available; without one, re-sync can't run. The result is bundled in `report`.

### Fixed

- **Cached subtitles keep their real format.** A downloaded ASS subtitle was cached under a synthetic
  `.srt` name, so the aligner (which keys off the extension) had to be rescued by a content sniff. The
  cache now stores each subtitle under its real filename extension and evicts a stale sibling when a slot
  is re-downloaded in a different format — so a cached ASS source re-syncs cleanly on its own.

## [2.0.1] - 2026-08-08

### Fixed

- **Drag-'n'-dropped / untagged Japanese subtitles are now coloured and centred.** A subtitle track with
  no language tag — dropped into mpv, or picked by a manual track switch — was treated as the English
  fallback and drawn as plain white, left-aligned text. saitenka now adopts an untagged newly-primary
  track as Japanese and identifies Japanese by **content** (kana/kanji), not language tags alone, so FSRS
  colouring and centred layout apply. Press ++alt+j++ to force the current track as Japanese if a guess is
  ever wrong (rebindable via `[keys].subtitle_mark_jp_key`).
- **UTF-8 for every spawned tool.** All `subprocess` text-mode calls now decode child output as UTF-8
  rather than the OS locale codec. On a non-UTF-8 Windows (e.g. Japanese cp932) the `ffprobe` probes
  behind embedded-subtitle sync, duration, and language detection could mojibake or raise — they no
  longer do. (Files were already UTF-8-guarded by ruff `PLW1514`; a new ast-grep rule closes the same gap
  for subprocess.)

- **No hang on quit.** The new mpv.net auto-reconnect (below) could mistake a normal mpv quit for a
  dropped pipe and stall the overlay for up to ~10 s before exiting. It now probes that mpv is actually
  alive before treating a re-dial as a reconnect, so quitting exits promptly.

### Added

- **mpv.net resilience.** mpv.net drops its IPC pipe mid-session; the overlay now auto-reconnects the
  dropped pipe and replays its property observers instead of exiting, and `saitenka doctor` labels
  mpv.net explicitly.
- **Dictionary-database diagnostics.** `saitenka report` and `saitenka doctor --verbose` now show the
  consolidated DB's schema, file size, and per-dictionary row counts (entries / keys / kanji / term_meta /
  tags). It names no term, reading, or gloss, so a diagnostics bundle can reveal, for example, a
  dictionary whose tags never imported — without shipping any dictionary content.

### Development

- **Two-engine layout parity tests hardened** — negative controls that prove the parity oracle can fail,
  boundary `@example`s, and a production-seam `resolve_backend("taffy")` matrix (taffylite stays a
  dev-only cross-validation oracle).
- **New ast-grep gate `subprocess-utf8-encoding`** — flags any `subprocess.*(text=True)` without
  `encoding=`, run by `poe invariants`.

## [2.0.0] - 2026-08-08

### Fixed

- **De-inflection now keeps every distinct trace Yomitan shows** (`saitenka-deinflect` 0.1.1). The engine
  ports Yomitan's per-chain cycle guard instead of deduping by reached state, so a surface reachable two
  ways surfaces both — `来られる` is recognised as *potential or passive*, not only *passive*. Clears the
  last 12 Yomitan conformance-corpus vectors. The single inflection label shown in the tooltip is unchanged.

### Added

- **Interactive `saitenka setup` — arrow-key pickers + type-to-filter.** The deck, note-type, and
  card-kind prompts are now arrow-key selectable and filter as you type (a 50-deck collection no longer
  means typing a name from memory), and the silent AnkiConnect lookups show a spinner. It degrades
  cleanly: a non-tty (the mpv plugin spawns the wizard console-less), `--yes`, a legacy Windows console,
  or `SAITENKA_NO_TUI=1` fall back to the previous numbered-list / `[y/N]` prompts unchanged. Adds a
  `questionary` dependency; all the ways the wizard asks now route through one shared prompt seam.
- **Field templates for mined cards (`[mine.card_format]`).** Yomitan's model: give each note field a
  template of `{marker}` tokens (`Reading = "{furigana}"`,
  `Sentence = "{cloze-prefix}<b>{cloze-body}</b>{cloze-suffix}"`), so one field can combine markers and
  one marker can fill several fields. Opt-in; wins wholesale over `[mine.fields]` when set. Twenty
  markers, each filled from real data — `expression`/`reading`/`furigana`/`glossary`/`sentence`/
  `cloze-*`/`screenshot`/`sentence-audio`/`frequencies`/`pitch-accents`/… `saitenka doctor` warns about
  an unfillable marker or a field the note type lacks. (Word audio, pitch-accent graphs,
  sentence-furigana, and plain furigana are named as not-yet-supported rather than silently blank.)
- **Configurable mining note type — field map, card kind, and presets.** `[mine]` is no longer
  Lapis-only under a configurable-looking model name. Pick a known note type with `preset = "Lapis"`
  or `"Kiku"` (they share field names), choose the card template with `card_kind`
  (`word-and-sentence` · `sentence` · `audio` · `click` · `none` — exactly one `Is…Card` marker), or
  spell out any note type's logical→real map in a `[mine.fields]` sub-table. `saitenka doctor` now
  validates the effective field map against the note type and warns about names it lacks, and unknown
  fields are dropped at mine time so the note still adds instead of failing.
- **Animated (motion) screenshots on mined cards.** `[mine].animated_screenshot = true` captures a short
  animated clip of the scene as the card image instead of a still — the scene reads better in motion and
  pairs with the cue audio. Opt-in (larger media, an extra ffmpeg pass). It prefers WebP and falls back to
  an animated **GIF** where ffmpeg lacks `libwebp` (Homebrew / Windows "essentials" builds), so a clip is
  produced out of the box on every platform (`saitenka doctor` reports which; `animated_format = "gif"`
  forces GIF). `animated_height`/`fps`/`quality`/`max_secs` trade quality against file size, and
  ++ctrl+shift+m++ (`[mine].video_key`) mines the hovered word with a clip for one card without turning it
  on globally.
- **Dictionary-attested compound merging (Yomitan longest-match).** A lexicalized compound that
  UniDic over-splits (`応急処置` → `応急`+`処置`, `満員電車`, `走り出した` mined as `走り`) now merges into one
  token whenever the joined span — the tail deinflected to its dictionary form — is an exact headword in
  your loaded dictionaries. The whole word becomes a single hover / hit-test / colour / mine unit, so it
  is looked up and mined the way Yomitan shows it instead of as fragments. Runs after the existing
  conjugation-tail merge, never crosses a particle/auxiliary boundary, and is a no-op until dictionaries
  are loaded.

### Changed

- **Mined cards are now marked `IsWordAndSentenceCard` by default, not `IsSentenceCard`.** This matches
  the Lapis/Kiku default (and SubMiner's), showing the word prominently on the card front. Set
  `[mine].card_kind = "sentence"` to restore the previous unconditional `IsSentenceCard` marker.

## [1.3.0] - 2026-08-04

### Added

- **Faster tooltips — warm hovers and scrolls now stay under one frame (~16 ms), most under 8 ms.**
  Two idle/memory levers, driven by real diagnostics-report telemetry:
  - **Idle pre-compose.** The background prefetch worker that already builds an upcoming word's tooltip
    head now also composites its first viewport in idle, so the actual hover is a buffer copy + upload
    with zero synchronous rasterisation — the bulk of a warm hover's cost (BGRA convert + overscan
    raster + assemble) moves off the hot path into otherwise-idle CPU. Measured warm-hover p50 fell
    ~24 ms → ~6 ms on the trace replay.
  - **Uncompressed render bands, with a per-panel ceiling.** A tooltip keeps its render bands
    uncompressed (skipping the one-time decompress on the first scroll-reach of a band — ~9 → ~4 ms off
    the cold-band frame tail) until the panel's estimated size exceeds the new
    `[tooltip] raw_band_ceiling_mb` (default 100), when its bands compress so one pathological entry
    can't blow the retained-pixel budget. `0` restores the previous always-compress behaviour.
- **Optional [taffy](https://github.com/DioxusLabs/taffy) layout engine behind a `LayoutBackend` seam
  (`saitenka[layout-engine]`, `[tooltip] layout_engine = "taffy"`).** The tooltip's row-stack geometry
  can be computed by the mature taffy CSS-flexbox solver (packaged as the in-repo `taffylite` Rust/PyO3
  extension) instead of hand-rolled arithmetic. It is **opt-in and parity-gated** — byte-identical to
  the always-available pure-Python default (a differential test proves it across random inputs, vendored
  fixtures, and a real panel's full scroll) — chosen for a maintained engine's robustness, not speed.
  Kept out of `full` because free-threaded `cp314t` wheels are still niche; the default install stays
  pure-Python.
- **Correct Japanese line-breaking (UAX-14 + kinsoku) in tooltips.** A pure line-break module keeps a
  closing bracket, small kana, or punctuation from starting or ending a wrapped line, so long glosses
  wrap the way Japanese text should.
- **Opt out of the post-mine card-preview panel** — `saitenka --no-mine-preview` or `[mine] preview =
  false` skips the auto-preview after mining a card.
- **Tooltip performance observability.** New OpenTelemetry spans attribute where a hover/scroll spends
  its time (compose, measure, mining lookup, pause-on-hover IPC), plus cache hit/eviction and
  pre-compose counters, all surfaced in the diagnostics report — so a slow hover can be explained rather
  than guessed.

### Changed

- **Lower-latency glyph and layout rendering.** Rendered glyph masks are cached behind a split text
  drawer, per-band pixels convert to BGRA once instead of per frame, and a tooltip's offset table is
  computed once per measure rather than on every read — cutting the residual per-frame cost that
  banding left behind.

### Fixed

- **Furigana'd cross-references are clickable again** — a reference decorated with reading annotations
  now hit-tests correctly.
- **Whole-track episode analysis is gated on the subtitle index**, not a Japanese track id, so it runs
  in more setups.

### Development

- **External conformance corpora as test oracles** — the tooltip layout, de-inflection, subtitle-index
  parser, and FSRS retrievability are now asserted against upstream projects' own test vectors (taffy,
  Yomitan, SubMiner, py-fsrs), catching drift a hand-written test would miss.
- **Trace-replay benchmark knobs** — the responsiveness bench can replay a real diagnostics report's
  event cadence and sweep the prefetch worker count and the raw-band ceiling, splitting the warm-hover
  report by whether the idle pre-compose reached it.

## [1.2.1] - 2026-08-03

### Fixed

- **Release pipeline no longer references the removed `poe bundle` task.** The tag-triggered publish
  workflow failed before uploading anything, so 1.2.0 was tagged but never reached PyPI. 1.2.1 is the
  first build published under the corrected, notes-only GitHub Release flow (install is via PyPI) — no
  code changes from 1.2.0.

## [1.2.0] - 2026-08-03

### Added

- **Optional loudness normalization for mined audio (`[mine].normalize_audio`).** Off by default; when
  on, the mined clip runs an EBU R128 `loudnorm` pass (−23 LUFS) so cards captured from quiet and loud
  lines play back at an even volume instead of lurching between them. It adds one ffmpeg pass per mine,
  hence opt-in — set it in `[mine]` or pass `--mine-normalize-audio` for a single run.
- **Pick which entry to mine (Yomitan-style stacked entries).** A word with several readings (退く =
  のく / しりぞく) now shows each entry as its own block with its own reading and its own ⊕, so you mine
  the exact reading and gloss you mean — not one fused card. Each block's ⊕ flips to ✓ once the word is
  in the deck, and the reading names label each stacked block.
- **Blended rareness pill in the tooltip.** A single `diff` chip leads the frequency row with the
  harmonic-mean rank across every loaded **rank-based** frequency dictionary — one summary of the 7+
  per-dict pills, shown for any word the freq dicts cover (deck membership irrelevant). It is
  color-banded by rareness: green ≤10k (common), amber ≤30k (uncommon), red beyond (rare).
  Occurrence-based dicts are excluded from the blend — their per-corpus dense rank is not comparable
  across lists, so only true ranks are combined (they still show their own pill). The blend matches
  `tools/anki_rank_dicts.py`, so the tooltip and the ranked mining lists agree on how rare a word is.

### Changed

- **Better default reading for the mined card.** When a headword has several readings, the auto-picked
  entry now prefers the one matching the reading actually used in the line (退いた → のく, not しりぞく),
  then the more common reading by frequency — instead of always taking the first dictionary entry.
- **Setup asks for the mining deck first, then defaults the known-words deck to it.** Most users mine
  into a single deck, so that deck is now the natural default for coloring — no need to pick a deck
  twice. The mining note-type default is also intersected with the note types actually installed, so
  setup never proposes a `Lapis` (or any) note type that isn't in your Anki.
- **`doctor` verifies your Anki config against Anki.** It now fails (not just warns) when the mining
  note type doesn't exist — it can't be auto-created — and adds a `known` check that errors when a
  configured `[known]` deck is missing or its chosen field isn't on that deck's note type, instead of
  coloring silently seeing nothing. A missing mining *deck* stays a heads-up (it's created on first mine).

### Removed

- **The sticky per-dictionary tab strip** (`show_dict_tabs` / `--dict-tabs` / `--no-dict-tabs`) and its
  `LEFT` / `RIGHT` tab-nav keys. It was off by default and purely cosmetic; the reading names still label
  each stacked entry, and `UP` / `DOWN` scroll the tooltip. The `[tooltip].banded` toggle and the
  `SAITENKA_BANDED` env override are also gone — the windowed (O(viewport)) renderer is now the single
  tooltip render path for every popup, so there is nothing to switch.

### Fixed

- **Rare kanji outside the basic plane are highlighted again.** Words containing supplementary-plane
  kanji (surrogate-pair ideographs such as 𩸽 ほっけ or 𠮟る) were read as having no kanji, so they lost
  kanji-based highlighting and N+1 eligibility. The tokenizer now recognizes the CJK Extension B–H
  planes, not just the BMP.

## [1.1.0] - 2026-08-01

### Added

- **New documentation site** — a task-first [Material for MkDocs](https://saitenka.readthedocs.io) site
  hosted on Read the Docs, built around the questions new users actually arrive with: **Getting Started**
  (install, quickstart), **Usage** (features, keyboard shortcuts, configuration, and a full **CLI
  reference generated from the app** so it can't drift), **Why Saitenka** (how it compares, performance &
  benchmarks), and **Contributing**. Each fact lives in one canonical place; the README now points here.
- **`saitenka update`** pulls the latest release while keeping your extras (wraps `uv tool upgrade`).
  Because a running tool on Windows cannot replace its own files, it prints the command to run in a fresh
  shell by default; `--now` hands off to a detached updater window that waits for the process to exit
  first. `reinstall` gained the same `--now` handoff and is now scoped to *changing* extras or install source.
- **"Add anyway" in the card preview** — mine a note even when Anki flags it as a duplicate, alongside
  assorted card-preview UX fixes.

### Changed

- **The windowed (banded) tooltip renderer and selective head-prefetch are now on by default** — both
  graduate from experimental. `banded = true` (env `SAITENKA_BANDED=0/1` overrides either way) and
  `head_prefetch_lookahead = 1`. Set them back to `false`/`0` in `[tooltip]`/`[perf]` to opt out.
- **`scan_delay` default raised to `1.0` s** (from `0.25`) — a longer dwell before a nested scan popup opens.

### Fixed

- **`reinstall` no longer crashes with a confusing traceback on Windows.** A self-replacing reinstall
  could not delete the running tool's own files and left the process importing an already-swapped module
  at exit; updates now run from a detached helper after the process exits, and telemetry shutdown no
  longer masks the real exit when its module is unavailable.

### Development

- The pre-push gate (`poe all`) is faster — coverage runs in parallel (`-n auto`) and the redundant
  standalone test run was dropped, so the suite runs twice (coverage + free-threaded) instead of three times.
- `RUNNING.md` was retired: user-facing content moved to the docs site, contributor content to a new
  Development page, with the generated CLI reference + `tests/test_cli.py` as the flag/key contract.

## [1.0.0] - 2026-08-01

### Added

- **FSRS maturity coloring** — an optional copied Anki database distinguishes learning, young,
  mature-known, and forgotten words without opening the live collection. N+1 remains the strongest
  signal, and learning/young colors can be overridden under `[palette]`.
- **Utility overlays can be resized together** with top-level `ui_scale`, covering the shortcut help,
  subtitle/backlog sidebar, and episode-analysis window while preserving the `1.0` default.

### Fixed

- **Windows overlay startup and refresh no longer stall behind named-pipe reads.** IPC now uses
  full-duplex overlapped I/O, invalid or locale-mangled pipe settings are diagnosed, and bare
  `attach` uses mpv.net's default pipe without requiring an escaped TOML path.
- **Subtitle-track cycling no longer loses English to a hidden secondary track.** Saitenka leases the
  translation track only while its translation overlay is visible, follows manual primary-track
  changes with a language/count indicator, and `Alt+o` hands rendering and the OSD back to mpv while
  Saitenka is hidden.
- **Fetched Japanese subtitles now appear promptly and survive later runs.** Jimaku and TsukiHime
  share a provider-neutral cache of finished subtitle files; startup reuses that cache before opening
  mpv, and a late fetch replaces an untouched English fallback without overriding a manual track
  choice.
- **Missing Anki on Windows no longer opens the shell's “cannot find anki” dialog.** Saitenka resolves
  the installed executable directly and quietly leaves mining unavailable when it cannot.
- **The subtitle sidebar now consumes completed episode analysis**, showing N+1/N+2 cue badges and
  clearing stale results before a subtitle-track redraw.
- **`poe affected` works from the repository root**, matching the other delegated development tasks.

### Added (developer tooling — not part of the `poe all` gate)

- **Sharpen loop** — an idle-time, one-module-per-run process that hardens the *existing* test suite
  (fixes bugs in the tests) via mutation auditing + a `poe test-lint` conformance linter, proposing
  through an isolated author→skeptic→judge review (two independent UPHOLDs to ship) and never merging.
  See `.agents/sharpen/GUIDE.md`.
- **`poe affected`** — inner-loop test selector: runs only the tests a change can touch (ruff
  dependency-graph reverse-closure + full-run fallback on blind spots), seconds vs the full `poe test`.
  Not a gate — `poe all` / `poe test-ft` remains the pre-push net.

## [0.9.1] - 2026-07-28

### Fixed

- **`saitenka[deinflect]` / `saitenka[full]` now resolve from PyPI.** The GPL `saitenka-deinflect`
  add-on is published to PyPI alongside `saitenka`, so `uv tool install 'saitenka[full]'` works from the
  index (previously the extra was unsatisfiable — the add-on shipped only inside the release bundle).

## [0.9.0] - 2026-07-28

### Changed

- **Renamed the distribution `saitenka-overlay` → `saitenka`** (and the GPL add-on
  `saitenka-overlay-deinflect` → `saitenka-deinflect`), now that the project is published to PyPI. The
  CLI command is `saitenka`; the default mined-note Anki tag and the jimaku keychain service also moved
  to `saitenka`. The import package stays `overlay`. **Breaking:** reinstall as `saitenka` (e.g.
  `uv tool install 'saitenka[full]'`) and update anything that invoked `saitenka-overlay`. Migrate
  existing mined notes with `uv run tools/anki_retag.py`, and re-run `saitenka set-jimaku-key` once (the
  keychain service moved, so the old key isn't found under the new name).

### Added

- **Continuous integration (GitHub Actions).** `ci.yml` runs the full `poe all` gate on Linux plus a
  Python matrix (3.13 · 3.14 · 3.14t · 3.15 · 3.15t); `e2e.yml` exercises the real per-OS transport on
  Linux/macOS/Windows.
- **Publishing to PyPI.** `saitenka` is installable from PyPI; a version tag triggers `release.yml`,
  which publishes a GitHub Release (the self-contained bundle) and the wheel/sdist to PyPI via OIDC
  **Trusted Publishing** — no stored tokens.

### Fixed

- Test-suite portability the new Linux CI surfaced: macOS-only mpv-discovery / SubMiner-detection tests,
  a cross-FreeType tolerance for golden images, and a free-threading data race in a render-counter test.

### Build

- `libcst` moved to an opt-in `codemod` dependency group, and a lean `test` group added, so the CI test
  matrix (and new-interpreter legs like 3.15t) is not blocked building native dev tools it never runs.

## [0.8.0] - 2026-07-28

### Added

- **`--mpv-arg`** — repeatable raw mpv flag passthrough on `run` (SubMiner's `-a/--args` precedent).
  Wins over our own overridable defaults (`--slang`, `--sub-visibility`, `--osd-level`, `--loop-file`,
  `--start`, ...) since mpv is last-flag-wins, but never over `--input-ipc-server`/`--log-file`/the
  anti-duplicate `--script-opts` marker, which we always append last.
- **Prefetch lookahead now works on embedded subtitle tracks**, not just external/jimaku files. The
  currently-selected mpv track is resolved (`app/embedded_subs.py`): an external/jimaku track reads
  its path straight off `track-list`'s `external-filename`; an embedded track (baked into the video
  container) is extracted once via `ffmpeg -map 0:<ff-index> -c:s srt` and cached alongside jimaku's
  own fetched-sub cache (keyed by video name+size+track, so a rewatch reuses it). Both `run` and
  `attach` now share this one path — `attach` previously never built a lookahead index at all, even
  for external files.
- **Telemetry: three new spans covering seek-to-paint latency** (`otel_metrics.py`): `cue_redraw`
  (wraps `set_subtitle` end-to-end — tokenize/score/render/upload), `subtitle_render` (isolates the PIL
  `render_subtitle` call inside it), and `sub_text_reconcile` (the poll-loop's mpv-driven redraw,
  sibling to the pre-existing `sub_seek` span for the Alt+←/→/↓ instant-nav path). `sub_seek`'s span
  now also covers the render it triggers, so it nests `cue_redraw`→`subtitle_render`/`upload` as
  children sharing one `trace_id` — previously every span got its own random `trace_id`, so a
  `trace.json` export had no causal chain at all connecting a seek to the draw it caused.
- **Telemetry: full span coverage of background dep loading and the per-cue draw path** —
  `dictdb_open`/`anki_ensure_running`/`build_dict_set`/`load_freq_dict`/`load_jlpt_dict`/
  `build_mining`/`warm_tokenizer` (`reader_deps.py`) and `teardown_tip`/`hide_preview`/
  `tokenize_line`/`score_line` (nested inside `cue_redraw`, `controller.py`), plus
  `anki_http_call`/`anki_json_parse`/`anki_known_extract` (`wordlists.py`, per AnkiConnect action).
  Built to pin down exactly where load-deps/first-cue-color latency went; the tokenizer-contention
  fix above was found and verified through this instrumentation, not guesswork.

### Changed

- **Startup console noise reduced.** The `dictionaries:`/`frequency:`/`pitch:` title dump collapsed
  to one line with counts and the settings file path (full titles still land in the structured log).
  Telemetry now prints an explicit startup line when it's actually enabled — pointing at
  `saitenka telemetry disable` instead of telling you to hand-edit `overlay.toml`.
- **Background dep loading (`build_reader_deps`) is now parallelized** across a small thread pool
  instead of one strictly sequential background thread — Anki launch/poll, dict-title resolution, the
  JLPT table load, the frequency-dict load, the known-words fetch, and the mining `Anki()` object all
  fan out respecting their actual dependencies, turning the load's wall time from their *sum* into
  their *max*. **`run` mode now actually uses it** — it previously kept its own separate, sequential
  copy of this exact logic (`cli_run.py`'s `_resolve_dict_set`/`_build_scorer`/`_build_run_deps`),
  so the parallelization above was silently inert for `run` until this copy was deleted in favor of
  delegating to the shared implementation (only the CLI-only bits — the plain `--known word1,word2`
  fallback list, and this command's console feedback lines — stayed as thin wrappers). Coloring/
  tooltips/mining land sooner after playback starts. `build_reader_deps`'s return value is unchanged;
  its signature grew optional `known_words`/`on_anki_unreachable`/`on_known_words_error` params to
  support that delegation.
- **fugashi's first-ever `tokenize()` call (MeCab tagger/dictionary setup) is now pre-warmed on its
  own thread, as early as possible in `run`/`attach`** (before mpv even launches/connects). Measured:
  ~13ms in isolation, but ~600ms (46x) when it happened to run concurrently with the background dep
  thread pool — genuine free-threading contention (not GIL-reactivation, not general system load; both
  ruled out with isolated same-conditions timing), and mutual: it slowed the dep-loading threads down
  by a similar factor while they slowed it down. Warming it on its own thread, overlapping mpv's own
  launch/connect dead time, means the real first subtitle line's `tokenize()` call is already-warm
  and fast, and the dep-loading threads never contend with it in the first place.
- **`KnownWords.from_ankiconnect` no longer chunks `notesInfo` into 500-note batches** — one call per
  deck with every note id, matching SubMiner's own AnkiConnect client (no batching there either;
  AnkiConnect has no documented limit on `notes`). This was measured as the actual dominant cost in
  background dep loading for a real known-words deck (~1.8s of sequential round-trips) — dominating
  regardless of how well the freq/JLPT loads above parallelize alongside it. (An intermediate fix that
  fanned the chunked calls out over threads was reverted in favor of this simpler one call.)
- **A word's dictionary lookup now issues ONE query across every dictionary instead of one per
  (dict, form).** `entry_for` fanned out ~27 SQLite point queries per word decode (3 forms —
  lemma/surface/reading — × 9 dicts), which dominated a telemetry trace at ~90% of all spans. Every
  dictionary lives in the one consolidated DB scoped by `dict_id`, so a single `IN`-list query
  (`_batch_exact`) fetches them all at once and reassembles per-dict **byte-identically** (`ORDER BY
  e.id` pins the row order). Colliding forms are also de-duplicated — for any uninflected word
  `lemma == surface`, so the identical query was re-run ~26% of the time. Per-decode lookup queries
  drop 27→1.

### Fixed

- **`scan_delay` (dwell before a nested/scan popup opens, and the cooldown after scrolling the base
  tooltip) was never read from `overlay.toml`** — stuck at its 0.25s default regardless of config in
  both `run` and `attach` modes. Now wired and documented in `overlay.example.toml`.
- **The `N prefetch worker(s)` runtime line was console-only** (a bare `print`), so it never reached
  `overlay.log` or a `saitenka report` bundle — made diagnosing "0 prefetch workers" reports
  impossible from a bundle alone. Now also logged.
- **A globally-installed `saitenka.lua` plugin (`install-plugin`, for the ATTACH-from-Finder workflow)
  double-attached onto `run` mode's own mpv instance.** `--no-config` suppresses `mpv.conf`/
  `input.conf` but NOT mpv's script autoload, and `saitenka.lua`'s `spawn_overlay()` reuses whatever
  `input-ipc-server` is already set — `run` mode passes its own explicitly, so a plugin installed for
  the attach workflow would spawn a second, redundant `saitenka attach` onto `run`'s socket:
  two independent `Reader`/telemetry instances driving one mpv (doubled IPC traffic/CPU, and the
  actual cause of `telemetry/trace.json` corruption on some sessions — two OS processes writing the
  same file with no cross-process lock). Fixed with a handshake: `run` now launches mpv with
  `--script-opts=saitenka-managed=yes`; `saitenka.lua` checks it and no-ops instead of double-attaching.
  Requires re-running `install-plugin` to pick up the fix if you already have the plugin installed.
- **Alt+←/→/↓ instant-nav could silently lose its `_nav_idx` chaining hint on every press**, degrading
  next/next/next to per-press text matching instead of index-based chaining. Right after `sub_nav()`
  renders the target cue and sets `_nav_idx`, the caller also fires mpv's own native `sub-seek` behind
  it to catch the video up; that native seek transiently re-reports the PRE-nav cue's text before
  landing on the real target. The settle-guard only swallowed an *empty* mid-seek blip, so this
  non-empty "revert" value was adopted, silently resetting `_nav_idx` back to -1 via `set_subtitle`
  even though the render was already correct. `sub_nav()` now records the pre-nav text and the
  settle-guard swallows either transient value, while still adopting any genuinely different mpv
  correction.
- **mpv crashing natively (e.g. a GPU-driver SIGSEGV) was indistinguishable from a clean quit** in
  `overlay.log`/report bundles — both just look like `mpv IPC reader: EOF ... mpv closed the pipe`.
  `run` mode's shutdown now checks `proc.returncode` and logs a warning naming the signal (or nonzero
  status) when mpv didn't exit cleanly.
- **Engaged prefetch (paused, or the cursor resting over the video) pre-rendered the *whole* panel for
  every content word on the line** — so a single pathological monolingual entry cost up to ~2.8s on a
  background worker and backed the prefetch queue up under real use. A hover defers the entry's tail
  via the finish queue regardless, so the speculative full render was wasted work: engaged prefetch
  now renders only the viewport-first head, exactly as a hover's own first paint does.

### Development

- **Head-prefetch renders are now traced** (`prefetch_decode` `kind="head_ahead"`), distinct from the
  engaged current-line `kind="head"`; they previously folded into anonymous `render` spans, invisible
  in a trace.
- **`poe timeline-bench-banded`** reproduces the shipped config (`head_prefetch_lookahead=1`,
  `prefetch_lookahead=2`, banded render), and the timeline bench now engages on hover cues so it
  exercises the engaged-render path a real session hits; `poe timeline-bench` runs under `PYTHON_GIL=0`
  so the prefetch workers are measured truly free-threaded, not on the GIL-reactivated fallback.
- **A non-hermetic telemetry test** read the real user cache dir for `trace_exists` and failed whenever
  a real session had already written a trace; it now isolates `SAITENKA_CACHE_DIR`.

## [0.7.0] - 2026-07-27

### Added

- **`--mpv-arg`** — repeatable raw mpv flag passthrough on `run` (SubMiner's `-a/--args` precedent).
  Wins over our own overridable defaults (`--slang`, `--sub-visibility`, `--osd-level`, `--loop-file`,
  `--start`, ...) since mpv is last-flag-wins, but never over `--input-ipc-server`/`--log-file`/the
  anti-duplicate `--script-opts` marker, which we always append last.
- **Prefetch lookahead now works on embedded subtitle tracks**, not just external/jimaku files. The
  currently-selected mpv track is resolved (`app/embedded_subs.py`): an external/jimaku track reads
  its path straight off `track-list`'s `external-filename`; an embedded track (baked into the video
  container) is extracted once via `ffmpeg -map 0:<ff-index> -c:s srt` and cached alongside jimaku's
  own fetched-sub cache (keyed by video name+size+track, so a rewatch reuses it). Both `run` and
  `attach` now share this one path — `attach` previously never built a lookahead index at all, even
  for external files.
- **Telemetry: three new spans covering seek-to-paint latency** (`otel_metrics.py`): `cue_redraw`
  (wraps `set_subtitle` end-to-end — tokenize/score/render/upload), `subtitle_render` (isolates the PIL
  `render_subtitle` call inside it), and `sub_text_reconcile` (the poll-loop's mpv-driven redraw,
  sibling to the pre-existing `sub_seek` span for the Alt+←/→/↓ instant-nav path). `sub_seek`'s span
  now also covers the render it triggers, so it nests `cue_redraw`→`subtitle_render`/`upload` as
  children sharing one `trace_id` — previously every span got its own random `trace_id`, so a
  `trace.json` export had no causal chain at all connecting a seek to the draw it caused.
- **Telemetry: full span coverage of background dep loading and the per-cue draw path** —
  `dictdb_open`/`anki_ensure_running`/`build_dict_set`/`load_freq_dict`/`load_jlpt_dict`/
  `build_mining`/`warm_tokenizer` (`reader_deps.py`) and `teardown_tip`/`hide_preview`/
  `tokenize_line`/`score_line` (nested inside `cue_redraw`, `controller.py`), plus
  `anki_http_call`/`anki_json_parse`/`anki_known_extract` (`wordlists.py`, per AnkiConnect action).
  Built to pin down exactly where load-deps/first-cue-color latency went; the tokenizer-contention
  fix above was found and verified through this instrumentation, not guesswork.

### Changed

- **Startup console noise reduced.** The `dictionaries:`/`frequency:`/`pitch:` title dump collapsed
  to one line with counts and the settings file path (full titles still land in the structured log).
  Telemetry now prints an explicit startup line when it's actually enabled — pointing at
  `saitenka telemetry disable` instead of telling you to hand-edit `overlay.toml`.
- **Background dep loading (`build_reader_deps`) is now parallelized** across a small thread pool
  instead of one strictly sequential background thread — Anki launch/poll, dict-title resolution, the
  JLPT table load, the frequency-dict load, the known-words fetch, and the mining `Anki()` object all
  fan out respecting their actual dependencies, turning the load's wall time from their *sum* into
  their *max*. **`run` mode now actually uses it** — it previously kept its own separate, sequential
  copy of this exact logic (`cli_run.py`'s `_resolve_dict_set`/`_build_scorer`/`_build_run_deps`),
  so the parallelization above was silently inert for `run` until this copy was deleted in favor of
  delegating to the shared implementation (only the CLI-only bits — the plain `--known word1,word2`
  fallback list, and this command's console feedback lines — stayed as thin wrappers). Coloring/
  tooltips/mining land sooner after playback starts. `build_reader_deps`'s return value is unchanged;
  its signature grew optional `known_words`/`on_anki_unreachable`/`on_known_words_error` params to
  support that delegation.
- **fugashi's first-ever `tokenize()` call (MeCab tagger/dictionary setup) is now pre-warmed on its
  own thread, as early as possible in `run`/`attach`** (before mpv even launches/connects). Measured:
  ~13ms in isolation, but ~600ms (46x) when it happened to run concurrently with the background dep
  thread pool — genuine free-threading contention (not GIL-reactivation, not general system load; both
  ruled out with isolated same-conditions timing), and mutual: it slowed the dep-loading threads down
  by a similar factor while they slowed it down. Warming it on its own thread, overlapping mpv's own
  launch/connect dead time, means the real first subtitle line's `tokenize()` call is already-warm
  and fast, and the dep-loading threads never contend with it in the first place.
- **`KnownWords.from_ankiconnect` no longer chunks `notesInfo` into 500-note batches** — one call per
  deck with every note id, matching SubMiner's own AnkiConnect client (no batching there either;
  AnkiConnect has no documented limit on `notes`). This was measured as the actual dominant cost in
  background dep loading for a real known-words deck (~1.8s of sequential round-trips) — dominating
  regardless of how well the freq/JLPT loads above parallelize alongside it. (An intermediate fix that
  fanned the chunked calls out over threads was reverted in favor of this simpler one call.)

### Fixed

- **`scan_delay` (dwell before a nested/scan popup opens, and the cooldown after scrolling the base
  tooltip) was never read from `overlay.toml`** — stuck at its 0.25s default regardless of config in
  both `run` and `attach` modes. Now wired and documented in `overlay.example.toml`.
- **The `N prefetch worker(s)` runtime line was console-only** (a bare `print`), so it never reached
  `overlay.log` or a `saitenka report` bundle — made diagnosing "0 prefetch workers" reports
  impossible from a bundle alone. Now also logged.
- **A globally-installed `saitenka.lua` plugin (`install-plugin`, for the ATTACH-from-Finder workflow)
  double-attached onto `run` mode's own mpv instance.** `--no-config` suppresses `mpv.conf`/
  `input.conf` but NOT mpv's script autoload, and `saitenka.lua`'s `spawn_overlay()` reuses whatever
  `input-ipc-server` is already set — `run` mode passes its own explicitly, so a plugin installed for
  the attach workflow would spawn a second, redundant `saitenka attach` onto `run`'s socket:
  two independent `Reader`/telemetry instances driving one mpv (doubled IPC traffic/CPU, and the
  actual cause of `telemetry/trace.json` corruption on some sessions — two OS processes writing the
  same file with no cross-process lock). Fixed with a handshake: `run` now launches mpv with
  `--script-opts=saitenka-managed=yes`; `saitenka.lua` checks it and no-ops instead of double-attaching.
  Requires re-running `install-plugin` to pick up the fix if you already have the plugin installed.
- **Alt+←/→/↓ instant-nav could silently lose its `_nav_idx` chaining hint on every press**, degrading
  next/next/next to per-press text matching instead of index-based chaining. Right after `sub_nav()`
  renders the target cue and sets `_nav_idx`, the caller also fires mpv's own native `sub-seek` behind
  it to catch the video up; that native seek transiently re-reports the PRE-nav cue's text before
  landing on the real target. The settle-guard only swallowed an *empty* mid-seek blip, so this
  non-empty "revert" value was adopted, silently resetting `_nav_idx` back to -1 via `set_subtitle`
  even though the render was already correct. `sub_nav()` now records the pre-nav text and the
  settle-guard swallows either transient value, while still adopting any genuinely different mpv
  correction.
- **mpv crashing natively (e.g. a GPU-driver SIGSEGV) was indistinguishable from a clean quit** in
  `overlay.log`/report bundles — both just look like `mpv IPC reader: EOF ... mpv closed the pipe`.
  `run` mode's shutdown now checks `proc.returncode` and logs a warning naming the signal (or nonzero
  status) when mpv didn't exit cleanly.

## [0.6.0] - 2026-07-26

### Added

- **`saitenka telemetry enable|disable|status`** — flip `[telemetry] enabled` without
  hand-editing `overlay.toml` (comment-preserving, backs up the prior file), plus a `status` readout
  of both switches (config flag + whether the `telemetry` extra is installed), the export dir, and
  the last trace. `enable` prints the install command if the extra is missing.
- **Windowed (banded) tooltip render engine**, behind `[tooltip].banded` / `SAITENKA_BANDED=1` (off by
  default): composites only the blocks in the viewport (± overscan) and hit-tests from retained
  per-block geometry, instead of slicing a whole-panel bitmap. Byte-for-byte identical to the
  existing renderer at every scroll offset; the blob path is untouched when off.
- **Prefetch lookahead** (`prefetch_lookahead` config knob, off by default) — warms the next N
  subtitle cues' dictionary glossaries during idle playback (needs an external sub index; a no-op on
  embedded/jimaku tracks), plus a cheap dict-only warm pass while just playing (not paused/hovering)
  so the JSON-decode cost is usually already paid by the first hover.
- **Cache-size and RSS telemetry gauges** — panel-cache size/bytes, decoded dictionary-entry count,
  and process RSS, sampled on the telemetry writer thread's 1s cadence.
- **`doctor` reports app version, Windows edition/build, PowerShell version, and an `mpv_socket`
  hint** — closes the diagnostic gap around attaching to your own already-running mpv. The report
  bundle's manifest header now carries the version too.
- **`reinstall` preserves installed extras** (a bare reinstall previously replaced the extras set,
  silently dropping `deinflect`/`telemetry`); tries PyPI then the latest GitHub release tag
  (overridable). **`uninstall`** removes config/dicts/cache/crash logs and the mpv plugin, but never
  touches mpv/ffmpeg (`--keep-dicts`, `--yes`).
- **Render-executor policy + parallelism benchmarks** — free-threaded builds render tooltip panels
  across threads (FreeType releases the GIL; ~78% of the render tail is glyph rasterization), falling
  back to a process pool on a GIL build; `examples/bench_responsiveness.py --vocab` and
  `bench_parallelism.py` benchmark the policy against a frozen real-episode word list. Sub-interpreters
  were evaluated and rejected — PIL's C extension segfaults across them.
- **`overlay.example.toml` rewritten sshd-style** — every flag documented at its default, the common
  ones active, the rest commented, so it stays legible as the config surface grows.

### Changed

- **Renamed the optional `observability` extra to `telemetry`** for consistency with the
  `[telemetry]` config table and the new command — install as `saitenka[telemetry]`
  (the old `[observability]` name no longer resolves; `[full]` is unaffected).
- **Collapsed the trace write-pipeline** into a single `CTFSpanProcessor` (one bounded queue, one
  writer thread) from three classes / two queues / three threads. Fixes an O(n²) full-file rewrite
  per flush, a `force_flush` drain/write race, and an unbounded second queue; the CTF output and the
  public telemetry behaviour are unchanged.
- **Split `controller.py`'s remaining subsystems into their own modules** — background prefetch,
  card-preview UI, the nested (scan) popup, the base tooltip (hover hysteresis, panel cache, tabs),
  translation reveal, and subtitle navigation each moved to their own `app/*.py` file behind thin
  delegating methods on `Reader`, with the progressive dep-loading glue folded into the existing
  `reader_deps.py`. `controller.py`: 2028 → ~1030 lines. No behavior change.
- **Complexity-reduction pass** on `Reader.poll_once`, `DictionarySet.entry_for`, and
  `_windows_registry_mpv`, each split into smaller, independently-testable helpers to stay under the
  `poe complexity` gate.
- Moved `Theme` and `overlay_version` to the core `overlay.model`/`overlay.version` layer to break
  two import cycles (`render↔panel`, `report↔doctor`) uncovered while adding the banded renderer.

### Fixed

- **Off-PATH mpv on Windows** — `find_mpv` gains a registry probe (App Paths + the default video-file
  handler); `setup` now survives a package-manager install failure (e.g. winget's non-zero exit)
  instead of crashing the whole wizard, and falls back to an interactive prompt that persists
  `mpv_path`.
- **The paused-overlay "only updates on mouse move" bug on Windows** — mpv throttles OSD updates while
  paused (mpv #8172); a draw landing mid-pause now schedules a one-tick-later re-flush so mpv actually
  presents it. `--d3d11-flip=no` alone was insufficient (the throttle isn't flip-model-specific).
- **A reading collision (き → 気/木/生/期/器…) dumped every unrelated homophone into one tooltip** —
  `entry_for` now groups on the term like Yomitan, keeping only exact-term hits when any exist (a
  kana word whose forms are all kanji still keeps every reading match, the intended polysemy).
- **A per-dictionary telemetry histogram (`dict_sql_duration_ms`) undercounted ~9-11x** — OTel fans a
  labeled instrument into one data point per label; the summarizer read only the last point instead
  of summing across all of them.

## [0.5.0] - 2026-07-25

### Added

- **Dependency-contract engine (`uv run poe arch`)** replaces the single-rule regex layering test:
  no import cycles among `overlay`'s top-level packages, the `sc`/`model.py` core stays PIL-agnostic,
  and the optional GPL-3.0 `saitenka_deinflect` add-on is only importable through its
  `app/dictionary.py` + `app/doctor.py` chokepoint (`ruff` `TID` bans it elsewhere as
  defense-in-depth). Folded into `poe all`. A non-gating `poe arch-report` (`pyscn`) ranks coupling
  hotspots to guide the `controller.py` split.
- **Cognitive-complexity gate (`uv run poe complexity`)** — `complexipy`, ratcheted against a
  checked-in baseline (`overlay/complexipy-snapshot.json`) so today's pre-existing high-complexity
  functions are grandfathered and only new complexity growth fails the gate. Folded into `poe all`;
  regenerate the baseline with `poe complexity-baseline` after a deliberate refactor.
- **Structured logging + opt-in OpenTelemetry tracing/metrics.** `structlog` JSON-lines logging
  (always on, redacted, free-threading-safe `msgspec` serialization) plus an opt-in OTel stack: a
  gated, bounded-queue span pipeline exporting Chrome Trace Format, pull-based metric instruments,
  and CTF counter tracks — spans and metrics land in one Perfetto-viewable `trace.json`, no
  Prometheus/backend required. `doctor`/`report` surface the trace file; `$OTEL_SDK_DISABLED=true`
  force-disables it even when configured on. Fully no-op (memoized) when the `[observability]` extra
  isn't installed.
- **Call-level invariant gate (`poe invariants`, ast-grep)** — catches anti-patterns below
  import-linter's module graph: no `time.sleep` in the mpv reader thread, single-writer IPC pipe, no
  model-derived readings. Blocking, in `poe all`. Ships planted +/− rule tests so a rule can't
  silently match nothing.
- **Dataflow taint tier (`poe invariants-taint`, opt-in, semgrep)** — the one check ast-grep can't
  do: model output reaching a reading field *through* intermediate variables.
- **Mutation auditing (`poe mutate`, opt-in, cosmic-ray)** — reruns the test suite against small
  code mutations to measure how much the tests actually catch, not just cover. `sub_index.py`'s
  score went 57% → 66% after 4 new Hypothesis properties (59 mutants killed).
- **Supply-chain & hygiene gate tier** — `poe all` gained vulnerability scanning (`uv audit`),
  unused/missing dependency checks (`deptry`), a license boundary gate (only the project's own
  GPL-3.0 `deinflect` add-on may be copyleft), spell-check, offline link-check, and shellcheck over
  the installers. Adequacy beyond the unit suite is now three-pronged and opt-in: mutation auditing
  above, coverage-guided fuzzing (`poe fuzz`, atheris, over the subtitle parser), and symbolic
  execution (`poe crosshair`, CrossHair/z3) — each catches a different class of bug.

### Changed

- Complexity-reduction sweep across several high-cognitive-complexity functions flagged by the new
  `complexity` gate: `cli.py::run` (CCN 147, split into `cli_run.py`), `render/flow.py::render_flow`,
  `SubIndex::locate`, `fsrs.py::_read`, `Miner::bulk_mine`, and `subtitles.py::render_subtitle` —
  each split into smaller, independently-testable helpers with no behavior change.

### Fixed

- **Episode detection for `SxxExx`-style filenames** — `parse_filename` now recognizes
  `Show.S02E01.…`, `S2E03`, and `1x08` (yielding the episode) in addition to a bare trailing number,
  and an underscore-delimited `Show_ep05_…` parses correctly. A resolution like `1920x1080` is not
  mistaken for a season×episode. This is what jimaku uses to pick the right subtitle file.
- **`overlay.mpvio` importing from `overlay.app`** (a real import cycle) — `otel_metrics.py`, a leaf
  instrumentation module with no `app/` dependencies, had been placed under `app/` by accident; moved
  to `overlay/otel_metrics.py`.
- **Telemetry span gate defaulted off** with nothing to flip it on, so telemetry produced logs but
  never a trace file.
- **A bare OpenTelemetry import crashed background dependency loading** on any install without the
  `observability` extra (the default) — now a no-op when the package isn't importable.
- **Chrome Trace Format thread IDs were derived from the random trace ID**, not the real thread —
  every independently-started span landed on its own synthetic row in Perfetto instead of grouping
  by the thread that ran it.

### Development

- Persisted a working local-MLX setup (`repowise-mlx-*`/`repowise-doc-*` poe tasks) for repowise's
  optional LLM doc generation, after finding model size alone doesn't fix its hallucination-prone
  synthesis pages — a prompt-grounding gap in the tool, not a capability ceiling
  (`vibe/repowise-local-mlx-investigation.md`, local notes).
- Added `ARCHITECTURE.md` (module map + data flow) and fixed stale, duplicated task references in
  README and RUNNING.md — both now point at the `dev-gate` skill as the single source of truth.

## [0.4.0] - 2026-07-23

### Added

- **`saitenka import <dir>`** — build your Yomitan dictionaries into the consolidated database in
  one step and register them in the config by title. Accepts `.zip` files and/or folders; source zips are
  read **in place** (no copy kept), so you can delete them afterwards.
- **The tooltip and card preview scale with the window** (mpv's OSD model) — their contents (fonts,
  chips, pitch graphs, icons, padding, width) are now defined on a reference-height canvas and multiplied
  by `window_height / 1080`, so a small video shows the **same amount of content**, just smaller, and a
  big screen shows it larger — crisp at both. Previously the container tracked the window but the content
  was a fixed pixel size, so small windows cramped/clipped it. A 1080p window is unchanged.
- **`doctor` reports live session health** — real latency percentiles and current RSS from the actual
  running session (previously only available from the offline `--stress` benchmark), plus the
  interpreter's Python version and GIL/free-threaded build state (useful since the free-threading advice
  differs between a `3.14` and a `3.14t` build and a user can swap installs).
- **Pause-on-tooltip is on by default** (the mining default), and the per-dictionary tab strip is off by
  default.

### Changed

- **Dictionaries are now imported once into a single database**, the Yomitan way. Every dictionary —
  definition, frequency, pitch, and the bundled JLPT levels — lives in one
  `~/.local/share/saitenka/dictionaries.sqlite`, built only at `import` time. `dicts`/`freq`/`pitch` in
  the config now hold dictionary **titles** (resolved against that DB), not file paths, and `run`/`attach`
  only ever **open** the database — nothing is parsed or rebuilt at play time. Previously the definition
  dicts were cached per-zip and the frequency/pitch/JLPT lists were re-parsed from their zips on **every**
  launch (~3 s of startup work with a full set); that startup cost is gone. `doctor` now lists what's
  imported and flags any configured title that hasn't been, and warns (informationally) about the old
  per-zip caches and copied zips, which are now unused and safe to delete.
- **Default tooltip height is now 0.4** (was 0.5) of the video height — a smaller default that covers
  less of the frame. Override per-config with `tip_height` or per-run with `--tip-height`.
- **Deinflection chain reads as chips** — each Yomitan transform (e.g. `causative › potential or passive
  › negative › -た`) is now a green pill after a plain green dot marker, instead of a hard-to-read
  puzzle-piece icon + coloured text. `doctor` gained a **deinflect** check (warns, with how to enable the
  optional GPL add-on, when it's missing so no chain shows).
- **Own frequency lists show a short pill name** — `Saitenka Known` etc. now renders as `Known`, freeing
  up pill width; other dictionaries pass through unchanged.
- **Cold dictionary lookups are dramatically faster** (p99 ~1012ms → ~214ms, max ~1073ms → ~299ms on the
  `--stress` repro, a ~72-79% reduction) — a profile found lookup/JSON overhead, not rendering, was the
  real cost behind the ROADMAP's "cold first-paint jank": a dedup key was needlessly re-serializing an
  already-decoded glossary to JSON, and a pitch-lookup query (`term=? OR reading=?`) had no usable index
  for its `reading` branch and fell back to a full table scan. Both are fixed (plus a bounded LRU cache of
  decoded dictionary entries), and the hot JSON decode path moved from stdlib `json` to `msgspec.json`.
- **Tooltip panels are cached compressed** (zlib-compressed BGRA, ~44x smaller per entry) instead of as
  raw image arrays, decompressed only when a panel becomes active; the panel cache cap rose 48 → 128.
- **SQLite's per-connection mmap window shrunk 1 GiB → 256 MiB** (page cache 64 → 32 MiB) — the mmap view
  counts toward the process working set on Windows, inflating RAM by gigabytes across the per-thread
  connections; a benchmark showed the mmap win over `pread` was mostly a page-cache artifact, so this
  costs next to nothing.

### Removed

- **`copy-dicts`** — the command that relocated dictionary zips out of macOS TCC-protected folders is
  gone. Runtime no longer reads the zips at all (only the database), so a plugin-mode mpv never triggers a
  Documents/Downloads consent prompt; import the dicts once with `import` and the zips can live anywhere.

### Fixed

- **`doctor` no longer false-warns about the jimaku key** when it's stored in the Keychain *and* also
  present in `$JIMAKU_API_KEY` — it now checks the Keychain directly (what plugin-mode mpv reads) rather
  than trusting the env-shadowed source.
- **Furigana'd kanji in a definition are now scannable** — a kanji rendered with a reading (a ruby box)
  used to be skipped by the hover-scan pass, so you couldn't open its nested popup. Its base kanji now
  emit hitboxes (and keep the run contiguous, so a word spanning ruby + okurigana still scans whole).
- **Nested popup tracks the scanned word again** — on a tall/HiDPI window the wider tooltip made every
  nested popup snap to the same screen-right position; placement now flips to open leftward when it would
  overflow, so it follows the word.
- **Contrasting frame around popups** — a tooltip (and an overlapping nested popup) now has a border, so
  the nested one reads as its own panel instead of a continuation of the base.

- **Tooltip no longer strands itself on fullscreen or window resize** — toggling fullscreen (`f`) or
  resizing the window moves every on-screen coordinate, which used to leave an open dictionary tooltip
  floating, detached and mis-sized, in the corner. The tooltip (and any nested scan popup) is now
  dismissed on a resize and reopens correctly placed on the next hover, once the size settles.
- **Windows: paused overlays repaint again** — Windows' default d3d11 GPU context uses flip-model
  presentation, which doesn't re-present the window while paused, so a new/updated subtitle or tooltip
  only became visible on the next real window event ("the subtitle doesn't update until I move the
  mouse"). mpv now launches with `--d3d11-flip=no`; a no-op on other contexts.
- **No more JLPT pill on function words** — a particle whose bare-kana reading collided with an N1 word
  (e.g. は, ね) was mislabelled N1; the check now gates on content part-of-speech, like the underline
  already does.
- **Tooltip keys release cleanly** — releasing a tooltip's key bindings now sends mpv's `ignore` command
  instead of an empty one it rejects (which was spamming `Invalid command for key binding` and leaving
  the arrow keys grabbed since the unbind never took).
- **Ctrl+C exits cleanly** — the CLI now exits `130` instead of dumping a `KeyboardInterrupt` traceback
  from the free-threaded re-exec.

### Development

- **Bounded the per-thread font cache** (same LRU pattern as the panel/entry caches) — sizes aren't drawn
  from a small fixed set (ruby text scales to its base, structured-content nodes carry their own sizes),
  so a long session touching varied dict content could grow it unbounded. Added a memray-based memory
  regression test (`tests/test_stress_memory.py`, `poe stress-memory`, `slow`-marked).

## [0.3.0] - 2026-07-22

Tooltip and scan-popup refinements, plus a large cross-platform test and IPC-refactor pass following the
Windows end-to-end lessons: the mpv IPC layer now sits behind a small transport port with one contract
suite that runs on every OS, so portability is provable and the past Windows regressions are pinned.

### Added

- **Configurable dictionary tabs** — `show_dict_tabs` toggles the per-dictionary tab strip in tooltips.

### Changed

- **Compact nested scan popups and a smaller base tooltip** — the base tooltip scale is decoupled from
  nested popups, and the dictionary-tab strip now renders inside nested scan popups too.

### Development

- **mpv IPC behind a `Transport` port** (Unix socket / Windows named pipe / an in-memory fake) with a
  single cross-platform contract suite, and a pure, tested `build_mpv_argv` for the mpv launch command —
  no user-visible change, but the two historical Windows bugs (the inert named pipe; the run-vs-attach
  divergence) are now named regression cases.
- **Cross-platform test harness runnable entirely on macOS** — a `use_platform()` fixture that drives
  real Windows path resolution off-Windows (`platformdirs` `WIN_PD_OVERRIDE_*`), test-tier markers
  (`windows_sim`/`slow`/`integration`/…) under `--strict-markers`, a fake-mpv launch smoke, and repo-wide
  LF enforcement (`.gitattributes`/`.editorconfig`). Automated Windows/macOS/Linux CI is deferred (see
  `ROADMAP.md`); the local gate remains `uv run poe all`.

## [0.2.0] - 2026-07-22

Cross-platform support (especially Windows), a streaming dictionary importer, diagnostics, a broad
hardening pass, instant/progressive subtitle UX, and dictionary-classification fixes.

### Added

- **Instant subtitle navigation.** `Alt+←/→/↓` now draw the previous/next/replayed line in the overlay
  immediately from a parsed cue index, then let mpv's seek catch the picture up behind it — the text no
  longer waits on the video seek. Applies to external subtitle files (`--sub-file` / jimaku).
- **Progressive `run` startup.** `run` draws plain subtitles the instant mpv is up and loads
  dictionaries / coloring / mining in the background (with the loading spinner), like `attach` —
  instead of blocking the window on the first-run dictionary cache build.
- **Windows support, end-to-end.** The overlay now installs, sets up, and runs on Windows without
  hand-patching: mpv IPC over a Windows **named pipe**, plugin install into `%APPDATA%\mpv\scripts`
  (and mpv.net's), and a runtime that copes with a GUI-launched mpv's minimal `PATH`.
- **`import-dictionaries`** — stream a Yomitan **database export** (the multi-GB dexie JSON backup)
  into standard per-dictionary `.zip`s the overlay already loads, with a progress bar and constant
  memory (never a full load). Complements importing plain dictionary `.zip`s.
- **`report`** — bundle diagnostics (versions, `doctor`, config, `mpv.conf`, the plugin Lua, recent
  logs, crash reports) into one timestamped, **redacted, local-only** zip for bug reports. `--no-log`
  opts out of the log.
- **Automatic crash capture** — `sys.excepthook` + `threading.excepthook` + `faulthandler` write
  redacted, local-only crash reports (never uploaded); `doctor` surfaces them.
- **`--jimaku-force` / `[jimaku].force`** — prefer jimaku.cc subtitles over a mistimed/wrong embedded
  track, falling back to the embedded track on fetch failure.
- **`[anki]` config** — configurable AnkiConnect endpoint (`url`, or `host`/`port`) and `api_key`, for
  users who changed AnkiConnect's `webBindPort`/`webBindAddress`/`apiKey`.
- **Cross-platform secret storage** via `keyring` (macOS Keychain / Windows Credential Locker / Linux
  Secret Service), with a config-file fallback where no backend exists.
- **`$SAITENKA_MPV_PATH`** and expanded mpv discovery (mpv.net, off-`PATH` installs), plus
  ffmpeg/ffprobe discovery so mining works from a GUI-launched (plugin-mode) mpv.
- **`--version`** now reports the real version.
- **Graceful shutdown** on POSIX `SIGTERM` and Windows `SIGBREAK` — the same cleanup as Ctrl+C (quit
  mpv, close the socket, remove temp dirs) instead of a hard exit.
- **Progressive startup** (attach/plugin mode) — plain subtitles draw immediately with a top-left
  loading spinner, then FSRS coloring, tooltips, and mining light up in place once dictionaries finish
  loading in the background. Dictionaries are now **optional** (like Anki): with none configured,
  attach is a working subtitle renderer with jamdict-fallback tooltips.

### Changed

- **IPC transport** rewritten to a background reader thread with a single-flight reply channel —
  uniform across Unix sockets and Windows named pipes (replacing a poll that no-op'd on the pipe).
- **Config / data / cache directories** are now platform-native via `platformdirs` (with a legacy
  `~/.config` fallback so existing installs don't move), and mpv/mpv.net directories mirror mpv's own
  resolution (`$MPV_HOME` > portable_config > `%APPDATA%\mpv` / `~/.config/mpv`). Every path is
  user-overridable and `~`/env-expanded.
- **Config and plugin writes are atomic** (temp file → `fsync` → `os.replace`) and LF-only, so a
  crash can't leave a truncated config, and the mpv Lua stays LF on Windows.
- **Dictionary loading fails soft** — a config entry that's a bare Yomitan title (not a file) is
  skipped with an actionable warning instead of crashing the overlay.
- **`doctor` and `setup` are hardened** — `doctor` validates the config end-to-end (flagging
  bare-title dict entries), `setup` runs a final self-verify, and failures point at `report`.
- The dictionary-cache build is guarded by a cross-process file lock (two mpv instances won't both
  rebuild the same cache), and jimaku HTTPS uses `certifi`'s CA bundle.

### Fixed

- **Pitch/frequency dictionaries with a wrong stored CRC-32 (e.g. NHK 2016 pitch) were misclassified
  as definition dictionaries** and silently filed under `dicts`, so their pitch accents never rendered
  (and `doctor` showed no pitch category). Classification now reads the term-meta bank CRC-tolerantly,
  matching the loader.
- **The overlay was inert on Windows** — nothing read the named pipe in steady state, so
  hover/tooltip/mining/translation and mpv-quit detection all silently failed even though `attach`
  reported success.
- **`re.PatternError: bad escape \U`** crash in plugin install/`setup` on Windows paths.
- **`FileNotFoundError`** on first run when the config held Yomitan titles instead of file paths.
- **`run` crashed with a traceback** when mpv wasn't found; it now exits with a clear hint.
- **`--version` reported `0.0.0`.**
- **Secret redaction** leaked the token in `Authorization: Bearer <token>` (caught by a property test).
- **`config` writes** could drop `[mine]`/`[jimaku]`/`[known]` tables on merge; the TOML writer now
  round-trips nested tables.

### Security

- Diagnostics and crash logs redact API keys/tokens and scrub the home path + OS username; they are
  written locally and never uploaded (the user chooses to share via `report`).

<!-- Release links go here once tags exist, e.g.:
[Unreleased]: https://github.com/serjflint/saitenka/compare/v0.1.0...HEAD
-->
