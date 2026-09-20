"""Bounded final ASS artifacts, shared by lookahead preparation and live publication."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from saitenka_subtitles import decoration, whole_cue

from saitenka import otel_metrics


@dataclass(frozen=True, slots=True)
class OsdInputs:
    events: tuple[whole_cue.OsdEvent, ...]
    mapping: tuple[float, float, float, float]
    resolution: tuple[int, int]
    colors: tuple[tuple[int, int], ...]
    rules: tuple[decoration.TokenRule, ...]


@dataclass(frozen=True, slots=True)
class PreparedOsd:
    payload: str
    resolution: tuple[int, int]


class PreparedOsdCache:
    """Owner-thread cache. Keys retain immutable layout inputs, never bitmap layers."""

    def __init__(self, capacity: int = 128) -> None:
        if capacity <= 0:
            raise ValueError("prepared OSD capacity must be positive")
        self._capacity = capacity
        self._entries: OrderedDict[OsdInputs, PreparedOsd] = OrderedDict()

    def prepare(self, inputs: OsdInputs) -> PreparedOsd:
        with otel_metrics.traced("subtitle_osd_artifact") as span:
            cached = self._entries.get(inputs)
            span.set("cache_hit", cached is not None)
            if cached is not None:
                self._entries.move_to_end(inputs)
                return cached
            cue = whole_cue.WholeCue(events=inputs.events, mapping=inputs.mapping)
            payload = whole_cue.osd_payload(cue, inputs.colors)
            rules = decoration.payload(list(inputs.rules))
            artifact = PreparedOsd(
                "\n".join(part for part in (payload, rules) if part), inputs.resolution
            )
            self._entries[inputs] = artifact
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
            return artifact
