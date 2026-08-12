"""Explicit, replaceable ordering for one reader tick."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


@dataclass(frozen=True, slots=True)
class TickStage:
    """One named synchronous phase in the interactive reader loop."""

    name: str
    run: Callable[[], None]


class TickPipeline:
    """Run named phases in assembly order, rejecting ambiguous duplicate names."""

    def __init__(self, stages: Iterable[TickStage]) -> None:
        self._stages = tuple(stages)
        names = [stage.name for stage in self._stages]
        if len(names) != len(set(names)):
            duplicate = next(name for name in names if names.count(name) > 1)
            raise ValueError(f"tick stage already registered: {duplicate}")

    def run(self) -> None:
        for stage in self._stages:
            stage.run()

    def names(self) -> tuple[str, ...]:
        return tuple(stage.name for stage in self._stages)
