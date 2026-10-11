"""Broker-owned preparation of card-preview content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.features.preview.miner_ui import PreparedPreview, PreviewPorts

LANE = "card-preview"


@dataclass(frozen=True, slots=True)
class PreviewDestination:
    start: float
    end: float
    previous_text: str


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    sequence: int
    build: Callable[[], PreparedPreview | None]
    ports: Callable[[], PreviewPorts]
    toast: Callable[..., object]
    fallback: str
    cancelled: threading.Event
    current: Callable[[], bool]
    destination: PreviewDestination | None = None


def run_preparation(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, PreviewRequest):
        raise TypeError("invalid preview request")
    if cancelled.is_set() or request.cancelled.is_set():
        return None
    prepared = request.build()
    return None if cancelled.is_set() or request.cancelled.is_set() else prepared
