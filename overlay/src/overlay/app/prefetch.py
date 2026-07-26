"""Background-prefetch data model: typed queue items instead of bare tuples.

The prefetch machinery itself (queues, workers, generation counter) lives on the Reader; these are
the messages that flow through it. Frozen dataclasses so a line change can never make a worker read
mutated state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from overlay.app.popups import TipPanel
    from overlay.app.tokenize import Token


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
class FinishItem:
    """High-priority job: finish the deferred tail of the panel the user is looking at RIGHT NOW.

    ``key`` is the panel-cache key — the worker flags a refresh only if this panel is still the one
    on screen."""

    panel: TipPanel
    key: tuple
