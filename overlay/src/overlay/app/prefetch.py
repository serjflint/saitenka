"""Background prefetch: warm the current/next line's tooltips ahead of the mouse.

Typed queue items (frozen dataclasses, so a line change can never make a worker read mutated state)
plus the worker-thread + enqueue logic itself. The functions take ``reader: Reader`` — the Reader
still owns the queues/threads/generation counter as instance state; this module is the logic, not a
new owner — and are called from thin delegating methods on
:class:`~overlay.app.controller.Reader`.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay import otel_metrics
from overlay.app.perf import gil_disabled

if TYPE_CHECKING:
    from overlay.app.controller import Reader
    from overlay.app.popups import Panel
    from overlay.app.sub_index import SubIndex
    from overlay.app.tokenize import Token

log = logging.getLogger(__name__)

# Auto (``[perf].prefetch_workers = 0``) worker counts — flat, sane per-build defaults rather than a
# cpu-count formula: render parallelism plateaus by ~4 (see the render-parallelism notes) and each
# worker costs real RAM, so a bigger number buys idle-warming coverage we almost never need. Override
# with a positive ``prefetch_workers`` to pin/cap it.
_AUTO_WORKERS_FREE_THREADED = (
    4  # free-threaded: renders in parallel, 4 pairs with the render pool width
)
_AUTO_WORKERS_GIL = 2  # a GIL build can't render in parallel — extra workers only contend


@dataclass(frozen=True, slots=True)
class PrefetchItem:
    """One speculative background job for ``token``: either a FULL panel render (``full=True``,
    when the user is *engaged* — paused or hovering the video, a hover is imminent) or a cheap
    dict-only WARM (``full=False``): just decode+cache each dictionary's glossary for this word,
    skipping layout/drawing entirely. Warm jobs run for every new subtitle line regardless of
    engagement — genuinely idle CPU time while the line is only being watched/listened to, not yet
    looked at — so that the JSON-decode cost (the single biggest cost in a `--stress` profile, see
    ``Dictionary._entry_cache``) is usually already paid by the time a hover actually happens.

    ``gen`` is the prefetch generation at enqueue time — a line change / resume / seek bumps the
    Reader's counter, so stale items are dropped by the worker. ``mined`` is evaluated on the MAIN
    thread (card_for → jamdict is not worker-safe) and selects the ⊕/✓ header variant (unused by a
    warm job, which never builds a header)."""

    gen: int
    token: Token
    inflected: str
    mined: bool
    full: bool = True


@dataclass(frozen=True, slots=True)
class HeadPrefetchItem:
    """A selective speculative HEAD render for an upcoming word: the SAME
    viewport-capped head a real hover would render (``panel_for(..., finish=False)``), not a whole
    ``finish()``, and not a separate cache — it's written straight into ``reader._panel_cache`` via
    the exact path a real hover reads, so a later hover is a plain cache hit with no promotion/
    key-matching logic to get wrong. Only enqueued for words the priority function judges worth the
    extra render cost over plain decode-warming (see ``_head_priority``); selectivity is the actual
    RAM/CPU cap, not just the queue's ``maxsize``. ``mined`` is resolved on the main thread (jamdict is
    not worker-safe), same contract as :class:`PrefetchItem`."""

    gen: int
    token: Token
    inflected: str
    mined: bool


@dataclass(frozen=True, slots=True)
class RenderAheadReq:
    """The latest scroll-ahead warm for the on-screen tooltip: render the blocks just beyond the
    viewport in the scroll ``direction`` (+1 down / -1 up) off the main thread. Held in a single slot
    on the Reader (newest scroll wins — only the current position matters), not a queue. ``gen`` is the
    prefetch generation at request time, so a word switch / seek drops a stale request."""

    gen: int
    panel: Panel
    scroll: int
    view_h: int
    direction: int


@dataclass(frozen=True, slots=True)
class EngagedHoverReq:
    """A TOP-priority head render for the word the user is engaging RIGHT NOW that missed the panel cache
    (a far-seek / worker-hasn't-caught-up cold miss). The worker composes it off the main thread, then
    hands ``(gen, key, nested, tail)`` back on ``engaged_results`` so the tick can SHOW the now-warm
    tooltip — IFF the user is still on the same target (``gen`` + ``key`` guard). Newest-wins single slot:
    you can only look at one word.

    ``nested`` targets the popup: the base tooltip (``False``) — the worker seeds tier-2 + disk and the
    re-show is a warm cache hit — or the nested scan popup (``True``), where the worker instead warms the
    nested-cap viewport BANDS (the getmask2 raster #293 moved off the base hover) but does NOT persist to
    disk: the nested viewport is nested-cap-shaped, not the base-cap head the render cache keys share, so a
    write would collide. ``tail`` (nested only) is the scan-cell text the tick re-derives the anchor from."""

    gen: int
    token: Token
    inflected: str
    mined: bool
    key: tuple
    cap: int
    phrase: tuple = ()  # stacked-phrase terms — the worker MUST build under the same _panel_cache key
    #  the main thread will re-look-up, or the deferred re-show stays cold and re-defers in a loop
    nested: bool = False  # target overlay: base tooltip (False) or the nested scan popup (True)
    tail: str = ""  # nested only: the scan-cell tail the tick re-derives the fresh anchor from


@dataclass(frozen=True, slots=True)
class EngagedNavReq:
    """A clicked cross-reference: replace the base tooltip's content with ``query`` IN PLACE. Building +
    rastering the navigated panel's first viewport on the click tick is a synchronous getmask2 raster
    (``tip_compose[clicked]``); this defers it — the worker builds and warms the bands, the tick swaps
    from warm bands (a cheap assemble, no raster). ``origin`` is ``id(reader._tip_state)`` at click time,
    so the tick skips the swap if the tooltip changed under it (a word switch) rather than hijacking the
    new one. Newest-wins single slot: one navigation intent at a time."""

    gen: int
    query: str
    origin: int


class PrefetchState:
    """Runtime state of the background prefetch subsystem: the decode-warm and speculative-head work
    queues, the persistent worker threads, the generation counter whose bump drops every in-flight job
    on a line change / resume / seek, and the single-slot scroll-ahead + engaged-hover requests. Grouped
    so the Reader owns prefetch as one unit and a #100 re-slot can invalidate it in one call (``cancel()``)."""

    def __init__(self, head_queue_max: int) -> None:
        self.q: queue.Queue = queue.Queue()  # decode-warm + engaged-head jobs (FIFO)
        # speculative head-renders for upcoming words; maxsize is the transient-RSS cap
        self.head_q: queue.PriorityQueue = queue.PriorityQueue(maxsize=head_queue_max)
        self.head_seq = 0  # tie-breaker so priority-queue items never compare HeadPrefetchItems
        self.head_built = 0  # a speculative head-render job actually ran to completion
        self.gen = 0  # bumped on line change / resume / seek → cancels in-flight (see cancel())
        self.key: tuple[str, bool] | None = (
            None  # last (sub_text, engaged) queued — dedupes re-runs
        )
        # Scroll-ahead: a single slot (newest scroll wins) the worker drains; guarded by its own lock.
        self.render_ahead_req: RenderAheadReq | None = None
        self.render_ahead_lock = threading.Lock()
        # Engaged-hover (tier-3 cold-miss deferred render): a newest-wins slot the worker drains at TOP
        # priority + a results queue it hands the composed head back on for the tick to apply.
        self.engaged_req: EngagedHoverReq | None = None
        self.engaged_lock = threading.Lock()
        self.engaged_results: queue.Queue = queue.Queue()
        # Clicked cross-ref navigation (tier-3 for the in-place nav): its own newest-wins slot + results,
        # so a nav intent and a hover cold-miss don't clobber each other's slot.
        self.nav_req: EngagedNavReq | None = None
        self.nav_lock = threading.Lock()
        self.nav_results: queue.Queue = queue.Queue()
        self.threads: list[threading.Thread] = []  # persistent workers (session-lifetime)

    def cancel(self) -> int:
        """Invalidate everything queued/in-flight for the old state by bumping the generation; the
        worker drops any item whose ``gen`` no longer matches. Returns the new generation for the
        items about to be enqueued."""
        self.gen += 1
        with self.engaged_lock:  # drop a pending cold-miss render for the abandoned word
            self.engaged_req = None
        with self.nav_lock:  # drop a pending cross-ref navigation too
            self.nav_req = None
        return self.gen


def prefetch_worker_count(reader: Reader) -> int:
    """An explicit ``[perf].prefetch_workers`` (>0) pins the count on both builds; ``0`` (auto) picks a
    flat per-build default (``_AUTO_WORKERS_FREE_THREADED`` / ``_AUTO_WORKERS_GIL``). Each worker is
    PERSISTENT and carries real per-thread RAM (SQLite page cache + FreeType faces + the free-threaded
    allocator arena), so this is primarily a RAM/coverage knob — see PerfOptions.

    ``gil_disabled()`` is only trustworthy AFTER fugashi has loaded — it wraps a C extension that
    hasn't declared free-threaded safety and silently re-enables the GIL on first use, not at import
    (``tokenize.py``). This is called from ``start_prefetch()`` at the very top of ``run()``, before
    any subtitle line has ever been tokenized, so without this warm-up it always sees the pre-fugashi
    state — spawning the free-threaded worker count on a build that loses the GIL moments later anyway,
    paying the allocator's per-thread-arena memory tax (see ``vibe/hot-path-idle-spreading-plan.md``)
    for parallelism that never actually happens. Force the load now (its one-time cost lands at startup
    instead of on the first real subtitle line either way) so this reflects the GIL state that will
    actually hold for the session."""
    reader.tokenizer.tokenize("")
    if reader.prefetch_workers > 0:  # explicit [perf].prefetch_workers pins it on BOTH builds
        return reader.prefetch_workers
    return _AUTO_WORKERS_FREE_THREADED if gil_disabled() else _AUTO_WORKERS_GIL


def start_prefetch(reader: Reader) -> None:
    if not reader.prefetch or reader.dict_set is None or reader._prefetch_threads:
        return
    for k in range(prefetch_worker_count(reader)):
        th = threading.Thread(
            target=lambda: prefetch_worker(reader), name=f"saitenka-prefetch-{k}", daemon=True
        )
        th.start()
        reader._prefetch_threads.append(th)


def _run_item(reader: Reader, item: PrefetchItem) -> None:
    """A viewport-first HEAD render when the user's engaged (a hover is imminent), else a cheap
    dict-only WARM (decode+cache the glossary into ``Dictionary._entry_cache``, no layout) so a
    later hover/render skips the decode."""
    # Idle-warming span: how much idle budget the worker actually spends warming upcoming words —
    # the live counterpart to what --timeline measures out-of-band (worker keep-ahead margin).
    with otel_metrics.traced("prefetch_decode", kind="head" if item.full else "warm"):
        if item.full:
            # Head-first, NOT the whole panel: the windowed engine composites the tail on scroll, so
            # pre-rendering the full body of every engaged content word just saturates the worker on
            # pathological entries. Same viewport cap + panel_for()/cache path a hover uses, so the
            # warmed head is a hit. item.mined came from the main thread — never call _is_mined/card_for
            # from a worker (jamdict is not thread-safe on free-threaded builds).
            cap = reader._tip_cap()
            st = reader._panel_for(item.token, item.inflected, min_h=cap, mined=item.mined)
            # Seed from disk FIRST (inflate on the worker, ~10× cheaper than a raster) into the panel +
            # tier-2 RAM; only compose+persist on a genuine miss — so a prewarmed head is a cheap inflate
            # here, and a fresh raster still write-backs to disk (#149) and mirrors into tier-2.
            if not reader._worker_seed_head(
                st, item.token, item.inflected, mined=item.mined, cap=cap
            ):
                reader._precompose_head(st, item.token, item.inflected, mined=item.mined, cap=cap)
                reader._mem_fill(item.token, item.inflected, mined=item.mined)
        elif reader.dict_set is not None:  # None only if dicts were torn down mid-flight
            reader.dict_set.entry_for(item.token, item.inflected)


def _try_prefetch_item(reader: Reader) -> None:
    try:
        item: PrefetchItem = reader._prefetch_q.get(timeout=0.2)
    except queue.Empty:
        return
    if otel_metrics.prefetch_queue_depth is not None:
        otel_metrics.prefetch_queue_depth.add(-1)
    if reader._stop.is_set() or item.gen != reader._prefetch_gen:
        return  # cancelled (line changed / resumed / seek / closing)
    try:
        _run_item(reader, item)
    except Exception:
        log.debug(
            "prefetch render failed for %r", item.token.surface, exc_info=True
        )  # a bad word must never kill the worker


def _try_head_prefetch_item(reader: Reader) -> bool:
    """One queued speculative head-render, if any; ``True`` when handled (so the
    worker loops before the plain decode-warm queue: a render job is worth doing ahead of a cheap
    decode, but never outranks a real :class:`FinishItem` for the on-screen tooltip)."""
    try:
        _priority, _seq, item = reader._head_prefetch_q.get_nowait()
    except queue.Empty:
        return False
    if reader._stop.is_set() or item.gen != reader._prefetch_gen:
        return True  # cancelled (line changed / resumed / seek) — still "handled", keep looping
    try:
        # Same viewport cap + same panel_for()/panel_cache path a real hover uses — written into the
        # SAME cache, so a later hover is an ordinary cache hit (see HeadPrefetchItem docstring).
        # kind="head_ahead" distinguishes the speculative lookahead render from the engaged current
        # line's kind="head" — without a span here it was invisible, folding into anonymous `render`.
        with otel_metrics.traced("prefetch_decode", kind="head_ahead"):
            cap = reader._tip_cap()
            st = reader._panel_for(item.token, item.inflected, min_h=cap, mined=item.mined)
            # Disk-seed first (cheap inflate → panel + tier-2 RAM); compose+persist only on a miss.
            if not reader._worker_seed_head(
                st, item.token, item.inflected, mined=item.mined, cap=cap
            ):
                reader._precompose_head(st, item.token, item.inflected, mined=item.mined, cap=cap)
                reader._mem_fill(item.token, item.inflected, mined=item.mined)
        reader._head_built += 1
    except Exception:
        log.debug(
            "head prefetch render failed for %r", item.token.surface, exc_info=True
        )  # a bad/pathological word must never kill the worker
    return True


def prefetch_worker(reader: Reader) -> None:
    while not reader._stop.is_set():
        if _try_engaged_nav(reader):  # a clicked cross-ref — top intent, like a hover cold-miss
            continue
        if _try_engaged_hover(reader):  # the word hovered NOW that missed everything — top priority
            continue
        if _try_render_ahead(reader):  # on-screen scroll warm next
            continue
        if _try_head_prefetch_item(reader):
            continue
        _try_prefetch_item(reader)


def workers_running(reader: Reader) -> bool:
    """True when at least one prefetch worker thread has been started to service background work (the
    engaged cold-miss compose). False → prefetch off / not started, so a cold hover must build
    synchronously rather than defer to a worker that will never drain the queue."""
    return bool(reader._prefetch_threads)


def request_engaged_render(
    reader: Reader,
    token,
    inflected,
    key,
    *,
    mined: bool,
    phrase: tuple = (),
    nested: bool = False,
    tail: str = "",
) -> None:
    """Enqueue a TOP-priority head render for a word the user is engaging that missed the panel cache
    (tier-3 cold miss). ``nested`` targets the scan popup instead of the base tooltip; ``tail`` is its
    scan-cell text (the tick re-derives the anchor from it). Newest-wins (only the current target
    matters). Main-thread + cheap: stores the request; a worker composes it (nothing is shown until it
    lands). No-op when prefetch is off."""
    if not reader.prefetch:
        return
    req = EngagedHoverReq(
        reader._prefetch_gen,
        token,
        inflected,
        mined,
        tuple(key),
        reader._tip_cap(),
        tuple(phrase),
        nested,
        tail,
    )
    with reader._engaged_lock:
        reader._engaged_req = req


def _try_engaged_hover(reader: Reader) -> bool:
    """Drain the engaged-hover slot: compose the cold-missed head off the main thread, then signal
    ``(gen, key, nested, tail)`` on ``engaged_results`` so the tick SHOWS the now-warm popup. Builds under
    the SAME ``extra_terms`` the main-thread lookup uses, so the re-show is a panel-cache hit (never a
    re-defer). ``True`` when handled (so the worker re-checks it before the cheaper queues)."""
    with reader._engaged_lock:
        req = reader._engaged_req
        reader._engaged_req = None
    if req is None:
        return False
    if reader._stop.is_set() or req.gen != reader._prefetch_gen:
        return True  # stale (word changed / seek / closing) — handled, keep looping
    try:
        with otel_metrics.traced(
            "prefetch_decode", kind="engaged_nested" if req.nested else "engaged"
        ):
            _compose_engaged_nested(reader, req) if req.nested else _compose_engaged_base(
                reader, req
            )
        reader._engaged_results.put((req.gen, req.key, req.nested, req.tail))
    except Exception:
        log.debug("engaged hover render failed for %r", req.token.surface, exc_info=True)
    return True


def _compose_engaged_base(reader: Reader, req: EngagedHoverReq) -> None:
    """Base tooltip cold miss: build the head (seeding panel cache + tier-2 + disk via the same
    seed/compose path a prefetch uses), so the tick's re-show is a warm cache hit that re-blits soft then
    upgrades to crisp via the poll loop (``apply_pending_crisp``)."""
    st = reader._panel_for(
        req.token, req.inflected, min_h=req.cap, mined=req.mined, extra_terms=req.phrase
    )
    if not reader._worker_seed_head(st, req.token, req.inflected, mined=req.mined, cap=req.cap):
        reader._precompose_head(st, req.token, req.inflected, mined=req.mined, cap=req.cap)
        reader._mem_fill(req.token, req.inflected, mined=req.mined)


def _compose_engaged_nested(reader: Reader, req: EngagedHoverReq) -> None:
    """Nested scan popup cold miss: build the panel then WARM the nested-cap viewport bands off the main
    thread — the getmask2 raster #293 removed from the base hover — so the tick's re-show composites from
    warm bands with no synchronous raster. Warms native (crisp) AND raw (soft) so the re-show is crisp at
    hi-dpi with no soft-then-poll flicker. NO disk persist: the nested viewport is nested-cap-shaped, not
    the base-cap head the render cache keys share, so a write would collide with the base head."""
    st = reader._panel_for(
        req.token,
        req.inflected,
        min_h=req.cap,
        mined=req.mined,
        nested=True,
        extra_terms=req.phrase,
    )
    vh = min(st.full_height, reader._cap_for(reader.nested_max_frac))
    if vh <= 0:
        return
    scale = reader._raster_scale
    if scale > 1.0:
        st.viewport(0, vh, overscan=vh, scale=scale)  # native bands → crisp compose on the re-show
    st.viewport(
        0, vh, overscan=vh
    )  # raw bands → the soft/assemble path (1080p, or a crisp fallback)


def drain_engaged_results(reader: Reader):
    """Yield ``(gen, key, nested, tail)`` for each composed engaged head the worker handed back this tick.
    The show logic (generation + key guard, then the warm re-show — base tooltip or nested scan popup)
    lives in ``tooltip.apply_engaged_results`` — it needs panel_key/is_mined/show_tooltip/show_nested."""
    while True:
        try:
            yield reader._engaged_results.get_nowait()
        except queue.Empty:
            return


def request_engaged_nav(reader: Reader, query: str) -> None:
    """Enqueue a clicked cross-reference navigation (tier-3): the worker builds + warms the navigated
    panel off the main thread, the tick swaps it in from warm bands. ``origin`` pins the tooltip that was
    showing at click time so a word switch can't be hijacked. Newest-wins. No-op when prefetch is off."""
    if not reader.prefetch:
        return
    req = EngagedNavReq(reader._prefetch_gen, query, id(reader._tip_state))
    with reader._nav_lock:
        reader._nav_req = req


def _try_engaged_nav(reader: Reader) -> bool:
    """Drain the nav slot: build the navigated panel and WARM its first-viewport bands off the main thread
    (native + raw), then hand ``(gen, origin, panel)`` back so the tick swaps it in with no raster. ``True``
    when handled (so the worker re-checks it before the cheaper queues)."""
    with reader._nav_lock:
        req = reader._nav_req
        reader._nav_req = None
    if req is None:
        return False
    if reader._stop.is_set() or req.gen != reader._prefetch_gen:
        return True  # stale (line change / seek / closing) — handled, keep looping
    try:
        with otel_metrics.traced("prefetch_decode", kind="engaged_nav"):
            st = reader._navigated_panel(req.query)
            if st is not None:
                st.render_head(reader._tip_cap())
                vh = min(st.full_height, reader._tip_cap())
                if vh > 0:
                    scale = reader._raster_scale
                    if scale > 1.0:
                        st.viewport(0, vh, overscan=vh, scale=scale)  # native bands (crisp swap)
                    st.viewport(0, vh, overscan=vh)  # raw bands (soft/assemble path)
                reader._nav_results.put((req.gen, req.origin, st))
    except Exception:
        log.debug("engaged nav render failed for %r", req.query, exc_info=True)
    return True


def drain_nav_results(reader: Reader):
    """Yield ``(gen, origin, panel)`` for each worker-built navigated panel. The swap (nav-stack push +
    state set + blit) lives in ``tooltip.apply_engaged_results`` — it needs the tip-view capture."""
    while True:
        try:
            yield reader._nav_results.get_nowait()
        except queue.Empty:
            return


def request_render_ahead(reader: Reader, direction: int) -> None:
    """Record a scroll-ahead warm for the current tooltip in ``direction`` (newest wins). Main-thread
    and cheap: just stores the request; a worker does the render. No-op when prefetch is off or no
    tooltip is up."""
    st = reader._tip_state
    if st is None or not reader.prefetch:
        return
    req = RenderAheadReq(
        reader._prefetch_gen, st, reader._tip_scroll, reader._tip_view_h, direction
    )
    with reader._render_ahead_lock:
        reader._render_ahead_req = req


def _try_render_ahead(reader: Reader) -> bool:
    """Drain the scroll-ahead slot and warm the next blocks off the main thread. ``True`` when a
    request was handled (so the worker re-checks the on-screen warm before the cheaper decode queue)."""
    with reader._render_ahead_lock:
        req = reader._render_ahead_req
        reader._render_ahead_req = None
    if req is None:
        return False
    if reader._stop.is_set() or req.gen != reader._prefetch_gen:
        return True  # stale (word changed / seek / closing) — handled, keep looping
    try:
        scale = reader._raster_scale
        cancel = lambda: reader._stop.is_set() or req.gen != reader._prefetch_gen  # noqa: E731
        # One-panel crisp: the main thread painted SOFT and never rasters. Warm the CURRENT native
        # viewport here (worker) so the poll loop can upgrade it to crisp, then warm the next screen ahead.
        if scale > 1.0:
            req.panel.viewport(
                req.scroll, req.view_h, scale=scale
            )  # render+cache the visible native bands
        req.panel.render_ahead(
            req.scroll, req.view_h, direction=req.direction, should_cancel=cancel, scale=scale
        )
    except Exception:
        log.debug("render-ahead failed", exc_info=True)  # a bad block must never kill the worker
    return True


def _enqueue(reader: Reader, item: PrefetchItem) -> None:
    reader._prefetch_q.put(item)
    if otel_metrics.prefetch_queue_depth is not None:
        otel_metrics.prefetch_queue_depth.add(1)


def _candidates(reader: Reader) -> list[tuple[int, int, Token]]:
    """This line's content words worth warming, N+1 first (likeliest hover / mine target),
    de-duplicated by lemma. Each entry is ``(priority, token_index, token)``."""
    seen: set[str] = set()
    items: list[tuple[int, int, Token]] = []
    for i, t in enumerate(reader.tokens):
        if not reader.tokenizer.is_content(t) or t.lemma in seen:
            continue
        seen.add(t.lemma)
        np1 = bool(
            reader.styles and i < len(reader.styles) and reader.styles[i].tag.startswith("n+1")
        )
        items.append((0 if np1 else 1, i, t))
    items.sort(key=lambda x: x[0])
    return items


def update_prefetch(reader: Reader) -> None:
    """Queue the current line's content words for background work every time the line (or engagement)
    changes — *engaged* (paused OR the cursor over the video) gets a viewport-first HEAD render (a
    hover is imminent); otherwise a cheap dict-only WARM (``full=False``): the video is just playing, but
    that's exactly the idle time to pay the JSON-decode cost. N+1 words go first. On any change bump
    the generation so in-flight renders are dropped; tokens pass by value (frozen), so a line change
    can't make a worker read stale state.

    With ``prefetch_lookahead`` set, the next few cues' words are then WARMED too (dict-only) so the
    first hover after the line advances is already decoded."""
    if not reader.prefetch or reader.dict_set is None:
        return
    engaged = bool(reader._prop("pause")) or reader._mouse_in
    key = (reader.sub_text, engaged)
    if key == reader._prefetch_key:
        return
    reader._prefetch_key = key
    gen = reader.prefetch_state.cancel()  # invalidate anything queued/in-flight for the old state
    cands = _candidates(reader)
    for _, i, t in cands:
        _enqueue(
            reader, PrefetchItem(gen, t, reader._inflected_surface(i), reader._is_mined(t), engaged)
        )
    if reader.prefetch_lookahead > 0:
        _enqueue_lookahead(reader, gen, {t.lemma for _, _, t in cands})
    if reader.head_prefetch_lookahead > 0:
        _enqueue_head_prefetch(reader, gen, {t.lemma for _, _, t in cands})


def _enqueue_lookahead(reader: Reader, gen: int, seen: set[str]) -> None:
    """WARM (dict-only, never full, ``mined=False``) the content words of the next
    ``prefetch_lookahead`` cues — a future line is never *engaged* and never builds a header, so no
    main-thread jamdict/scorer work runs here. ``seen`` carries the current line's lemmas so a word
    already queued isn't warmed twice. No-op without an external sub index."""
    for text in upcoming_cue_texts(reader, reader.prefetch_lookahead):
        toks = reader.tokenizer.tokenize(text)
        for i, t in enumerate(toks):
            if not reader.tokenizer.is_content(t) or t.lemma in seen:
                continue
            seen.add(t.lemma)
            _enqueue(
                reader,
                PrefetchItem(
                    gen, t, reader.tokenizer.inflected_in(toks, i), mined=False, full=False
                ),
            )


def _head_priority(tag: str) -> int | None:
    """Lower sorts first; ``None`` means "not worth a render job at all," which is the
    real RAM/CPU cap on this feature (selectivity, not just the queue's ``maxsize``). n+1/forgotten
    (the word the system already expects to be looked up) first, then rarer frequency bands; already-
    ``known`` words are excluded outright — see :class:`overlay.app.scoring.Scorer` for the tag
    vocabulary (``'n+1' | 'known' | 'forgotten' | 'freq-N' | 'base'``, ``+'/jlpt-Nx'``).

    Known blind spot: a word absent from the frequency list entirely (arguably the RAREST case) tags
    ``'base'`` — identical to a word that's merely low-signal — so it's excluded here rather than
    risking treating an ordinary word as high-priority. A finer ranking could read
    ``Scorer.freq.rank()`` directly instead of the coloring tag string."""
    base = tag.split("/", 1)[0]
    if base in {"n+1", "forgotten"}:
        return 0
    if base.startswith("freq-"):
        band = int(base.split("-", 1)[1])
        return 1 + (5 - band)  # rarer (higher band) sorts first
    return None  # 'known' or 'base' (no strong signal) — plain decode-warming is enough


def _head_prefetch_candidate(
    reader: Reader, gen: int, toks: list[Token], i: int, t: Token, styles
) -> tuple[int, HeadPrefetchItem] | None:
    """Is token `t` (at index `i`) worth a speculative head-render? None if not — either
    :func:`_head_priority` says no, it's already mined, or it's already warm in the panel cache."""
    priority = _head_priority(styles[i].tag)
    if priority is None:
        return None
    if reader._is_mined(t):  # main thread only (jamdict) — see HeadPrefetchItem docstring
        return None
    inflected = reader.tokenizer.inflected_in(toks, i)
    key = reader._panel_key(t, inflected, mined=False)
    if key in reader._panel_cache:
        return None  # already warm (hovered earlier, or a prior speculative render)
    return priority, HeadPrefetchItem(gen, t, inflected, mined=False)


def _enqueue_head_prefetch(reader: Reader, gen: int, seen: set[str]) -> None:
    """Speculative HEAD render for a SELECTIVE subset of the next
    ``head_prefetch_lookahead`` cues' words: only ones :func:`_head_priority` judges worth the extra
    render cost over plain decode-warming, in priority order, bounded by the queue's ``maxsize`` (the
    transient-RSS cap — panel_cache's LRU only bounds RETAINED size). Needs a scorer for the n+1/
    known/freq signal; a no-op without one (or without a sub index, like ``_enqueue_lookahead``)."""
    if reader.scorer is None:
        return
    for text in upcoming_cue_texts(reader, reader.head_prefetch_lookahead):
        toks = reader.tokenizer.tokenize(text)
        styles = reader.scorer.score_line(toks)
        for i, t in enumerate(toks):
            if not reader.tokenizer.is_content(t) or t.lemma in seen:
                continue
            seen.add(t.lemma)
            candidate = _head_prefetch_candidate(reader, gen, toks, i, t, styles)
            if candidate is None:
                continue
            priority, entry = candidate
            reader._head_seq += 1
            try:
                reader._head_prefetch_q.put_nowait((priority, reader._head_seq, entry))
            except queue.Full:
                return  # at capacity — drop the rest; decode-only warming still covers them


def upcoming_cue_texts(reader: Reader, n: int) -> list[str]:
    """Text of the ``n`` cues after the one on screen, from the external sub index (empty when there's
    no index, the line isn't located, or we're at the tail). Located by the displayed text alone — the
    reliable signal per :meth:`SubIndex.locate` — so it stays off the mpv IPC path."""
    idx = reader._sub_index
    if idx is None or not len(idx) or n <= 0:
        return []
    current = idx.locate(text=reader.sub_text, preferred=reader._nav_idx)
    if current < 0:
        return []
    return [idx.cues[i].text for i in range(current + 1, min(len(idx), current + 1 + n))]


def warm_episode_tokens(reader: Reader) -> None:
    """Fire-and-forget: tokenize EVERY cue of the current sub index into ``reader.token_cache`` on a
    background thread, so no cue pays cold tokenization mid-playback (the whole episode is warm ahead
    of playback, not just a short window). Best-effort — a key mismatch (mpv re-wrapping a line) just
    re-tokenizes that cue on demand; a track switch (new index object) supersedes a stale warm. No-op
    without prefetch, a dictionary, or an index; skips an index already warmed."""
    idx = reader._sub_index
    if not reader.prefetch or reader.dict_set is None or idx is None or reader._warmed_index is idx:
        return
    reader._warmed_index = idx
    threading.Thread(
        target=lambda: _warm_episode_loop(reader, idx), name="saitenka-episode-warm", daemon=True
    ).start()


def _warm_episode_loop(reader: Reader, idx: SubIndex) -> None:
    warmed = 0
    # The generation this warm belongs to: a live profile swap (#254 D8) clears the cache and bumps it,
    # so a worker mid-tokenize with the OLD tokenizer can't put() a stale-language entry after the swap
    # — the gen is threaded into every put (dropped under the cache lock on mismatch) AND breaks the loop.
    gen = reader.token_cache.generation
    for cue in list(idx.cues):
        if (
            reader._stop.is_set()
            or reader._sub_index is not idx
            or reader.token_cache.generation != gen
        ):
            return  # closing, a track switch replaced the index, or a profile swap → drop the stale warm
        try:
            reader._tokenize_cue(reader._cue_norm(cue.text), generation=gen)
            warmed += 1
        except Exception:
            log.debug("episode token warm failed for a cue", exc_info=True)  # never kill the warm
    log.info("episode token warm: %d/%d cues into the token cache", warmed, len(idx.cues))


# The tooltip's FIXED reference resolution. Tooltip geometry (width, viewport-height cap) is computed
# against this, NOT the live OSD, so the persistent render cache is resolution-independent: a 1080p
# prewarm hits at any playback resolution, and osd_h/REF_H scales the composited bitmap to the actual
# display at upload time (Reader._tip_display_scale). The tooltip is a VIDEO-OVERLAY element that tracks
# the vertical viewport, NOT the app-chrome ui_scale (its fonts are theme scale 1.0). Matches the
# interaction goldens pinned at 1080p (scale 1.0 = the reference, unscaled).
REF_W, REF_H = 1920, 1080


def cap_for(reader: Reader, frac: float) -> int:  # noqa: ARG001 — reader kept for the call-site shape
    """A viewport-height cap: ``frac`` of the REFERENCE height, clear of the header/footer margin.
    REFERENCE-based (not the live OSD, not ui_scale) so the tooltip render cache is resolution-independent;
    the display scale (osd_h/REF_H) then maps ``frac`` onto the ACTUAL viewport at upload."""
    margin = max(16, round(REF_H * 0.05))
    return min(round(REF_H * frac), REF_H - 2 * margin)


def tip_cap(reader: Reader) -> int:
    """Max BASE tooltip viewport height (≤ ``tip_max_frac`` of the video). The nested popup has its
    own, deliberately roomier cap (``nested_max_frac``) so shrinking the base doesn't cramp it."""
    return cap_for(reader, reader.tip_max_frac)
