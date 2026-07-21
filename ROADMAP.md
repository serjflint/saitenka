# Roadmap

The direction and near-term plans for Saitenka. This is the high-level "what & why"; concrete,
trackable work lives in the issue tracker and milestones. Shipped work is in
[CHANGELOG.md](CHANGELOG.md). Contributions toward anything here are welcome — see
[AGENTS.md](AGENTS.md) for how to work in the repo.

## Now / next

- **Progressive subtitles** — draw plain (uncolored) subtitles immediately and swap in FSRS coloring +
  tooltips once dictionaries finish loading. The top-left loading spinner already covers the
  blank-frame gap; this would make the first-run wait feel instant.
- **Runtime jimaku keybind** — re-fetch subtitles mid-playback from a key. The option already exists
  (`--jimaku-force` / `[jimaku].force`); the reusable primitive (`fetch_jimaku`) is in place, so this
  is wiring a controller keybind.
- **HTTP client decision** — keep `urllib`, or move AnkiConnect + jimaku to a shared synchronous
  `httpx.Client` for connection pooling and `respx`-based tests. (An async/`aiohttp` client is
  explicitly out of scope — overkill for this sync, low-volume, local-first, free-threaded tool.)

## Considering

- **CI matrix** — Windows / macOS / Linux (plus a free-threaded job) once we want automated
  cross-platform gating. The local gate is `uv run poe all`. (A containerized Windows runner via
  Podman is possible but high-setup-effort — deferred in favor of a real Windows run-through.)
- **Deeper cross-platform hardening** — unified signal / clean-shutdown handling (Windows vs POSIX),
  filename sanitization applied at more write sites, `psutil`-based process-tree cleanup coverage.
- **Test tooling** — `pytest-subprocess` for mpv/ffmpeg launch-argument coverage (currently the live
  launch path is only smoke-tested).

## Explicitly not planned

- **Async / `aiohttp` HTTP stack** — the app is synchronous and its HTTP volume is tiny and mostly
  localhost; going async would be a large refactor with a free-threading (C-extension GIL) risk for
  no meaningful gain.

## How this roadmap is kept

Curated by hand and reviewed against reality, not auto-generated. Near-term items graduate to issues
and milestones; when they ship they move to the changelog. Automated changelog generation (e.g.
[git-cliff](https://git-cliff.org/) with Conventional Commits) may be adopted later, but the
changelog is written for readers, not derived from commit messages.
