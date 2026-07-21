# Changelog

All notable changes to Saitenka are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/). Entries are curated for readers — they are not raw commit
logs.

## [Unreleased]

Cross-platform support (especially Windows), a streaming dictionary importer, diagnostics, and a broad
hardening pass.

### Added

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
- A **top-left loading spinner** while dictionaries load (attach/plugin mode), so the first frames
  aren't a blank screen.

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
