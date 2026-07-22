# Changelog

All notable changes to Saitenka are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/). Entries are curated for readers — they are not raw commit
logs.

## [Unreleased]

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
