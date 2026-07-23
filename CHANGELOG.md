# Changelog

All notable changes to Saitenka are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/). Entries are curated for readers — they are not raw commit
logs.

## [Unreleased]

## [0.4.0] - 2026-07-23

### Added

- **`saitenka-overlay import <dir>`** — build your Yomitan dictionaries into the consolidated database in
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
