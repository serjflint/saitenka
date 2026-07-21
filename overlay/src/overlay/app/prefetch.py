"""Background-prefetch data model: typed queue items instead of bare tuples.

The prefetch machinery itself (queues, workers, generation counter) lives on the Reader; these are
the messages that flow through it. Frozen dataclasses so a line change can never make a worker read
mutated state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay.app.tokenize import Token

if TYPE_CHECKING:
    from overlay.app.popups import TipPanel


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
