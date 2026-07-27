"""Background prefetch: warm the current/next line's tooltips ahead of the mouse.

Typed queue items (frozen dataclasses, so a line change can never make a worker read mutated state)
plus the worker-thread + enqueue logic itself. The functions take ``reader: Reader`` — the Reader
still owns the queues/threads/generation counter as instance state; this module is the logic, not a
new owner — and are called from thin delegating methods on
:class:`~overlay.app.controller.Reader`.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay import otel_metrics
from overlay.app.perf import gil_disabled
from overlay.app.tokenize import SKIP_POS, Token, inflected_in, tokenize

if TYPE_CHECKING:
    from overlay.app.controller import Reader
    from overlay.app.popups import TipPanel

log = logging.getLogger(__name__)


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
    """EXPERIMENTAL (prototype) — a selective speculative HEAD render for an upcoming word: the SAME
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
class FinishItem:
    """High-priority job: finish the deferred tail of the panel the user is looking at RIGHT NOW.

    ``key`` is the panel-cache key — the worker flags a refresh only if this panel is still the one
    on screen."""

    panel: TipPanel
    key: tuple


def prefetch_worker_count(reader: Reader) -> int:
    """GIL-free (3.14t + PYTHON_GIL=0): Pillow render scales ~linearly → use more workers (measured
    ~3.8x on 4 cores). Standard GIL build: extra workers just contend, so keep the configured count.

    ``gil_disabled()`` is only trustworthy AFTER fugashi has loaded — it wraps a C extension that
    hasn't declared free-threaded safety and silently re-enables the GIL on first use, not at import
    (``tokenize.py``). This is called from ``start_prefetch()`` at the very top of ``run()``, before
    any subtitle line has ever been tokenized, so without this warm-up it always sees the pre-fugashi
    state — spawning up to 8 workers on a free-threaded build that loses the GIL moments later anyway,
    paying the free-threaded allocator's per-thread-arena memory tax (see
    ``vibe/hot-path-idle-spreading-plan.md``) for parallelism that never actually happens. Force the
    load now (its one-time cost lands at startup instead of on the first real subtitle line either
    way) so this reflects the GIL state that will actually hold for the session."""
    tokenize("")
    if gil_disabled():
        return min(8, max(2, (os.cpu_count() or 4) - 2))
    return reader.prefetch_workers


def start_prefetch(reader: Reader) -> None:
    if not reader.prefetch or reader.dict_set is None or reader._prefetch_threads:
        return
    for k in range(prefetch_worker_count(reader)):
        th = threading.Thread(
            target=lambda: prefetch_worker(reader), name=f"saitenka-prefetch-{k}", daemon=True
        )
        th.start()
        reader._prefetch_threads.append(th)


def _try_finish_job(reader: Reader) -> bool:
    """Finish the deferred tail of the on-screen tooltip if one is queued; ``True`` when handled (so
    the worker loops before touching the warm queue — the visible panel outranks warming)."""
    try:
        fin: FinishItem = reader._finish_q.get_nowait()
    except queue.Empty:
        return False
    try:
        fin.panel.finish()
    except Exception:
        log.debug("finish job failed", exc_info=True)
        return True
    if fin.key == reader._tip_key and fin.panel is reader._tip_state:
        reader._tip_dirty = True  # main loop re-uploads the now-complete panel
    elif fin.key == reader._nest.key and fin.panel is reader._nest.state:
        reader._nest.dirty = True
    return True


