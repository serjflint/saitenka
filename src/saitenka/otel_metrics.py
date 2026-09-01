"""Pull-based metric instruments: registered once when telemetry is configured, read on demand via
:func:`snapshot` — no periodic push, so metrics cost nothing until something actually inspects them
(``doctor``, a test, a future ``report`` bundle).

Instrument handles are module globals, ``None`` until :func:`register` runs (i.e. until telemetry is
enabled) — call sites must null-check before recording, the same pattern the rest of the codebase
already uses for optional collaborators. Low-cardinality labels only: dict *name* is fine,
per-word/per-entry is not (unbounded label cardinality is an OTel/Prometheus anti-pattern).
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator
    from contextlib import AbstractContextManager
    from types import ModuleType

    from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricsData

_lock = threading.Lock()
_reader: InMemoryMetricReader | None = None

# Histograms — render/upload/hit-test/dict-sql/ipc-roundtrip/sub-seek, all in milliseconds.
render_duration_ms: Histogram | None = None
# Pixel height of one materialised block — the render-budget signal. A def body is one block, so this
# distribution's tail exposes the coarse-block jank (a single reach rasterises the whole body).
block_rendered_px: Histogram | None = None
upload_duration_ms: Histogram | None = None
hit_test_duration_ms: Histogram | None = None
dict_sql_duration_ms: Histogram | None = None
ipc_roundtrip_ms: Histogram | None = None
sub_seek_duration_ms: Histogram | None = None
# Seek-to-paint chain: cue_redraw wraps set_subtitle end-to-end (tokenize/score/render/upload);
# subtitle_render isolates the PIL render_subtitle() call inside it; sub_text_reconcile wraps the
# poll-loop's mpv-driven redraw (native sub-seek / normal cue advance), sibling to sub_nav's own
# sub_seek span for the instant-nav (Alt+←/→/↓) path.
cue_redraw_duration_ms: Histogram | None = None
subtitle_render_duration_ms: Histogram | None = None
sub_text_reconcile_duration_ms: Histogram | None = None
subtitle_geometry_ready_ms: Histogram | None = None
subtitle_geometry_prepare_ms: Histogram | None = None
subtitle_geometry_render_ms: Histogram | None = None
subtitle_geometry_renderer_build_ms: Histogram | None = None
subtitle_geometry_extract_ms: Histogram | None = None
subtitle_geometry_active_events: Histogram | None = None
subtitle_geometry_eligible_tokens: Histogram | None = None
subtitle_geometry_skipped_tokens: Histogram | None = None
# Scroll-input → redraw-finished chain: wraps controller.scroll_tip end-to-end (banded/blit
# re-render + OSD upload) for one wheel tick or TIP_UP/DOWN keypress — its duration IS the
# scroll-to-photon latency a user would feel as stutter.
scroll_frame_duration_ms: Histogram | None = None
# Hover → tooltip-drawn latency: wraps show_tooltip end-to-end (panel_for head render + place +
# blit), the headline "does the tip feel instant?" number. Labeled kind=cold|warm — a cold hover
# builds the panel, a warm one is a cache hit + upload. Symmetric with scroll_frame/sub_seek, which
# already have their own end-to-end wrapper; the base-tooltip show was the one hot path that didn't.
show_tooltip_duration_ms: Histogram | None = None

# Counters.
panel_cache_hits: Counter | None = None
panel_cache_misses: Counter | None = None
panel_cache_evictions: Counter | None = (
    None  # LRU entries dropped — thrash driver under a small cap
)
dict_cache_hits: Counter | None = None
dict_cache_misses: Counter | None = None
dict_cache_evictions: Counter | None = None  # decoded entries dropped over entry_cache_max
# Per-block pixel cache (WindowedPanel._blocks), the layer under panel_cache: a miss rasterises the
# block, a hit reuses a still-retained one, an eviction drops it (O(viewport) retention → a re-scroll
# re-renders). The jank lives here, so it gets its own counters distinct from the per-word panel_cache.
block_cache_hits: Counter | None = None
block_cache_misses: Counter | None = None
block_cache_evictions: Counter | None = None
# Font/pixel memos added during the post-1.0.0 render campaign — a miss is the real work (getmask2
# raster / getlength measure / premul-BGRA convert), so the hit:miss ratio is the memo's payoff.
glyph_mask_hits: Counter | None = None  # font.glyph_mask memo (#156) — getmask2 is ~half render CPU
glyph_mask_misses: Counter | None = None
glyph_mask_evictions: Counter | None = None  # LRU raster drops — a capacity miss vs a cold one
glyph_width_hits: Counter | None = None  # font.text_width memo (#125) — getlength
glyph_width_misses: Counter | None = None
glyph_width_evictions: Counter | None = (
    None  # LRU width drops — separates cap thrash from first-see
)
bgra_memo_hits: Counter | None = None  # WindowedPanel per-band premul-BGRA memo (#138)
bgra_memo_misses: Counter | None = None
precompose_hits: Counter | None = None  # warm hover served the idle-precomposed first viewport
precompose_builds: Counter | None = None  # first viewports composited in idle by a prefetch worker
dropped_telemetry_spans: Counter | None = None
cold_first_paint_overshoot: Counter | None = None
osd_paused_draw: Counter | None = (
    None  # overlay draws that landed while paused (the #8172 bug window)
)
osd_paused_nudge: Counter | None = None  # paused-OSD re-flushes issued to un-throttle mpv
# A hover that pauses playback owes a resume, and a session where the resume never reaches mpv is
# indistinguishable in the log from one where it was never owed. These three split that: which
# lifecycle deadlines are armed vs delivered (nothing retires a hover if `tooltip-hide` never fires),
# what the hover machine decided as the cursor left, and whether a teardown found a claim to release.
lifecycle_timer_armed: Counter | None = None  # labeled kind=, outcome=accepted|refused
lifecycle_timer_settled: Counter | None = None  # labeled kind=, outcome=delivered|fenced|failed
hover_route_decisions: Counter | None = None  # labeled decision= (the machine's own class name)
hover_pause_release: Counter | None = None  # labeled outcome=resumed|nothing-owed
hover_pause_claim: Counter | None = (
    None  # labeled outcome=sent|already-paused|observed-paused|policy-off
)
#: labeled outcome=no-observation|adopted|reinstalled. The settle runs once per event drain, and the
#: drain runs at mpv's observation rate — `time-pos` alone is ~27/s, mouse motion several times that.
#: Counted rather than spanned because a tick that finds no cue is a clock reading, not an event: as
#: spans those were 39% of a whole trace file and 2873 of their 2902 instances said only this.
cue_settles: Counter | None = None
#: labeled sources=system+fonts-dir+attachments+in-file. Field evidence for whether the three
#: sources beyond the system providers matter: how often a track's typesetting comes from one.
subtitle_geometry_font_sources: Counter | None = None
#: labeled renderer=legacy|native. A deliberate switch, not a failure: the catastrophic-fallback
#: counter next door says the native path gave up, and conflating the two would make a user
#: comparing the engines look like a regression.
subtitle_renderer_forced: Counter | None = None
#: labeled reason=. The overprint stands down rather than coloring words in a substitute face. Each
#: demotion is a device the ladder could not use, so this is where "the color went missing" stops
#: being invisible and becomes a number a report can show.
subtitle_overprint_demotions: Counter | None = None
subtitle_overpaint_frames: Counter | None = None
subtitle_layout_drift_px: Histogram | None = None


def record_cue_settle(outcome: str, span: SpanSetter | None = None) -> None:
    """Record one settle outcome to the counter, and to *span* when the settle earned one. Both
    writes live here so the count and the span can never disagree about what a settle decided —
    and beside the instrument rather than on the SessionController, which needs no member to hold a null check.
    """
    if span is not None:
        span.set("outcome", outcome)
    if cue_settles is not None:
        cue_settles.add(1, {"outcome": outcome})


# A correlated write is fire-and-forget at the call site, so nothing downstream separates a
# command mpv ran in 5ms from one that sat queued for six seconds — and mpv's own log timestamps
# when it RAN a command, never when we asked for it.
mpv_effect_apply_ms: Histogram | None = None  # submitted → terminal outcome, labeled identity=
mpv_effect_outcome: Counter | None = None  # labeled identity=, outcome=succeeded|…|not-admitted
scroll_frame_jank: Counter | None = None  # scroll frames slower than SCROLL_JANK_THRESHOLD_MS
# Persistent cross-session caches (#149). Their hit:miss ratio IS the "is the prewarm worth it?" signal:
# a render_cache hit is a cold pathological hover direct-painted from disk (the 40-170ms → <2ms win); an
# atlas hit is a getmask2 raster skipped by a disk-loaded mask. Writebacks/evictions show the live cache
# earning its keep / churning under the byte ceiling.
render_cache_hits: Counter | None = None  # cold hover direct-painted from the on-disk render cache
render_cache_misses: Counter | None = None  # cold hover with no cached head → full build+raster
render_cache_writebacks: Counter | None = None  # live-built head persisted to disk for next session
render_cache_evictions: Counter | None = None  # on-disk heads LRU-dropped over render_cache_max_mb
mask_atlas_hits: Counter | None = None  # getmask2 served from the disk-loaded glyph mask atlas
mask_atlas_misses: Counter | None = None  # atlas active but glyph absent → rasterised this session
mask_atlas_writebacks: Counter | None = (
    None  # rasterised glyph persisted to the atlas for next session
)
# Idle crisp post-render (hi-dpi): swaps = a native re-render replaced the soft upscale; stale = it
# finished after the user switched word / scrolled, so it was discarded (idle work that didn't pay off).
crisp_swaps: Counter | None = None
crisp_stale: Counter | None = None
subtitle_geometry_failures: Counter | None = None
subtitle_geometry_decisions: Counter | None = None
subtitle_geometry_owner_transitions: Counter | None = None
subtitle_geometry_recoveries: Counter | None = None
subtitle_pixel_catastrophic_fallbacks: Counter | None = None
subtitle_pixel_retry_exhausted: Counter | None = None

# ~one frame at 60Hz. The tail (p95/p99 jank-frame rate), not the mean, is what a user perceives
# as scroll stutter — see scroll_frame_jank.
SCROLL_JANK_THRESHOLD_MS = 16.0

# A cold hover slower than this feels laggy (the perceptible-lag threshold; well above one frame
# because a cold paint legitimately builds the panel). cold_first_paint_overshoot counts base-tooltip
# shows that a viewport-first head render was supposed to keep under it but didn't — see show_tooltip.
COLD_FIRST_PAINT_BUDGET_MS = 100.0

# Gauges (prefetch queue depth is push-updated by the caller; gil_enabled is observed on read).
prefetch_queue_depth: UpDownCounter | None = None

# Histograms with NO corresponding span: their summary (count/p50/p95/p99/max) is their ONLY trace
# representation, so the writer exports it as counter-series. A span-backed histogram (render, upload,
# dict_sql, scroll_frame, …) is deliberately absent — its percentiles are derivable from the spans it
# already emits, so exporting the summary too just duplicates bytes for zero extra step-resolution.
# Staleness fails safe: a newly-added spanless histogram simply won't graph until listed here (the same
# state as before this existed) — it never silently drops a counter that has no other representation.
SPANLESS_HISTOGRAMS = frozenset(
    {
        "saitenka.ipc.roundtrip_ms",  # timed() only — no ipc span (would flood at poll cadence)
        "saitenka.block_cache.rendered_px",  # band pixel height — a measure, not a duration span
        "saitenka.mpv_effect.apply_ms",  # no span: the wait happens after the caller returned
    }
)


@contextmanager
def timed(histogram: Histogram | None, **attributes: str) -> Generator[None]:
    """Record the wrapped block's duration (ms) into *histogram* — a no-op when it's ``None``
    (telemetry disabled or not yet configured), so every call site stays safe to wrap
    unconditionally. Pass the live module attribute (e.g. ``otel_metrics.dict_sql_duration_ms``),
    not a captured local — the attribute is re-read fresh each time the ``with`` block is entered,
    so it tracks configure()/shutdown() without the call site needing to care."""
    if histogram is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        histogram.record((time.perf_counter() - start) * 1000.0, attributes or None)


#: Memoized resolution of `opentelemetry.trace`: `None` = not yet checked, ``False`` = confirmed
#: unavailable (the telemetry extra isn't installed — this can't change at runtime, so caching
#: it forever is correct). Avoids re-attempting (and re-catching ImportError from) a failing import
#: on every single call from a hot call site like `traced()`.
_trace_available: bool | None = None
_trace_module: ModuleType | None = None


def _resolve_trace_module() -> ModuleType | None:
    global _trace_available, _trace_module
    if _trace_available is None:
        try:
            import opentelemetry.trace as _trace
        except ImportError:
            _trace_available = False
        else:
            _trace_available = True
            _trace_module = _trace
    return _trace_module if _trace_available else None


class SpanSetter:
    """Handle yielded by :func:`traced`/:func:`instrumented` so a caller can attach an attribute
    computed *during* the block (e.g. how many bands a scroll frame rasterised — known only after the
    work). ``None`` span → a no-op, so ``with traced(...) as s: s.set(...)`` is safe with telemetry off
    and with the extra absent. Low-cardinality values only, same rule as counters: a count/height/bool,
    never a per-word/per-entry string."""

    __slots__ = ("_span",)

    def __init__(self, span: Any | None) -> None:
        self._span: Any | None = span

    def set(self, key: str, value: object) -> None:
        if self._span is not None:
            self._span.set_attribute(key, value)


_NOOP_SPAN = SpanSetter(None)


@contextmanager
def traced(name: str, **attributes: str) -> Generator[SpanSetter]:
    """A real OTel span via the global tracer API — a no-op (not just an unrecorded span, but no
    `opentelemetry` import attempt beyond the first) when the ``telemetry`` extra isn't
    installed, so every call site stays safe to wrap unconditionally, same contract as :func:`timed`.
    When the extra IS installed but telemetry isn't configured, `trace.get_tracer()` itself returns
    OTel's built-in no-op tracer — cheap, not a crash.

    Yields a :class:`SpanSetter` for attributes computed inside the block; ``with traced(...):`` that
    ignores it stays valid.

    Bug this exists to prevent: an earlier direct ``from opentelemetry import trace`` at a call site
    crashed a background thread on any install without the extra — found via live end-to-end testing,
    not by any test in this suite (the dev/test env always has the extra installed via `[full]`)."""
    trace = _resolve_trace_module()
    if trace is None:
        yield _NOOP_SPAN
        return
    with trace.get_tracer("saitenka.overlay").start_as_current_span(name) as span:
        # otel_export._span_to_ctf_event reads this back for the CTF event's "tid" — without it,
        # every independently-started span (no parent) gets a random trace_id, and using THAT for
        # tid scatters unrelated spans across a different synthetic "thread" row each, defeating the
        # point of a timeline view (found by actually opening a real trace in Perfetto and looking).
        span.set_attribute("thread.id", threading.get_native_id())
        # No `session` here: it is one value for the whole run, so the exporter writes it once into
        # the document's `otherData`. Stamped per span it was 5.5% of a real trace file.
        for k, v in attributes.items():
            span.set_attribute(k, v)
        # Per-thread CPU time across the span. wall (the span's dur) ≫ cpu_ms ⇒ the thread was
        # descheduled inside the span — GIL contention, a lock, or I/O wait — not doing work. This is
        # what disambiguates a genuinely-slow span from one merely stalled behind other threads: the
        # startup dep-load's freq-dict span reads ~470ms wall but ~290ms cpu once fugashi re-enables
        # the GIL, so ~180ms of it is contention, not freq work (invisible without this).
        cpu0 = time.thread_time()
        try:
            yield SpanSetter(span)
        finally:
            span.set_attribute("cpu_ms", round((time.thread_time() - cpu0) * 1000.0, 3))


class _GeometryTelemetry:
    """This module, as the subtitle core's geometry-telemetry port.

    Structural, not declared: `saitenka_subtitles.telemetry` defines the shape and nothing here
    imports it, so the subtitle core owes this module nothing back. The indirection is not
    ceremony — the histograms below are module globals `configure` reassigns, and a library that
    reads them is bound to this application's telemetry lifecycle.
    """

    def span(self, name: str) -> AbstractContextManager[SpanSetter]:
        return traced(name)

    def record(self, metric: str, milliseconds: float) -> None:
        # Read inside the call, never captured: `configure` rebinds these globals, so a mapping built
        # at import would pin whatever they were before telemetry was set up — all three `None`.
        histograms = {
            "renderer_build_ms": subtitle_geometry_renderer_build_ms,
            "render_ms": subtitle_geometry_render_ms,
            "extract_ms": subtitle_geometry_extract_ms,
        }
        if metric not in histograms:
            raise ValueError(f"unknown geometry metric: {metric}")
        histogram = histograms[metric]
        if histogram is not None:
            histogram.record(milliseconds)


geometry_telemetry = _GeometryTelemetry()


class DeferredSpan:
    """A span whose end arrives on someone else's thread, later.

    :func:`traced` cannot express a correlated mpv write: the caller returns the moment the effect is
    admitted and the terminal outcome lands in a callback seconds later, so a `with` block would
    measure the submission and nothing else. Started here, ended from the completion — which is what
    puts the wait on the same timeline as the `tooltip_show` that caused it, instead of leaving it to
    be reconstructed by aligning three clocks (overlay.log wall, mpv's relative, the trace's epoch).

    Not `start_as_current_span`: the span must not become the ambient parent of whatever the
    submitting thread does next, and it outlives that scope anyway.
    """

    __slots__ = ("_span",)

    def __init__(self, name: str, **attributes: str) -> None:
        trace = _resolve_trace_module()
        if trace is None:
            self._span: Any | None = None
            return
        span = trace.get_tracer("saitenka.overlay").start_span(name)
        span.set_attribute("thread.id", threading.get_native_id())  # the SUBMITTING thread
        for k, v in attributes.items():
            span.set_attribute(k, v)
        self._span = span

    def finish(self, **attributes: object) -> None:
        if self._span is None:
            return
        for k, v in attributes.items():
            self._span.set_attribute(k, v)
        self._span.end()
        self._span = None


@contextmanager
def instrumented(
    histogram: Histogram | None, span_name: str, *, emit_span: bool = True, **attributes: str
) -> Generator[SpanSetter]:
    """:func:`traced` + :func:`timed` together — a span AND a histogram sample from the same block,
    for anchors where both a live percentile and a visible Perfetto timeline entry are useful.
    Deliberately NOT used at every anchor: a very-high-frequency call site (e.g. the mpv IPC
    round-trip, called on effectively every poll tick) would flood trace.json with spans — it stays
    on :func:`timed` alone.

    ``emit_span=False`` records only the histogram (the percentile still lands) and skips the span —
    for a call site whose phase a parent span already covers on the hot path (dict_sql inside a
    background prefetch_decode), so the per-call spans don't flood the trace off the critical path."""
    if not emit_span:
        with timed(histogram, **attributes):
            yield _NOOP_SPAN
        return
    with traced(span_name, **attributes) as span, timed(histogram, **attributes):
        yield span


