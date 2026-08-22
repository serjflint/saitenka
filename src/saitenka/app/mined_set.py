"""The in-deck expressions, and the generation that says when they last changed."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class MinedSet:
    """One object, because membership and its generation are one fact.

    Every panel cached against this set keys on the generation, so a set that can be mutated
    without bumping it renders a stale ⊕. Writes go through `add`/`update`, which answer whether
    membership actually moved; there is no setter for the generation, because a caller that can
    report a change can report one that did not happen.

    Locked because it replaced a plain `set`, whose `frozenset(...)` copy took an internally
    protected C-level path. Reading it through this class does not, so under free threading a copy
    taken while another thread mines would see the set resize under the iterator. Every writer is on
    the event thread today; the lock is what keeps that from being a precondition of correctness.
    """

    __slots__ = ("_expressions", "_generation", "_lock")

    def __init__(self, expressions: Iterable[str] = ()) -> None:
        self._expressions: set[str] = set(expressions)
        self._generation = 0
        self._lock = threading.Lock()

    @property
    def generation(self) -> int:
        return self._generation

    def add(self, expression: str) -> bool:
        """Record one expression as in-deck. True when it was not already."""
        return self.update((expression,))

    def update(self, expressions: Iterable[str]) -> bool:
        """Record many. True when at least one was new."""
        with self._lock:
            before = len(self._expressions)
            self._expressions.update(expressions)
            changed = len(self._expressions) != before
            self._generation += int(changed)
            return changed

    def snapshot(self) -> frozenset[str]:
        """Membership as a value, copied under the lock — what a reader should hold, not the set."""
        with self._lock:
            return frozenset(self._expressions)

    def __contains__(self, expression: object) -> bool:
        return expression in self._expressions

    def __iter__(self) -> Iterator[str]:
        return iter(self.snapshot())

    def __len__(self) -> int:
        return len(self._expressions)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MinedSet):
            return self._expressions == other._expressions
        if isinstance(other, frozenset | set):
            return self._expressions == other
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]  # mutable, like the set it replaces

    def __repr__(self) -> str:
        return f"MinedSet({sorted(self._expressions)!r}, generation={self._generation})"
