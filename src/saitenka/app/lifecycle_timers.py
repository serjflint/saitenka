"""Named timer ownership for startup, loading, and toast lifecycle behavior."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime import EffectOutcome, Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime import EffectFinished


class LifecycleTimerKind(StrEnum):
    STARTUP_HEALTH = "startup-health"
    LOADING_FRAME = "loading-frame"
    TOAST_EXPIRY = "toast-expiry"
    FLASH_EXPIRY = "flash-expiry"
    SIDEBAR_MANUAL_HOLD = "sidebar-manual-hold"
    MINED_SEED_RETRY = "mined-seed-retry"
    MOUSE_CAPTURE_REASSERT = "mouse-capture-reassert"
    PAUSED_REPAINT = "paused-repaint"
    SESSION_PERSIST = "session-persist"
    #: Hover dwell. Interaction-owned, but the same mechanism: one deadline per kind, latest wins.
    #: A second implementation of revision-fenced named timers is the divergence this avoids.
    HOVER_SWITCH = "hover-switch"
    TOOLTIP_HIDE = "tooltip-hide"
    NESTED_HIDE = "nested-hide"
    SCAN_OPEN = "scan-open"


#: Deadlines are owned by the subsystem whose work they retire, and the owner reaches the gateway
#: on every scheduled effect — so it is a property of the kind, not of the scheduler.
_OWNERS = {
    LifecycleTimerKind.HOVER_SWITCH: Owner.INTERACTION,
    LifecycleTimerKind.TOOLTIP_HIDE: Owner.INTERACTION,
    LifecycleTimerKind.NESTED_HIDE: Owner.INTERACTION,
    LifecycleTimerKind.SCAN_OPEN: Owner.INTERACTION,
}


@dataclass(frozen=True, slots=True)
class LifecycleTimerIdentity:
    kind: LifecycleTimerKind
    revision: int


class RuntimeTimerPort(Protocol):
    def schedule_runtime_timer(self, **kwargs) -> bool: ...

    def cancel_runtime_timer(self, timer: str) -> bool: ...


class LifecycleTimers:
    """Revision-fence lifecycle deadlines and deliver only the latest successful due event."""

    def __init__(
        self,
        port: RuntimeTimerPort,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._port = port
        self._clock = clock
        self._admission_lock = Lock()
        self._state_lock = Lock()
        self._revisions = dict.fromkeys(LifecycleTimerKind, 0)
        self._closed = False

    def schedule(
        self,
        kind: LifecycleTimerKind,
        delay_s: float,
        callback: Callable[[], object],
    ) -> bool:
        if delay_s < 0:
            raise ValueError("lifecycle timer delay must be non-negative")
        with self._admission_lock:
            with self._state_lock:
                if self._closed:
                    return False
                revision = self._revisions[kind] + 1
                self._revisions[kind] = revision
            identity = LifecycleTimerIdentity(kind, revision)

            def finished(completion: EffectFinished) -> None:
                if completion.outcome is not EffectOutcome.SUCCEEDED:
                    return
                with self._state_lock:
                    if self._closed or self._revisions[kind] != revision:
                        return
                callback()

            schedule = getattr(self._port, "schedule_runtime_timer", None)
            if schedule is None:
                return False
            return schedule(
                owner=_OWNERS.get(kind, Owner.SESSION),
                identity=identity,
                timer=self._name(kind),
                due_at=self._clock() + delay_s,
                on_finished=finished,
            )

    def cancel(self, kind: LifecycleTimerKind) -> bool:
        with self._admission_lock:
            with self._state_lock:
                if self._closed:
                    return False
                self._revisions[kind] += 1
            cancel = getattr(self._port, "cancel_runtime_timer", None)
            return False if cancel is None else cancel(self._name(kind))

    def close(self) -> None:
        with self._admission_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                for kind in LifecycleTimerKind:
                    self._revisions[kind] += 1
            cancel = getattr(self._port, "cancel_runtime_timer", None)
            if cancel is not None:
                for kind in LifecycleTimerKind:
                    cancel(self._name(kind))

    @staticmethod
    def _name(kind: LifecycleTimerKind) -> str:
        return f"lifecycle:{kind.value}"
