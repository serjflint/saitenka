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
- **Architecture enforcement — cycles, layering, GPL chokepoint.** `overlay/app/` has grown into a
  coupling hotspot (~38 modules, god-objects: `controller.py` ~2000 LOC, `cli.py` ~1200), and the
  only guard is one regex test (`tests/test_layering.py`, single PIL rule). Replace it with a real
  dependency-contract engine — **trial import-linter vs Tach**, then pick — folded into
  `uv run poe all`, enforcing three rules: **no import cycles**, the **PIL-agnostic core**, and a
  **GPL chokepoint** (only `app/dictionary.py` may import the optional `saitenka_deinflect` add-on,
  guarded — a chokepoint, *not* a ban, since the core imports it on purpose). Rolled out via a
  **ratchet/baseline** so it lands without a big-bang refactor (`sc/` isn't layer-pure today). Cheap
  `ruff TID` ban as defense-in-depth on the license boundary; **pyscn** for offline god-object
  metrics (CBO + complexity) feeding the `controller.py` decomposition — metrics *inform*, contracts
  *enforce* (Goodhart). Analyzers run out-of-process on the standard interpreter (never imported into
  the free-threaded runtime). Plan: `vibe/architecture-enforcement-plan.md`.
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
- **Observability — non-blocking logs, traces, and metrics.** Make the latency story
  *measurable at runtime*, not just in the benchmark harness. Three grounded decisions (after surveying
  how mpv and comparable tools do it):
  - **Structured logging (`structlog` + JSON).** Key-value event logs for the rotating log + `report`
    bundle so diagnostics are grepable/parseable (e.g. `event=jimaku.fetch title=… status=400`) instead
    of free-text; redaction as a pipeline processor; keep the human console renderer, JSON to the file.
    Low-risk, incremental over the current stdlib `logging`.
  - **Tracing/metrics via the OpenTelemetry *API* — not the deployment.** Instrument the
    latency-critical points (render/raster, overlay upload, hover hit-test, panel/dict caches, dict SQL,
    IPC round-trip, sub-seek, prefetch depth) with `opentelemetry-{api,sdk}` (both pure-Python, so
    free-threading-safe — no C extension to re-enable the GIL). Vendor-neutral and swappable, vs. a
    hand-rolled facade. Lift the harness's p50/p95/**p99** into live histograms; add a `gil_enabled` gauge.
  - **Non-blocking + local-first by design.** A custom mpv-style *gated* span processor (an atomic
    "active" flag → ~free when nobody is inspecting, a bounded ring buffer with a surfaced `dropped`
    counter, off-hot-path export, sampling on the hot per-tick paths). Default export is a small
    **Chrome Trace Format** writer → view in `chrome://tracing` / Perfetto, **no backend, no gRPC/protobuf**
    (the OTLP/gRPC exporter has no free-threaded wheels and can silently re-enable the GIL — kept as an
    opt-in HTTP-only extra for anyone who wants a self-hosted backend). Traps to respect:
    `contextvars` don't cross a `queue.Queue` (context must be reattached in worker threads), and a CI
    check should assert the GIL stays off after telemetry imports.

## Explicitly not planned

- **Async / `aiohttp` HTTP stack** — the app is synchronous and its HTTP volume is tiny and mostly
  localhost; going async would be a large refactor with a free-threading (C-extension GIL) risk for
  no meaningful gain.

## How this roadmap is kept

Curated by hand and reviewed against reality, not auto-generated. Near-term items graduate to issues
and milestones; when they ship they move to the changelog. Automated changelog generation (e.g.
[git-cliff](https://git-cliff.org/) with Conventional Commits) may be adopted later, but the
changelog is written for readers, not derived from commit messages.
