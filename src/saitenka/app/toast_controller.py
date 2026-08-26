"""Owner-thread presentation of transient user notifications."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka.app.lifecycle_timers import LifecycleTimerKind, LifecycleTimers
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.toast import render_toast

if TYPE_CHECKING:
    from saitenka.app.features.help.help_controller import ScreenState
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces


class NotificationSink(Protocol):
    def show(self, text: str, kind: str = "ok", seconds: float = 2.8) -> None: ...


class ToastController:
    """Render and retire the session's single toast surface."""

    def __init__(
        self,
        surfaces: LifecycleSurfaces,
        screen: ScreenState,
        timers: LifecycleTimers,
    ) -> None:
        self._surfaces = surfaces
        self._screen = screen
        self._timers = timers

    def show(self, text: str, kind: str = "ok", seconds: float = 2.8) -> None:
        image = render_toast(text, kind)
        x = (self._screen.osd[0] - image.width) // 2
        y = round(self._screen.osd[1] * 0.08)
        self._surfaces.present(image, x, y, oid=OverlayId.TOAST)
        scheduled = self._timers.schedule(
            LifecycleTimerKind.TOAST_EXPIRY,
            seconds,
            lambda: self._surfaces.remove(OverlayId.TOAST),
        )
        if not scheduled:
            self._surfaces.remove(OverlayId.TOAST)
