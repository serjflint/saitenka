"""Implementation-neutral, text-free runtime behavior trace contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

CueState = Literal["none", "active", "retired"]
PixelState = Literal["none", "legacy", "native", "unknown"]
InteractionState = Literal["unavailable", "ready", "hovered", "tooltip"]
LifecycleState = Literal["open", "closed"]
SurfaceState = Literal["none", "present"]
_CUES = frozenset({"none", "active", "retired"})
_PIXELS = frozenset({"none", "legacy", "native", "unknown"})
_INTERACTIONS = frozenset({"unavailable", "ready", "hovered", "tooltip"})
_SURFACES = frozenset({"none", "present"})
_LIFECYCLES = frozenset({"open", "closed"})
_EVENTS = frozenset(
    {
        "first-input",
        "next-turn",
        "cue-installed",
        "cue-conflict",
        "cue-reconciled",
        "native-cue",
        "geometry-ready",
        "geometry-miss",
        "geometry-recovered",
        "close",
    }
)
_OUTCOMES = frozenset(
    {
        "dispatched-before-ready-clear",
        "clear-reply-not-required",
        "interactive",
        "input-rejected",
        "replacement-active",
        "pixels-established",
        "interaction-ready",
        "interaction-only-degraded",
        "presentation-retired",
    }
)


@dataclass(frozen=True, slots=True)
class BehaviorRecord:
    event: str
    cue: CueState
    pixels: PixelState
    interaction: InteractionState
    surfaces: SurfaceState
    lifecycle: LifecycleState
    outcome: str


class BehaviorTrace:
    """Strict ordered oracle; records contain state classes, never subtitle text."""

    def __init__(self) -> None:
        self._records: list[BehaviorRecord] = []

    def append(self, record: BehaviorRecord) -> None:
        valid = (
            record.event in _EVENTS
            and record.cue in _CUES
            and record.pixels in _PIXELS
            and record.interaction in _INTERACTIONS
            and record.surfaces in _SURFACES
            and record.lifecycle in _LIFECYCLES
            and record.outcome in _OUTCOMES
        )
        if not valid:
            raise ValueError("behavior trace event and outcome must use the text-free vocabulary")
        self._records.append(record)

    def records(self) -> tuple[dict[str, str], ...]:
        return tuple(asdict(record) for record in self._records)