@contextmanager
def instrumented_jank(
    histogram: Histogram | None,
    jank_counter: Counter | None,
    jank_threshold_ms: float,
    span_name: str,
    **attributes: str,
) -> Generator[SpanSetter]:
    """:func:`instrumented` plus a jank counter bump when the block runs past *jank_threshold_ms* —
    for interactive paths (e.g. scroll) where the tail, not the mean, is what a user perceives as
    stutter. Both instruments share one timer so a jank frame's duration always matches what the
    histogram recorded for it."""
    if histogram is None and jank_counter is None:
        with traced(span_name, **attributes) as span:
            yield span
        return
    start = time.perf_counter()
    with traced(span_name, **attributes) as span:
        try:
            yield span
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if histogram is not None:
                histogram.record(elapsed_ms, attributes or None)
            if jank_counter is not None and elapsed_ms > jank_threshold_ms:
                jank_counter.add(1, attributes or None)


def _gil_enabled_callback(_options):
    from opentelemetry.metrics import Observation

    yield Observation(1 if sys._is_gil_enabled() else 0)


def register(reader: InMemoryMetricReader, meter: Meter) -> None:
    """Create every instrument against *meter* (whose provider is wired to *reader*). Idempotent
    isn't needed here — :func:`saitenka.app.telemetry.configure` already guards against double-init."""
    global _reader
    global render_duration_ms, upload_duration_ms, hit_test_duration_ms
    global dict_sql_duration_ms, ipc_roundtrip_ms, sub_seek_duration_ms
    global cue_redraw_duration_ms, subtitle_render_duration_ms, sub_text_reconcile_duration_ms
    global subtitle_geometry_ready_ms, subtitle_geometry_prepare_ms
    global subtitle_geometry_render_ms, subtitle_geometry_extract_ms
    global subtitle_geometry_renderer_build_ms
    global subtitle_geometry_active_events
    global subtitle_geometry_eligible_tokens, subtitle_geometry_skipped_tokens
    global scroll_frame_duration_ms, show_tooltip_duration_ms
    global panel_cache_hits, panel_cache_misses, panel_cache_evictions
    global dict_cache_hits, dict_cache_misses, dict_cache_evictions
    global block_rendered_px, block_cache_hits, block_cache_misses, block_cache_evictions
    global glyph_mask_hits, glyph_mask_misses, glyph_mask_evictions
    global glyph_width_hits, glyph_width_misses, glyph_width_evictions
    global bgra_memo_hits, bgra_memo_misses, precompose_hits, precompose_builds
    global dropped_telemetry_spans, cold_first_paint_overshoot, prefetch_queue_depth
    global osd_paused_draw, osd_paused_nudge, scroll_frame_jank
    global render_cache_hits, render_cache_misses, render_cache_writebacks, render_cache_evictions
    global mask_atlas_hits, mask_atlas_misses, mask_atlas_writebacks
    global crisp_swaps, crisp_stale, subtitle_geometry_failures
    global subtitle_geometry_decisions, subtitle_geometry_owner_transitions
    global subtitle_geometry_recoveries
    global subtitle_pixel_catastrophic_fallbacks, subtitle_pixel_retry_exhausted
    global lifecycle_timer_armed, lifecycle_timer_settled
    global hover_pause_claim, mpv_effect_apply_ms, mpv_effect_outcome
    global hover_route_decisions, hover_pause_release, cue_settles
    global subtitle_geometry_font_sources, subtitle_renderer_forced
    global subtitle_overprint_demotions, subtitle_overpaint_frames
    global subtitle_layout_drift_px

    with _lock:
        _reader = reader
        render_duration_ms = meter.create_histogram(
            "saitenka.render.duration_ms", unit="ms", description="panel/flow render time"
        )
        upload_duration_ms = meter.create_histogram(
            "saitenka.upload.duration_ms", unit="ms", description="overlay BGRA upload time"
        )
        hit_test_duration_ms = meter.create_histogram(
            "saitenka.hit_test.duration_ms", unit="ms", description="per-tick hover hit-test time"
        )
        dict_sql_duration_ms = meter.create_histogram(
            "saitenka.dict_sql.duration_ms", unit="ms", description="dictionary SQLite lookup time"
        )
        ipc_roundtrip_ms = meter.create_histogram(
            "saitenka.ipc.roundtrip_ms", unit="ms", description="mpv IPC command round-trip time"
        )
        sub_seek_duration_ms = meter.create_histogram(
            "saitenka.sub_seek.duration_ms", unit="ms", description="subtitle-index seek time"
        )
        cue_redraw_duration_ms = meter.create_histogram(
            "saitenka.cue_redraw.duration_ms",
            unit="ms",
            description="set_subtitle end-to-end: tokenize/score/render/upload for one cue",
        )
        subtitle_render_duration_ms = meter.create_histogram(
            "saitenka.subtitle_render.duration_ms",
            unit="ms",
            description="render_subtitle() PIL layout+draw time for one cue",
        )
        sub_text_reconcile_duration_ms = meter.create_histogram(
            "saitenka.sub_text_reconcile.duration_ms",
            unit="ms",
            description="poll-loop redraw latency for an mpv-driven sub-text change (native "
            "sub-seek / normal cue advance), sibling to sub_seek for the instant-nav path",
        )
        subtitle_geometry_ready_ms = meter.create_histogram(
            "saitenka.subtitle_geometry.ready_ms",
            unit="ms",
            description="accepted current-frame request to published geometry",
        )
        subtitle_geometry_prepare_ms = meter.create_histogram(
            "saitenka.subtitle_geometry.prepare_ms",
            unit="ms",
            description="ASS active-frame matching and ID rewrite time",
        )
        subtitle_geometry_render_ms = meter.create_histogram(
            "saitenka.subtitle_geometry.render_ms",
            unit="ms",
            description="offscreen libass render time",
        )
        subtitle_geometry_renderer_build_ms = meter.create_histogram(
            "saitenka.subtitle_geometry.renderer_build_ms",
            unit="ms",
            description="libass library init and font scan for a renderer the cache did not hold",
        )
        subtitle_geometry_extract_ms = meter.create_histogram(
            "saitenka.subtitle_geometry.extract_ms",
            unit="ms",
            description="ID-layer geometry extraction time",
        )
        subtitle_geometry_active_events = meter.create_histogram(
            "saitenka.subtitle_geometry.active_events",
            description="authored ASS events in a geometry decision",
        )
        subtitle_geometry_eligible_tokens = meter.create_histogram(
            "saitenka.subtitle_geometry.eligible_tokens",
            description="interaction-eligible tokens in a geometry decision",
        )
        subtitle_geometry_skipped_tokens = meter.create_histogram(
            "saitenka.subtitle_geometry.skipped_tokens",
            description="tokens excluded before geometry rendering",
        )
        scroll_frame_duration_ms = meter.create_histogram(
            "saitenka.scroll_frame.duration_ms",
            unit="ms",
            description="scroll-input to redraw-finished latency for one wheel tick / TIP_UP-DOWN",
        )
        show_tooltip_duration_ms = meter.create_histogram(
            "saitenka.show_tooltip.duration_ms",
            unit="ms",
            description="hover to base-tooltip-drawn latency (labeled kind=cold|warm)",
        )
        panel_cache_hits = meter.create_counter("saitenka.panel_cache.hits")
        panel_cache_misses = meter.create_counter("saitenka.panel_cache.misses")
        panel_cache_evictions = meter.create_counter(
            "saitenka.panel_cache.evictions", description="LRU panels dropped over the cap"
        )
        dict_cache_hits = meter.create_counter("saitenka.dict_cache.hits")
        dict_cache_misses = meter.create_counter("saitenka.dict_cache.misses")
        dict_cache_evictions = meter.create_counter(
            "saitenka.dict_cache.evictions",
            description="decoded entries dropped over entry_cache_max",
        )
        block_rendered_px = meter.create_histogram(
            "saitenka.block_cache.rendered_px", description="pixel height of one materialised block"
        )
        block_cache_hits = meter.create_counter("saitenka.block_cache.hits")
        block_cache_misses = meter.create_counter(
            "saitenka.block_cache.misses", description="blocks rasterised on demand (a render)"
        )
        block_cache_evictions = meter.create_counter(
            "saitenka.block_cache.evictions", description="retained block pixels dropped"
        )
        glyph_mask_hits = meter.create_counter("saitenka.glyph_mask.hits")
        glyph_mask_misses = meter.create_counter(
            "saitenka.glyph_mask.misses", description="getmask2 rasterisations (glyph memo miss)"
        )
        glyph_mask_evictions = meter.create_counter(
            "saitenka.glyph_mask.evictions", description="LRU raster drops (a capacity miss)"
        )
        glyph_width_hits = meter.create_counter("saitenka.glyph_width.hits")
        glyph_width_misses = meter.create_counter(
            "saitenka.glyph_width.misses", description="getlength measurements (width memo miss)"
        )
        glyph_width_evictions = meter.create_counter(
            "saitenka.glyph_width.evictions", description="LRU width drops (a capacity miss)"
        )
        bgra_memo_hits = meter.create_counter("saitenka.bgra_memo.hits")
        bgra_memo_misses = meter.create_counter(
            "saitenka.bgra_memo.misses", description="per-band premul-BGRA conversions (memo miss)"
        )
        precompose_hits = meter.create_counter(
            "saitenka.precompose.hits",
            description="warm hovers served the idle-precomposed viewport",
        )
        precompose_builds = meter.create_counter(
            "saitenka.precompose.builds",
            description="first viewports composited in idle by a worker",
        )
        dropped_telemetry_spans = meter.create_counter("saitenka.telemetry.dropped_spans")
        cold_first_paint_overshoot = meter.create_counter(
            "saitenka.render.cold_first_paint_overshoot"
        )
        osd_paused_draw = meter.create_counter(
            "saitenka.osd.paused_draw", description="overlay draws that landed while mpv was paused"
        )
        osd_paused_nudge = meter.create_counter(
            "saitenka.osd.paused_nudge",
            description="paused-OSD re-flushes issued (mpv #8172 workaround)",
        )
        scroll_frame_jank = meter.create_counter(
            "saitenka.scroll_frame.jank",
            description=f"scroll frames slower than {SCROLL_JANK_THRESHOLD_MS:.0f}ms",
        )
        render_cache_hits = meter.create_counter(
            "saitenka.render_cache.hits",
            description="cold hovers direct-painted from the on-disk render cache",
        )
        render_cache_misses = meter.create_counter(
            "saitenka.render_cache.misses",
            description="cold hovers with no cached head (full build)",
        )
        render_cache_writebacks = meter.create_counter(
            "saitenka.render_cache.writebacks", description="live heads persisted to disk"
        )
        render_cache_evictions = meter.create_counter(
            "saitenka.render_cache.evictions", description="on-disk heads LRU-dropped over the cap"
        )
        mask_atlas_hits = meter.create_counter(
            "saitenka.mask_atlas.hits", description="getmask2 served from the disk-loaded atlas"
        )
        mask_atlas_misses = meter.create_counter(
            "saitenka.mask_atlas.misses", description="atlas active but glyph absent (rasterised)"
        )
        mask_atlas_writebacks = meter.create_counter(
            "saitenka.mask_atlas.writebacks", description="rasterised glyphs persisted to the atlas"
        )
        crisp_swaps = meter.create_counter(
            "saitenka.crisp.swaps", description="native re-renders swapped in over the soft upscale"
        )
        crisp_stale = meter.create_counter(
            "saitenka.crisp.stale",
            description="native re-renders discarded (word switched/scrolled)",
        )
        subtitle_geometry_failures = meter.create_counter(
            "saitenka.subtitle_geometry.failures",
            description="native subtitle geometry provider or contract failures",
        )
        subtitle_geometry_decisions = meter.create_counter(
            "saitenka.subtitle_geometry.decisions",
            description="native subtitle geometry state transitions",
        )
        subtitle_geometry_owner_transitions = meter.create_counter(
            "saitenka.subtitle_geometry.owner_transitions",
            description="subtitle pixel-owner transitions",
        )
        subtitle_geometry_recoveries = meter.create_counter(
            "saitenka.subtitle_geometry.recoveries",
            description="native geometry recovery after fallback",
        )
        subtitle_pixel_catastrophic_fallbacks = meter.create_counter(
            "saitenka.subtitle_pixels.catastrophic_fallbacks",
            description="proved native-pixel failures committed to legacy subtitle pixels",
        )
        lifecycle_timer_armed = meter.create_counter(
            "saitenka.lifecycle_timer.armed",
            description="lifecycle deadlines scheduled (kind=, outcome=accepted|refused)",
        )
        lifecycle_timer_settled = meter.create_counter(
            "saitenka.lifecycle_timer.settled",
            description="lifecycle deadlines that came due (kind=, outcome=delivered|fenced|failed)",
        )
        hover_route_decisions = meter.create_counter(
            "saitenka.hover.route_decisions",
            description="decisions the hover machine published per observation (decision=)",
        )
        hover_pause_release = meter.create_counter(
            "saitenka.hover.pause_release",
            description="teardowns that found a pause claim to release (outcome=)",
        )
        hover_pause_claim = meter.create_counter(
            "saitenka.hover.pause_claim",
            description="hover pause decisions (outcome=sent|already-paused|observed-paused|policy-off)",
        )
        cue_settles = meter.create_counter(
            "saitenka.cue.settles",
            description="cue settles per event drain (outcome=no-observation|adopted|reinstalled)",
        )
        subtitle_geometry_font_sources = meter.create_counter(
            "saitenka.subtitle_geometry.font_sources",
            description="font sources resolved per track (sources=system+fonts-dir+attachments+in-file)",
        )
        subtitle_renderer_forced = meter.create_counter(
            "saitenka.subtitle.renderer_forced",
            description="deliberate runtime renderer switches (renderer=legacy|native)",
        )
        subtitle_overprint_demotions = meter.create_counter(
            "saitenka.subtitle.overprint_demotions",
            description="cues left uncolored because no device could draw them faithfully (reason=)",
        )
        subtitle_overpaint_frames = meter.create_counter(
            "saitenka.subtitle.overpaint_frames",
            description="frames the raster device colored after the text device stood down",
        )
        subtitle_layout_drift_px = meter.create_histogram(
            "saitenka.subtitle.layout_drift_px",
            unit="px",
            description="worst edge disagreement between mpv's OSD layout and our measurement",
        )
        mpv_effect_apply_ms = meter.create_histogram(
            "saitenka.mpv_effect.apply_ms",
            unit="ms",
            description="correlated command submitted to terminal outcome (identity=)",
        )
        mpv_effect_outcome = meter.create_counter(
            "saitenka.mpv_effect.outcome",
            description="correlated command terminal outcomes (identity=, outcome=)",
        )
        subtitle_pixel_retry_exhausted = meter.create_counter(
            "saitenka.subtitle_pixels.retry_exhausted",
            description="native subtitle visibility retries exhausted without a proved owner",
        )
        prefetch_queue_depth = meter.create_up_down_counter("saitenka.prefetch.queue_depth")
        meter.create_observable_gauge(
            "saitenka.runtime.gil_enabled",
            callbacks=[_gil_enabled_callback],
            description="1 if the GIL is enabled (0 = running free-threaded)",
        )


