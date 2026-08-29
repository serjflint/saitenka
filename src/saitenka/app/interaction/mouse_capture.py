"""INTERACTION's claim on mpv's clicks and wheel: the forced mouse section.

Its own object because it is a resource with a lifetime, not three flags on the session shell.
The direct close plan releases it while mpv transport still works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.bindings import MOUSE_SECTION
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.app.mpv_egress import send_correlated
from saitenka.runtime import Owner

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka.app.lifecycle_timers import LifecycleTimers


class MouseCapture:
    """Force mpv's saitenka section while a surface is up, and hand it back otherwise."""

    def __init__(self, ipc, timers: LifecycleTimers, wants: Callable[[], bool]) -> None:
        self._ipc = ipc
        self._timers = timers
        self._wants = wants
        self.defined = False
        self.held = False

    def define(self, bindings: Sequence) -> None:
        """Define (once) the FORCED mpv section for the ``mouse``-scoped bindings; once enabled it
        outranks other scripts' forced MBTN_LEFT (uosc/inputevent). Enabled by `sync`."""
        lines = [f"{b.key} script-message {b.spec.message}" for b in bindings]
        self.defined = bool(lines)
        if lines:
            send_correlated(
                self._ipc,
                "define-mouse-section",
                "define-section",
                MOUSE_SECTION,
                "\n".join(lines) + "\n",
                "force",
                owner=Owner.INTERACTION,
            )

    def sync(self) -> None:
        """Own clicks/wheel while a saitenka surface is up, release it otherwise.

        The re-assertion that keeps another script from reclaiming the forced section is a repeating
        named deadline now, not a timestamp this compares against.
        """
        if not self.defined:
            return
        try:
            if self._wants():
                if not self.held:
                    self.take()
            elif self.held:
                self.release()
        except (OSError, ValueError):
            pass  # mpv went away mid-tick — the loop will notice

    def take(self) -> None:
        """Force the section and arm its re-assertion.

        Fails open: with no timer the section is forced once and never refreshed, so capture still
        works and only the defence against a script reclaiming it is lost.
        """
        send_correlated(
            self._ipc,
            "enable-mouse-section",
            "enable-section",
            MOUSE_SECTION,
            "allow-hide-cursor+allow-vo-dragging",
            owner=Owner.INTERACTION,
        )
        self.held = True

        def due() -> None:
            # Re-check rather than trust the arm: the surface may have gone down since, and a
            # re-assert then would take the mouse back from mpv for nothing.
            if self.held and self._wants():
                self.take()

        self._timers.schedule(LifecycleTimerKind.MOUSE_CAPTURE_REASSERT, 0.5, due)

    def release(self) -> None:
        """Drop the forced section so a detached mpv can't route clicks to a dead saitenka."""
        if not self.held:
            return
        self._timers.cancel(LifecycleTimerKind.MOUSE_CAPTURE_REASSERT)
        # Stays a direct write: this also runs during close, where the reactor is stopping — a
        # correlated command queued there may never be drained, and the forced section would outlive
        # us still holding the mouse. Tolerates a dead socket for the same reason.
        try:
            self._ipc.command("disable-section", MOUSE_SECTION)
        except (OSError, ValueError):
            pass
        self.held = False

    #: The session-resource contract. Release *is* the close: nothing else about the section
    #: outlives the process, and a second call is a no-op by the `held` guard.
    close = release
