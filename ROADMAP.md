# Roadmap

The direction and near-term plans for Saitenka. This is the high-level "what & why"; concrete,
trackable work lives in the issue tracker and milestones. Shipped work is in
[CHANGELOG.md](CHANGELOG.md). Contributions toward anything here are welcome — see
[AGENTS.md](AGENTS.md) for how to work in the repo.

## Now / next

- **Cold first-paint jank — clip/stream the first def body.** The `--stress` benchmark quantifies it:
  memory is clean (no leak, cache LRU-bounded), but the frame-latency tail is severe under load —
  **p99 ~990 ms / MAX ~1.1 s** on a cold pathological entry (a huge monolingual first definition). The
  cause: viewport-first rendering fills the viewport by rasterising whole SC blocks, so a giant first
  block overshoots. Fix = clip/stream that first def body block-by-block (the head only rasterises the
  covering strip; the rest streams behind it), the way later def bodies already defer. Target: cold
  first-paint p99 back under ~250 ms. This is the one real jank lever left (warm hover / scroll / nested
  are all well inside the frame budget).
- **Runtime jimaku keybind** — re-fetch subtitles mid-playback from a key. The option already exists
  (`--jimaku-force` / `[jimaku].force`); the reusable primitive (`fetch_jimaku`) is in place, so this
  is wiring a controller keybind.
- **HTTP client decision** — keep `urllib`, or move AnkiConnect + jimaku to a shared synchronous
  `httpx.Client` for connection pooling and `respx`-based tests. (An async/`aiohttp` client is
  explicitly out of scope — overkill for this sync, low-volume, local-first, free-threaded tool.)

## Considering

- **CI matrix — Windows / macOS / Linux (deferred).** The local gate is `uv run poe all`; the suite is
  already structured for CI when we want it — the transport **contract suite**
  (`tests/test_transport_contract.py`, over a cross-platform `socketpair` + an in-memory fake) and the
  `windows_sim`-tagged tests run everywhere, and `pytest -m windows_sim` / the tier markers let CI
  select per-OS. What genuinely needs a **real Windows kernel** — named-pipe transport, `filelock`
  mandatory locks, `long_path` `\\?\` prefixing (an `os.name="nt"` `WindowsPath` can't instantiate on
  POSIX), known-folder redirection — is the only residue a Windows job would add. Two honest executors:
  a **free `windows-latest` GitHub Actions job** (public repo ⇒ $0, x86-64 = matches most users) and/or
  a **Windows 11 ARM VM** (UTM / free VMware Fusion) for interactive debug. **Note:** a Podman/Docker
  *Windows* container is **not** an option on Apple Silicon — Windows containers require a Windows host
  kernel, and Docker/Podman on a Mac only run *Linux* containers in an ARM VM (an earlier note calling
  it "high-effort but possible" was wrong). Deferred on purpose; the mac-local gate covers ~98% and the
  groundwork makes turning CI on later a small change.
- **Deeper cross-platform hardening** — unified signal / clean-shutdown handling (Windows vs POSIX),
  filename sanitization applied at more write sites, `psutil`-based process-tree cleanup coverage.
- **Test tooling** — `pytest-subprocess` for mpv/ffmpeg launch-argument coverage (currently the live
  launch path is only smoke-tested).
- **Benchmarking depth** — the headless harness (`examples/bench_responsiveness.py`, incl. `--stress`)
  now reports p50/p95/**p99** + CV, records the GIL state, and isolates raster/BGRA/upload timing. Not
  yet built, in rough priority: a **live-mpv jank harness** that polls mpv's own `frame-drop-count`
  while driving the overlay (the only true real-time signal — the fake-IPC harness can't see mpv's
  compositor); a **noise-aware regression gate** for CI (fail on a >X% p95 delta only once run-to-run
  variance is characterized); and continuous benchmarking (`asv`) once there's CI history. **Not**
  CodSpeed (its CPU-instruction model is blind to our IO + free-threaded contention). The suspected
  ~55 ms temp-file **upload floor turned out to be a page-cache artifact** (the write is ~1 ms), so an
  mmap/shared-memory upload backend is **de-prioritised** — cold first-paint is render + lookup bound.
- **Structured logging (`structlog`)** — key-value event logs for the rotating log + `report` bundle,
  so diagnostics are grepable/parseable (e.g. `event=jimaku.fetch title=… status=400`) instead of
  free-text. Keep the human console renderer; JSON to the file. Low-risk, incremental over the current
  stdlib `logging`; do it when the diagnostics story needs it.

## Explicitly not planned

- **Async / `aiohttp` HTTP stack** — the app is synchronous and its HTTP volume is tiny and mostly
  localhost; going async would be a large refactor with a free-threading (C-extension GIL) risk for
  no meaningful gain.

## How this roadmap is kept

Curated by hand and reviewed against reality, not auto-generated. Near-term items graduate to issues
and milestones; when they ship they move to the changelog. Automated changelog generation (e.g.
[git-cliff](https://git-cliff.org/) with Conventional Commits) may be adopted later, but the
changelog is written for readers, not derived from commit messages.