def unregister() -> None:
    global _reader
    global render_duration_ms, upload_duration_ms, hit_test_duration_ms
    global dict_sql_duration_ms, ipc_roundtrip_ms, sub_seek_duration_ms
    global cue_redraw_duration_ms, subtitle_render_duration_ms, sub_text_reconcile_duration_ms
    global subtitle_geometry_ready_ms, subtitle_geometry_prepare_ms
    global subtitle_geometry_render_ms, subtitle_geometry_extract_ms
    global subtitle_geometry_renderer_build_ms
    global subtitle_geometry_active_events
    global subtitle_geometry_eligible_tokens, subtitle_geometry_skipped_tokens
    global scroll_frame_duration_ms, show_tooltip_duration_ms
    global panel_cache_hits, panel_cache_misses, panel_cache_evictions
    global dict_cache_hits, dict_cache_misses, dict_cache_evictions
    global block_rendered_px, block_cache_hits, block_cache_misses, block_cache_evictions
    global glyph_mask_hits, glyph_mask_misses, glyph_mask_evictions
    global glyph_width_hits, glyph_width_misses, glyph_width_evictions
    global bgra_memo_hits, bgra_memo_misses, precompose_hits, precompose_builds
    global dropped_telemetry_spans, cold_first_paint_overshoot, prefetch_queue_depth
    global osd_paused_draw, osd_paused_nudge, scroll_frame_jank
    global render_cache_hits, render_cache_misses, render_cache_writebacks, render_cache_evictions
    global mask_atlas_hits, mask_atlas_misses, mask_atlas_writebacks
    global crisp_swaps, crisp_stale, subtitle_geometry_failures
    global subtitle_geometry_decisions, subtitle_geometry_owner_transitions
    global subtitle_geometry_recoveries
    global subtitle_pixel_catastrophic_fallbacks, subtitle_pixel_retry_exhausted
    global lifecycle_timer_armed, lifecycle_timer_settled
    global hover_pause_claim, mpv_effect_apply_ms, mpv_effect_outcome
    global hover_route_decisions, hover_pause_release, cue_settles
    global subtitle_geometry_font_sources, subtitle_renderer_forced
    global subtitle_overprint_demotions, subtitle_overpaint_frames
    global subtitle_layout_drift_px

    with _lock:
        _reader = None
        render_duration_ms = None
        upload_duration_ms = None
        hit_test_duration_ms = None
        dict_sql_duration_ms = None
        ipc_roundtrip_ms = None
        sub_seek_duration_ms = None
        cue_redraw_duration_ms = None
        subtitle_render_duration_ms = None
        sub_text_reconcile_duration_ms = None
        subtitle_geometry_ready_ms = None
        subtitle_geometry_prepare_ms = None
        subtitle_geometry_render_ms = None
        subtitle_geometry_renderer_build_ms = None
        subtitle_geometry_extract_ms = None
        subtitle_geometry_active_events = None
        subtitle_geometry_eligible_tokens = None
        subtitle_geometry_skipped_tokens = None
        scroll_frame_duration_ms = None
        show_tooltip_duration_ms = None
        panel_cache_hits = None
        panel_cache_misses = None
        panel_cache_evictions = None
        dict_cache_hits = None
        dict_cache_misses = None
        dict_cache_evictions = None
        block_rendered_px = None
        block_cache_hits = None
        block_cache_misses = None
        block_cache_evictions = None
        glyph_mask_hits = None
        glyph_mask_misses = None
        glyph_mask_evictions = None
        glyph_width_hits = None
        glyph_width_misses = None
        glyph_width_evictions = None
        bgra_memo_hits = None
        bgra_memo_misses = None
        precompose_hits = None
        precompose_builds = None
        dropped_telemetry_spans = None
        cold_first_paint_overshoot = None
        osd_paused_draw = None
        osd_paused_nudge = None
        scroll_frame_jank = None
        render_cache_hits = None
        render_cache_misses = None
        render_cache_writebacks = None
        render_cache_evictions = None
        mask_atlas_hits = None
        mask_atlas_misses = None
        mask_atlas_writebacks = None
        crisp_swaps = None
        crisp_stale = None
        subtitle_geometry_failures = None
        subtitle_geometry_decisions = None
        subtitle_geometry_owner_transitions = None
        subtitle_geometry_recoveries = None
        subtitle_pixel_catastrophic_fallbacks = None
        subtitle_pixel_retry_exhausted = None
        lifecycle_timer_armed = None
        lifecycle_timer_settled = None
        hover_route_decisions = None
        hover_pause_release = None
        hover_pause_claim = None
        cue_settles = None
        subtitle_geometry_font_sources = None
        subtitle_renderer_forced = None
        subtitle_overprint_demotions = None
        subtitle_overpaint_frames = None
        subtitle_layout_drift_px = None
        mpv_effect_apply_ms = None
        mpv_effect_outcome = None
        prefetch_queue_depth = None


