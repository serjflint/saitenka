"""Session-local color accounting and its existing runtime deadline."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.color_accounting import ColorAccounting, ColorWrite
from saitenka.runtime import EffectFinished, EffectOutcome, Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.mpvio.ipc import MpvIPC

_TIMER = "subtitle:color-deadline"


@dataclass(frozen=True, slots=True)
class ColorDeadline:
    occurrence: int


class ColorTelemetry:
    """One active appearance; old completion callbacks cannot settle its replacement."""

    def __init__(
        self,
        ipc: MpvIPC,
        *,
        clock: Callable[[], float] = time.monotonic,
        configuration_owner: int = 0,
    ) -> None:
        self._ipc = ipc
        self._configuration_owner = configuration_owner
        self._clock = clock
        self.session = uuid.uuid4().hex
        self.current: ColorAccounting | None = None
        self._serial = 0
        self._key: tuple[object, ...] | None = None
        self._late_recorded = False
        self._first_recorded = False
        self._withdrawals_recorded = 0
        self._progress: dict = {}

    @property
    def cue_start_ms(self) -> int | None:
        value = self._key[-2] if self._key else None
        return value if isinstance(value, int) else None

    def begin(self, kind: str) -> None:
        self.retire("replaced")
        if otel_metrics.subtitle_color_outcomes is None:
            return
        self._serial += 1
        self.current = ColorAccounting(
            self._serial, kind, self._clock(), otel_metrics.COLOR_LATENCY_BUDGET_MS
        )
        self._key = None
        self._late_recorded = self._first_recorded = False
        self._withdrawals_recorded = 0
        self._progress = {}
        if otel_metrics.subtitle_color_pending is not None:
            otel_metrics.subtitle_color_pending.add(1)
        self._trace("subtitle_color_arrival", kind=kind)
        self._arm()

    def bind(self, key: tuple[object, ...], *, reconcile: bool = False) -> None:
        if otel_metrics.subtitle_color_outcomes is None:
            return
        previous = self._key
        refinement = (
            previous is not None
            and previous[:-2] == key[:-2]
            and previous[-1] == key[-1]
            and previous[-2] is None
        )
        if (
            reconcile
            and previous is not None
            and previous != key
            and not refinement
            and self.current is not None
        ):
            # The provisional target's token indices cannot describe mpv's corrected target.
            self.current = self.current.corrected_target()
            self._arm()
            self._trace("subtitle_color_target_corrected")
            self._first_recorded = False
        elif self.current is None or (
            previous is not None and previous != key and not reconcile and not refinement
        ):
            self.begin("natural")
        self._key = key
        if key != previous:
            self._trace("subtitle_color_target")

    def _arm(self) -> None:
        current = self.current
        if current is None:
            return
        due = ColorDeadline(current.occurrence)

        def finished(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED and self.current is current:
                current.check_deadline(self._clock())
                self._record()

        self._ipc.schedule_runtime_timer(
            owner=Owner.SUBTITLE,
            identity=due,
            timer=_TIMER,
            due_at=current.started + (current.budget_ms + 0.001) / 1000,
            on_finished=finished,
        )

    def qualify(
        self, occurrence: int, requested: frozenset[int], permitted: frozenset[int] | None
    ) -> None:
        current = self.current
        if current is None or current.occurrence != occurrence:
            return
        current.qualify(requested, permitted, self._clock())
        self._record()

    def submit(self, occurrence: int, device: str, tokens: frozenset[int]) -> ColorWrite | None:
        current = self.current
        if current is None or current.occurrence != occurrence:
            return None
        write = current.submit(device, tokens)
        self._trace(
            "subtitle_color_submit",
            device=device,
            write=write.serial,
            tokens=len(tokens),
            elapsed_ms=(self._clock() - current.started) * 1000,
        )
        return write

    def settle(self, write: ColorWrite | None, *, accepted: bool) -> None:
        current = self.current
        if write is None:
            return
        if current is None or write.occurrence != current.occurrence:
            with otel_metrics.traced("subtitle_color_stale_ack") as span:
                span.set("configuration_owner", self._configuration_owner)
                span.set("color_session", self.session)
                span.set("occurrence", write.occurrence)
                span.set("device", write.device)
                span.set("write", write.serial)
                span.set("accepted", value=False)
            return
        settled = current.settle(write, self._clock(), accepted=accepted)
        self._trace(
            "subtitle_color_ack",
            device=write.device,
            write=write.serial,
            accepted=settled,
            tokens=len(write.tokens),
            elapsed_ms=(self._clock() - current.started) * 1000,
        )
        self._record()

    def _record(self) -> None:
        current = self.current
        if current is None:
            return
        labels = {"kind": current.kind}
        if current.late and not self._late_recorded:
            self._late_recorded = True
            if otel_metrics.subtitle_color_deadlines is not None:
                otel_metrics.subtitle_color_deadlines.add(1, labels)
            self._trace("subtitle_color_deadline", budget_ms=current.budget_ms)
        if current.acknowledged and not self._first_recorded:
            self._first_recorded = True
            elapsed = (min(current.acknowledged.values()) - current.started) * 1000
            self._trace("subtitle_color_first_ack", first_ms=elapsed)
        withdrawals = current.withdrawals - self._withdrawals_recorded
        self._withdrawals_recorded = current.withdrawals
        if withdrawals and otel_metrics.subtitle_color_withdrawals is not None:
            otel_metrics.subtitle_color_withdrawals.add(withdrawals, labels)
        progress = asdict(current.snapshot(self._clock()))
        progress.pop("elapsed_ms")
        if progress != self._progress:
            self._progress = progress
            self._trace("subtitle_color_progress", **progress)

    def retire(self, reason: str) -> None:
        current = self.current
        if current is None:
            return
        outcome = current.retire(self._clock(), reason)
        self._record()
        if outcome is not None:
            labels = {"kind": outcome.kind, "status": outcome.status, "reason": reason}
            if otel_metrics.subtitle_color_outcomes is not None:
                otel_metrics.subtitle_color_outcomes.add(1, labels)
            if otel_metrics.subtitle_color_pending is not None:
                otel_metrics.subtitle_color_pending.add(-1)
            if outcome.complete_ms is not None and otel_metrics.subtitle_color_ack_ms is not None:
                otel_metrics.subtitle_color_ack_ms.record(
                    outcome.complete_ms, {"kind": outcome.kind, "endpoint": "complete"}
                )
            if outcome.first_ms is not None and otel_metrics.subtitle_color_ack_ms is not None:
                otel_metrics.subtitle_color_ack_ms.record(
                    outcome.first_ms, {"kind": outcome.kind, "endpoint": "first"}
                )
            self._trace("subtitle_color_outcome", **asdict(outcome))
        self.current = None
        self._key = None
        self._ipc.cancel_runtime_timer(_TIMER)

    def _trace(self, name: str, **attrs: object) -> None:
        with otel_metrics.traced(name) as span:
            span.set("configuration_owner", self._configuration_owner)
            span.set("color_session", self.session)
            if self.current is not None:
                span.set("occurrence", self.current.occurrence)
            if self._key is not None:
                span.set("text_hash", self._key[-1])
                if self._key[-2] is not None:
                    span.set("cue_start_ms", self._key[-2])
            for key, value in attrs.items():
                if value is not None:
                    span.set("color_status" if key == "status" else key, value)
