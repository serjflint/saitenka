"""Background prefetch: warm the paused/hovered line's tooltips ahead of time.

Typed queue items (frozen dataclasses, so a line change can never make a worker read mutated
state) plus the worker-thread functions themselves. These take ``reader: Reader`` (the Reader still
owns the queues/threads/generation counter as instance state — this module is the logic, not a new
owner) and are called from thin delegating methods on :class:`~overlay.app.controller.Reader`.
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
from overlay.app.tokenize import SKIP_POS, Token

if TYPE_CHECKING:
    from overlay.app.controller import Reader
    from overlay.app.popups import TipPanel

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PrefetchItem:
    """One speculative panel-warm job: render ``token``'s FULL panel in the background.

    ``gen`` is the prefetch generation at enqueue time — a line change / resume / seek bumps the
    Reader's counter, so stale items are dropped by the worker. ``mined`` is evaluated on the MAIN
    thread (card_for → jamdict is not worker-safe) and selects the ⊕/✓ header variant."""

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
    # GIL-free (3.14t + PYTHON_GIL=0): Pillow render scales ~linearly → use more workers (measured
    # ~3.8× on 4 cores). Standard GIL build: extra workers just contend, so keep the configured count.
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


def prefetch_worker(reader: Reader) -> None:
    while not reader._stop.is_set():
        # Priority: finish the deferred tail of the tooltip the user is looking at RIGHT NOW,
        # ahead of speculatively warming the rest of the line.
        try:
            fin: FinishItem | None = reader._finish_q.get_nowait()
        except queue.Empty:
            fin = None
        if fin is not None:
            try:
                fin.panel.finish()
            except Exception:
                log.debug("finish job failed", exc_info=True)
            else:
                if fin.key == reader._tip_key and fin.panel is reader._tip_state:
                    reader._tip_dirty = True  # main loop re-uploads the now-complete panel
                elif fin.key == reader._nest.key and fin.panel is reader._nest.state:
                    reader._nest.dirty = True
            continue
        try:
            item: PrefetchItem = reader._prefetch_q.get(timeout=0.2)
        except queue.Empty:
            continue
        if otel_metrics.prefetch_queue_depth is not None:
            otel_metrics.prefetch_queue_depth.add(-1)
        if reader._stop.is_set() or item.gen != reader._prefetch_gen:
            continue  # cancelled (line changed / resumed / seek / closing)
        try:
            # item.mined came from the main thread — never call _is_mined/card_for from a
            # worker (jamdict is not thread-safe on free-threaded builds).
            reader._panel_for(item.token, item.inflected, finish=True, mined=item.mined)
        except Exception:
            log.debug(
                "prefetch render failed for %r", item.token.surface, exc_info=True
            )  # a bad word must never kill the worker


def update_prefetch(reader: Reader) -> None:
    """Queue the current line's content words for background rendering when the user is *engaged*
    — paused OR the cursor is over the video (you rarely move the mouse without intent to hover).
    N+1 words go first (likeliest hover / mine target). On any change (resume, mouse-out, seek,
    new line) bump the generation so in-flight renders are dropped. Tokens are passed by value
    (frozen), so a line change can't make a worker read stale state."""
    if not reader.prefetch or reader.dict_set is None:
        return
    engaged = bool(reader._prop("pause")) or reader._mouse_in
    key = (reader.sub_text, engaged)
    if key == reader._prefetch_key:
        return
    reader._prefetch_key = key
    reader._prefetch_gen += 1  # invalidate anything queued/in-flight for the old state
    if engaged and reader.tokens:
        gen, seen, items = reader._prefetch_gen, set(), []
        for i, t in enumerate(reader.tokens):
            if t.pos in SKIP_POS or not t.is_content or t.lemma in seen:
                continue
            seen.add(t.lemma)
            np1 = bool(
                reader.styles and i < len(reader.styles) and reader.styles[i].tag.startswith("n+1")
            )
            items.append((0 if np1 else 1, i, t))
        items.sort(key=lambda x: x[0])  # N+1 first
        for _, i, t in items:
            # Evaluate _is_mined on the main thread (card_for → jamdict must not be called
            # from a worker thread — jamdict is not thread-safe on free-threaded builds).
            reader._prefetch_q.put(
                PrefetchItem(gen, t, reader._inflected_surface(i), reader._is_mined(t))
            )
            if otel_metrics.prefetch_queue_depth is not None:
                otel_metrics.prefetch_queue_depth.add(1)


def cap_for(reader: Reader, frac: float) -> int:
    """A viewport-height cap: ``frac`` of the video, but always clear of the header/footer margin."""
    margin = max(16, round(reader.osd[1] * 0.05))
    return min(round(reader.osd[1] * frac), reader.osd[1] - 2 * margin)


def tip_cap(reader: Reader) -> int:
    """Max BASE tooltip viewport height (≤ ``tip_max_frac`` of the video). The nested popup has its
    own, deliberately roomier cap (``nested_max_frac``) so shrinking the base doesn't cramp it."""
    return cap_for(reader, reader.tip_max_frac)