def _percentiles(
    bucket_counts, explicit_bounds, dp_max, total, ps=(0.5, 0.95, 0.99)
) -> dict[str, float | None]:
    """Linear-interpolation-free percentile estimate: the bound of the first bucket whose
    cumulative count reaches ``p * total``. Coarser than true percentiles (bounded by the
    histogram's bucket boundaries), but matches what a live inspector needs — a ballpark, not a
    forensic replay."""
    if total == 0:
        return {f"p{int(p * 100)}": None for p in ps}
    bounds = [*explicit_bounds, dp_max]
    remaining = {p: p * total for p in ps}
    result: dict[str, float | None] = {}
    cum = 0
    for count, bound in zip(bucket_counts, bounds, strict=True):
        cum += count
        for p in list(remaining):
            if cum >= remaining[p]:
                result[f"p{int(p * 100)}"] = bound
                del remaining[p]
    for p in remaining:
        result[f"p{int(p * 100)}"] = dp_max
    return result


def snapshot() -> dict[str, dict[str, object]]:
    """A point-in-time read of every registered instrument. ``{}`` if telemetry isn't configured.
    Histograms summarize to p50/p95/p99 + count; counters/gauges report their latest value."""
    if _reader is None:
        return {}
    data: MetricsData | None = _reader.get_metrics_data()
    if data is None:
        return {}
    out: dict[str, dict[str, object]] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out[metric.name] = _summarize_metric(metric)
    return out


