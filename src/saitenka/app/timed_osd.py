"""Bounded, owner-thread staging of whole-cue color on mpv's video clock."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.subnav_policy import FILTER_OPTIONS, filters_can_drop_a_cue
from saitenka.mpvio.diagnostics import payload_hash
from saitenka.runtime import EffectFinished, EffectOutcome, Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka_subtitles import CueIndex

    from saitenka.app.native_subtitles import GeometryObservation
    from saitenka.mpvio.ipc import MpvIPC

MAX_ENTRIES = 16
MAX_BYTES = 1024 * 1024
FIRST_SLOT = 2001


@dataclass(frozen=True, slots=True)
class TimedCue:
    text_hash: str
    start_ms: int
    end_ms: int
    video_start_ms: int
    video_end_ms: int


@dataclass(slots=True)
class StagedCue:
    slot: int
    cue: TimedCue
    payload: str
    resolution: tuple[int, int]
    epoch: int
    payload_hash: str = ""
    state: str = "prepared"


def subtitle_delay_ms(seen: GeometryObservation) -> int | None:
    if (
        seen.prop("options/sub-speed") != 1
        or seen.prop("options/sub-fps") != 0
        or seen.prop("options/play-direction") != "forward"
        or seen.prop("options/blend-subtitles") is not False
        or filters_can_drop_a_cue({name: seen.prop(f"options/{name}") for name in FILTER_OPTIONS})
    ):
        return None
    delay = seen.prop("sub-delay")
    if not isinstance(delay, int | float) or not math.isfinite(delay):
        return None
    milliseconds = round(delay * 1000)
    return milliseconds if abs(milliseconds - delay * 1000) < 1e-6 else None


def timed_cue(index: CueIndex | None, timestamp_ms: int, delay_ms: int) -> TimedCue | None:
    if index is None:
        return None
    active = index.active_at(timestamp_ms / 1000)
    if not active.located:
        return None
    cue = index.cues[active.position]
    if any(
        (other.start, other.end) != (cue.start, cue.end)
        and other.start < cue.end
        and other.end > cue.start
        for other in index.cues
    ):
        return None
    start, end = round(cue.start * 1000), round(cue.end * 1000)
    if not -(2**52) <= start + delay_ms < end + delay_ms <= 2**52:
        return None
    return TimedCue(
        hashlib.blake2s(index.frame_text(active.position).encode(), digest_size=16).hexdigest(),
        start,
        end,
        start + delay_ms,
        end + delay_ms,
    )


class TimedOsd:
    def __init__(
        self,
        ipc: MpvIPC,
        observe: Callable[[], GeometryObservation],
        changed: Callable[[], None],
        *,
        evidence: Callable[[dict], None] | None = None,
        configuration_owner: int = 0,
    ) -> None:
        self._evidence = evidence
        self._configuration_owner = configuration_owner
        self._ipc = ipc
        self._observe = observe
        self._changed = changed
        self.supported: bool | None = None
        self._probing = False
        self._failed = False
        self._epoch = 0
        self._connection = 0
        self._entries: dict[int, StagedCue] = {}
        self._context: tuple[object, ...] | None = None
        self._closed = False

    def _record(self, event: str, entry: StagedCue | None = None, **attrs: object) -> None:
        from saitenka.app.telemetry import span_gate

        if not span_gate and self._evidence is None:
            return
        record = dict(
            event=event,
            epoch=self._epoch,
            connection_epoch=self._connection,
            supported="unknown" if self.supported is None else self.supported,
            **attrs,
        )
        if entry is not None:
            record.update(
                slot=entry.slot,
                text_hash=entry.cue.text_hash,
                start_ms=entry.cue.start_ms,
                end_ms=entry.cue.end_ms,
                video_start_ms=entry.cue.video_start_ms,
                video_end_ms=entry.cue.video_end_ms,
                state=entry.state,
                entry_epoch=entry.epoch,
                payload_hash=entry.payload_hash,
            )
        if self._evidence is not None:
            self._evidence(record)
        with otel_metrics.traced("subtitle_timed_osd", event=event) as span:
            span.set("configuration_owner", self._configuration_owner)
            for key, value in record.items():
                span.set(key, value)

    def discover(self) -> None:
        if self._closed or self.supported is not None or self._probing:
            return
        self._probing = True
        connection = self._connection

        def finished(completion: EffectFinished) -> None:
            if self._closed or connection != self._connection:
                return
            self._capability_finished(completion)

        if not self._send(("get_property", "command-list"), finished):
            self.supported = False
            self._probing = False
            self._record("capability-unavailable")

    def _capability_finished(self, completion: EffectFinished) -> None:
        self._probing = False
        value = completion.result
        self.supported = (
            completion.outcome is EffectOutcome.SUCCEEDED
            and isinstance(value, list)
            and any(isinstance(c, dict) and c.get("name") == "osd-overlay-timed" for c in value)
        )
        self._record("capability")
        if self.supported:
            for entry in tuple(self._entries.values()):
                if entry.state == "prepared":
                    self._send_entry(entry)
        else:
            self._entries.clear()
        self._changed()

    def _send(self, command: tuple, finished: Callable[[EffectFinished], None]) -> bool:
        return self._ipc.submit_runtime_mpv(
            owner=Owner.SUBTITLE,
            identity=("timed-osd", self._connection, self._epoch, command[:2]),
            command=command,
            timeout_s=5.0,
            on_finished=finished,
        )

    def _synchronize(self, seen: GeometryObservation) -> int | None:
        delay = subtitle_delay_ms(seen)
        context = (seen.prop("path"), seen.prop("sid"), seen.osd, id(seen.index), delay)
        if self._context is not None and self._context != context:
            self.invalidate("context-changed")
        if self._context != context:
            self._record("context", delay_ms=delay, width=seen.osd[0], height=seen.osd[1])
        self._context = context
        return delay

    def stage(
        self, timestamp_ms: int, payload: str, resolution: tuple[int, int]
    ) -> StagedCue | None:
        self.discover()
        if self._closed or self.supported is False or self._failed or not payload:
            return None
        seen = self._observe()
        delay = self._synchronize(seen)
        cue = None if delay is None else timed_cue(seen.index, timestamp_ms, delay)
        if cue is None:
            self._record("declined", reason="clock-or-event-ineligible")
            return None
        existing = self._existing(cue, payload, resolution)
        if existing is not None:
            return existing
        if not self._admit(cue, payload, seen):
            self._record("declined", reason="capacity")
            return None
        slot = next(
            i for i in range(FIRST_SLOT, FIRST_SLOT + MAX_ENTRIES) if i not in self._entries
        )
        entry = StagedCue(slot, cue, payload, resolution, self._epoch, payload_hash(payload))
        self._entries[slot] = entry
        self._record("prepared", entry)
        if self.supported:
            self._send_entry(entry)
        return entry

    def _admit(self, cue: TimedCue, payload: str, seen: GeometryObservation) -> bool:
        size = len(payload.encode())
        if (
            self._failed
            or size > MAX_BYTES
            or any(e.state in {"retiring", "uncertain"} for e in self._entries.values())
        ):
            return False
        if (
            len(self._entries) < MAX_ENTRIES
            and size + sum(len(e.payload.encode()) for e in self._entries.values()) <= MAX_BYTES
        ):
            return True
        position = seen.prop("time-pos")
        candidates = [
            e
            for e in self._entries.values()
            if not (
                isinstance(position, int | float)
                and e.cue.video_start_ms <= position * 1000 < e.cue.video_end_ms
            )
        ]
        if candidates:
            victim = max(candidates, key=lambda e: abs(e.cue.start_ms - cue.start_ms))
            self._record("evict", victim)
            self._remove(victim)
        return False

    def _existing(
        self, cue: TimedCue, payload: str, resolution: tuple[int, int]
    ) -> StagedCue | None:
        for entry in tuple(self._entries.values()):
            if entry.cue == cue and entry.state not in {"retiring", "uncertain"}:
                if (entry.payload, entry.resolution) == (payload, resolution):
                    return entry
                self._remove(entry)
                break
        return None

    def _send_entry(self, entry: StagedCue) -> None:
        if (
            self._closed
            or self._failed
            or self._entries.get(entry.slot) is not entry
            or entry.epoch != self._epoch
            or entry.state != "prepared"
        ):
            return
        entry.state = "pending"
        self._record("submit", entry)

        def finished(completion: EffectFinished) -> None:
            if (
                self._entries.get(entry.slot) is not entry
                or entry.epoch != self._epoch
                or entry.state != "pending"
            ):
                return
            if completion.outcome is EffectOutcome.SUCCEEDED:
                entry.state = "ready"
                self._record("ack", entry)
                self._changed()
            else:
                self._record("stage-failed", entry)
                self._failed = True
                self.invalidate("stage-failed")

        if not self._send(
            (
                "osd-overlay-timed",
                entry.slot,
                entry.cue.video_start_ms,
                entry.cue.video_end_ms,
                entry.payload,
                *entry.resolution,
                1,
            ),
            finished,
        ):
            self._failed = True
            self._record("stage-rejected", entry)
            self.invalidate("stage-rejected")

    def present(
        self,
        identity: tuple[str, float | None, int] | None,
        payload: str,
        resolution: tuple[int, int],
        *,
        occurrence: int | None = None,
    ) -> tuple[bool, bool]:
        """Return (owns base surface, acknowledged); pending writes never duplicate slots."""
        if self.supported is False:
            return False, False
        if self._closed:
            return True, False
        if self.supported is None:
            self.discover()
            return True, False
        if not payload:
            return True, False
        if self._failed or identity is None or identity[1] is None:
            return True, False
        seen = self._observe()
        delay = self._synchronize(seen)
        timestamp = round(identity[1]) + 1
        cue = None if delay is None else timed_cue(seen.index, timestamp, delay)
        if cue is None or (cue.text_hash, cue.start_ms) != identity[:2]:
            self._record("declined", reason="unmatched-occurrence")
            return True, False
        entry = self.stage(timestamp, payload, resolution)
        if entry is None:
            return True, False
        self._record("selected", entry, occurrence=occurrence, generation=identity[2])
        return True, entry.state == "ready"

    def retire(self, identity: tuple[str, float | None, int] | None) -> None:
        if identity is not None:
            for entry in tuple(self._entries.values()):
                if (entry.cue.text_hash, entry.cue.start_ms) == identity[:2]:
                    self._remove(entry)

    def close(self) -> None:
        self._closed = True
        self.invalidate("closed")

    def refresh(self) -> None:
        if not self._closed:
            self._changed()

    def invalidate(self, reason: str) -> None:
        self._epoch += 1
        self._record("invalidate", reason=reason)
        for entry in tuple(self._entries.values()):
            self._remove(entry)

    def _remove(self, entry: StagedCue) -> None:
        if entry.state == "retiring":
            return
        if entry.state == "prepared":
            self._entries.pop(entry.slot, None)
            self._record("discarded", entry)
            return
        entry.state = "retiring"
        self._record("remove", entry)

        def finished(completion: EffectFinished) -> None:
            if self._entries.get(entry.slot) is not entry:
                return
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._entries.pop(entry.slot)
                self._record("removed", entry)
                self.refresh()
            else:
                entry.state = "uncertain"
                self._failed = True
                self._record("remove-failed", entry)

        if not self._send(("osd-overlay", entry.slot, "none", ""), finished):
            entry.state = "uncertain"
            self._failed = True
            self._record("remove-rejected", entry)

    def connection_replaced(self) -> None:
        # mpv removes all externally owned slots when the previous IPC client disconnects.
        self._connection += 1
        self._epoch += 1
        self._entries.clear()
        self._context = None
        self._failed = False
        self.supported = None
        self._probing = False
        self._closed = False
        self._record("connection-replaced")