def _run_item(reader: Reader, item: PrefetchItem) -> None:
    """A FULL panel render when the user's engaged, else a cheap dict-only WARM (decode+cache the
    glossary into ``Dictionary._entry_cache``, no layout) so a later hover/render skips the decode."""
    if item.full:
        # item.mined came from the main thread — never call _is_mined/card_for from a worker
        # (jamdict is not thread-safe on free-threaded builds).
        reader._panel_for(item.token, item.inflected, finish=True, mined=item.mined)
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
    """EXPERIMENTAL — one queued speculative head-render, if any; ``True`` when handled (so the
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
        reader._panel_for(
            item.token, item.inflected, min_h=reader._tip_cap(), finish=False, mined=item.mined
        )
        reader._head_built += 1
    except Exception:
        log.debug(
            "head prefetch render failed for %r", item.token.surface, exc_info=True
        )  # a bad/pathological word must never kill the worker
    return True


def prefetch_worker(reader: Reader) -> None:
    while not reader._stop.is_set():
        if _try_finish_job(reader):
            continue
        if _try_head_prefetch_item(reader):
            continue
        _try_prefetch_item(reader)


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
        if t.pos in SKIP_POS or not t.is_content or t.lemma in seen:
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
    changes — *engaged* (paused OR the cursor over the video) gets a FULL render (a hover is
    imminent); otherwise a cheap dict-only WARM (``full=False``): the video is just playing, but
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
    reader._prefetch_gen += 1  # invalidate anything queued/in-flight for the old state
    gen = reader._prefetch_gen
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
        toks = tokenize(text)
        for i, t in enumerate(toks):
            if t.pos in SKIP_POS or not t.is_content or t.lemma in seen:
                continue
            seen.add(t.lemma)
            _enqueue(reader, PrefetchItem(gen, t, inflected_in(toks, i), mined=False, full=False))


def _head_priority(tag: str) -> int | None:
    """EXPERIMENTAL — lower sorts first; ``None`` means "not worth a render job at all," which is the
    real RAM/CPU cap on this feature (selectivity, not just the queue's ``maxsize``). n+1/forgotten
    (the word the system already expects to be looked up) first, then rarer frequency bands; already-
    ``known`` words are excluded outright — see :class:`overlay.app.scoring.Scorer` for the tag
    vocabulary (``'n+1' | 'known' | 'forgotten' | 'freq-N' | 'base'``, ``+'/jlpt-Nx'``).

    Known blind spot: a word absent from the frequency list entirely (arguably the RAREST case) tags
    ``'base'`` — identical to a word that's merely low-signal — so it's excluded here rather than
    risking treating an ordinary word as high-priority. Good enough for a prototype; a real ranking
    would read ``Scorer.freq.rank()`` directly instead of the coloring tag string."""
    base = tag.split("/", 1)[0]
    if base in ("n+1", "forgotten"):
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
    # tabs MUST match how panel_for() itself resolves it (reader.show_dict_tabs, since
    # _try_head_prefetch_item calls panel_for without an explicit tabs=) or this dedup check
    # would miss the panel_for() build's real cache key and always look like a miss.
    key = reader._panel_key(t, inflected_in(toks, i), mined=False, tabs=reader.show_dict_tabs)
    if key in reader._panel_cache:
        return None  # already warm (hovered earlier, or a prior speculative render)
    return priority, HeadPrefetchItem(gen, t, inflected_in(toks, i), mined=False)


def _enqueue_head_prefetch(reader: Reader, gen: int, seen: set[str]) -> None:
    """EXPERIMENTAL (prototype) — speculative HEAD render for a SELECTIVE subset of the next
    ``head_prefetch_lookahead`` cues' words: only ones :func:`_head_priority` judges worth the extra
    render cost over plain decode-warming, in priority order, bounded by the queue's ``maxsize`` (the
    transient-RSS cap — panel_cache's LRU only bounds RETAINED size). Needs a scorer for the n+1/
    known/freq signal; a no-op without one (or without a sub index, like ``_enqueue_lookahead``)."""
    if reader.scorer is None:
        return
    for text in upcoming_cue_texts(reader, reader.head_prefetch_lookahead):
        toks = tokenize(text)
        styles = reader.scorer.score_line(toks)
        for i, t in enumerate(toks):
            if t.pos in SKIP_POS or not t.is_content or t.lemma in seen:
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


def cap_for(reader: Reader, frac: float) -> int:
    """A viewport-height cap: ``frac`` of the video, but always clear of the header/footer margin."""
    margin = max(16, round(reader.osd[1] * 0.05))
    return min(round(reader.osd[1] * frac), reader.osd[1] - 2 * margin)


def tip_cap(reader: Reader) -> int:
    """Max BASE tooltip viewport height (≤ ``tip_max_frac`` of the video). The nested popup has its
    own, deliberately roomier cap (``nested_max_frac``) so shrinking the base doesn't cramp it."""
    return cap_for(reader, reader.tip_max_frac)
