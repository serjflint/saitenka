"""The in-deck expressions, and the generation that says when they last changed."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class MinedSet:
    """One object, because membership and its generation are one fact.

    Every panel cached against this set keys on the generation, so a set that can be mutated
    without bumping it renders a stale ⊕. They used to live apart — the set a `SessionContext`
    field, the counter a bare `Reader` attribute — and each of the three writers re-derived
    "did anything change?" for itself, one of them wrongly (a re-mine of a word already in the
    deck bumped the generation and invalidated every panel for nothing).

    Writes go through `add`/`update`, which answer whether membership actually moved. There is no
    setter for the generation: it is derived, not reported.
    """

    __slots__ = ("_expressions", "_generation")

    def __init__(self, expressions: Iterable[str] = ()) -> None:
        self._expressions: set[str] = set(expressions)
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def add(self, expression: str) -> bool:
        """Record one expression as in-deck. True when it was not already."""
        return self.update((expression,))

    def update(self, expressions: Iterable[str]) -> bool:
        """Record many. True when at least one was new."""
        before = len(self._expressions)
        self._expressions.update(expressions)
        changed = len(self._expressions) != before
        self._generation += int(changed)
        return changed

    def __contains__(self, expression: object) -> bool:
        return expression in self._expressions

    def __iter__(self) -> Iterator[str]:
        return iter(self._expressions)

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