def _summarize_metric(metric) -> dict[str, object]:
    """Merge every data point into one summary — an instrument recorded with per-label attributes
    (e.g. `dict_sql_duration_ms`'s `dict=<title>`) fans out into one point PER label, not one point
    total. A previous version read only `points[-1]` on the (false, for labeled instruments)
    assumption of a single track, silently reporting one label's count/sum instead of the sum across
    all of them — found via cross-checking this snapshot against the CTF trace.json span totals for
    the same run (~9-11x undercount, one point per dictionary title)."""
    data = metric.data
    points = getattr(data, "data_points", [])
    if not points:
        return {}
    if hasattr(points[0], "bucket_counts"):  # HistogramDataPoint(s) — same bucket bounds per metric
        count = sum(p.count for p in points)
        total = sum(p.sum for p in points)
        dp_max = max(p.max for p in points)
        bucket_counts = [sum(b) for b in zip(*(p.bucket_counts for p in points), strict=True)]
        return {
            "count": count,
            "sum": total,
            "max": dp_max,  # exact recorded max (the percentiles are bucket-bound estimates)
            **_percentiles(bucket_counts, points[0].explicit_bounds, dp_max, count),
            # Count and exact max per label, no percentiles: merging identities would say "some
            # correlated command took six seconds" when the whole question is which one, and the
            # exact max is the discriminator — a bucket-bound percentile is not.
            "by": {_label_key(p): {"count": p.count, "max": p.max} for p in points if p.attributes},
        }
    return {"value": sum(p.value for p in points), "by": _by_label(points)}


def _label_key(point) -> str:
    attributes = point.attributes or {}
    return ",".join(f"{k}={attributes[k]}" for k in sorted(attributes))


def _by_label(points) -> dict[str, float]:
    """Each label combination's own value, beside the total.

    The total alone cannot answer what a labeled counter was added for. `lifecycle_timer.armed`
    summed over every kind says some deadline was scheduled; the question is whether
    `kind=tooltip-hide` ever was, and merging erases exactly the axis that discriminates. Empty for
    an unlabeled counter, so its series is unchanged.
    """
    out: dict[str, float] = {}
    for point in points:
        attributes = point.attributes or {}
        if not attributes:
            continue
        key = ",".join(f"{k}={attributes[k]}" for k in sorted(attributes))
        out[key] = out.get(key, 0.0) + float(point.value)
    return out
