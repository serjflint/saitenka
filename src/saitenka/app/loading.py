"""The top-left "loading" spinner shown while dictionaries + scorer load (progressive startup).

Just a bitmap frame builder — the controller drives it from its own poll loop (it owns the mpv IPC
once running, so there's no separate thread to race it), drawing plain subtitles immediately and
swapping in FSRS coloring + tooltips + mining once the background load finishes.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.toast import render_toast
from saitenka.runtime.effects import EffectError, EffectOutcome, Owner

if TYPE_CHECKING:  # PIL is imported by the renderer (toast); we only need Image for the annotation
    from PIL import Image

    from saitenka.mpvio.gateway import MpvGateway
    from saitenka.runtime.events import EffectFinished

log = logging.getLogger("saitenka")

# ASCII spinner — the vendored fonts DON'T cover braille (⠋…), which would render blank; classic
# |/-\ is always covered so the spinner actually animates.
SPINNER = "|/-\\"

# Shown on mpv's OWN OSD the instant IPC connects — the only feedback possible before the first cue.
# ASCII so it renders under any mpv OSD font / user config; mpv (not our vendored fonts) draws it.
STARTUP_HINT = "saitenka starting..."


class HintOutcome(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


class HintOperation(Enum):
    SHOW = "show"
    CLEAR = "clear"


class StartupHintLease:
    """Ownership of one correlated startup-hint request."""

    def __init__(self, gateway: MpvGateway) -> None:
        self._gateway = gateway
        self._lock = threading.Lock()
        self._outcome = HintOutcome.PENDING
        self._ready = False
        self._reconnected_after_unknown = False
        self._clear_submitted = False
        self._clear_submitted_at = 0.0
        self._clear_connection_epoch = -1
        self._submitted_at = time.monotonic()
        self._connection_epoch = gateway.connection_epoch
        gateway.legacy.submit_mpv(
            owner=Owner.SESSION,
            identity=HintOperation.SHOW,
            command=("show-text", STARTUP_HINT, 30000),
            timeout_s=10.0,
            on_finished=self._resolve,
        )

    @property
    def outcome(self) -> HintOutcome:
        with self._lock:
            return self._outcome

    def _resolve(self, completion: EffectFinished) -> None:
        accepted = completion.outcome is EffectOutcome.SUCCEEDED
        ambiguous = completion.error is EffectError.DISCONNECTED
        replaced = ambiguous and self._gateway.connection_epoch > self._connection_epoch
        with otel_metrics.traced(
            "startup.hint",
            operation="show",
            outcome="accepted" if accepted else ("unknown" if ambiguous else "rejected"),
            connection_epoch=str(self._connection_epoch),
        ) as span:
            span.set("reply_latency_ms", round((time.monotonic() - self._submitted_at) * 1_000, 3))
        should_clear = False
        with self._lock:
            if self._outcome is not HintOutcome.PENDING:
                return
            self._outcome = (
                HintOutcome.ACCEPTED
                if accepted
                else (HintOutcome.UNKNOWN if ambiguous else HintOutcome.REJECTED)
            )
            if replaced:
                self._reconnected_after_unknown = True
            clearable = accepted or (ambiguous and self._reconnected_after_unknown)
            if clearable and self._ready and not self._clear_submitted:
                self._clear_submitted = True
                should_clear = True
        if should_clear:
            self._submit_clear()

    def mark_ready(self) -> None:
        should_clear = False
        with self._lock:
            self._ready = True
            clearable = self._outcome is HintOutcome.ACCEPTED or (
                self._outcome is HintOutcome.UNKNOWN and self._reconnected_after_unknown
            )
            if clearable and not self._clear_submitted:
                self._clear_submitted = True
                should_clear = True
        if should_clear:
            self._submit_clear()

    def connection_replaced(self) -> None:
        """Resolve an ambiguous lost acknowledgement on a live replacement connection."""
        should_clear = False
        with self._lock:
            if self._outcome is HintOutcome.UNKNOWN:
                self._reconnected_after_unknown = True
            clearable = self._outcome in {HintOutcome.ACCEPTED, HintOutcome.UNKNOWN}
            if clearable and self._ready and not self._clear_submitted:
                self._clear_submitted = True
                should_clear = True
        if should_clear:
            self._submit_clear()

    def _submit_clear(self) -> None:
        self._clear_submitted_at = time.monotonic()
        self._clear_connection_epoch = self._gateway.connection_epoch
        self._gateway.legacy.submit_mpv(
            owner=Owner.SESSION,
            identity=HintOperation.CLEAR,
            command=("show-text", "", 1),
            timeout_s=10.0,
            on_finished=self._resolve_clear,
        )

    def _resolve_clear(self, completion: EffectFinished) -> None:
        accepted = completion.outcome is EffectOutcome.SUCCEEDED
        with otel_metrics.traced(
            "startup.hint",
            operation="clear",
            outcome="accepted" if accepted else "rejected",
            connection_epoch=str(self._clear_connection_epoch),
        ) as span:
            span.set(
                "reply_latency_ms", round((time.monotonic() - self._clear_submitted_at) * 1_000, 3)
            )
        if completion.error is not EffectError.DISCONNECTED:
            return
        should_retry = False
        with self._lock:
            self._clear_submitted = False
            if (
                self._gateway.connection_epoch > self._clear_connection_epoch
                and self._ready
                and self._outcome in {HintOutcome.ACCEPTED, HintOutcome.UNKNOWN}
            ):
                self._clear_submitted = True
                should_retry = True
        if should_retry:
            self._submit_clear()


def loading_image(text: str, frame: int, size: int = 26) -> Image.Image:
    """One animated frame: ``⠋ <text>…`` rendered as a small toast bitmap."""
    return render_toast(f"{SPINNER[frame % len(SPINNER)]} {text}…", size=size)


def show_startup_hint(gateway: MpvGateway, *, screenshot: bool = False) -> StartupHintLease | None:
    """Post the startup breadcrumb on mpv's native OSD the moment IPC connects. This is the ONLY thing
    that can appear during mpv's file-load window: our own overlay doesn't exist yet, and the main
    thread is then blocked in a ``get_property`` waiting on mpv, so nothing of ours can draw. mpv shows
    it as soon as its VO is up; the Reader's first completed event-loop turn authorizes its one-shot
    removal. Skipped for screenshots (it would land in the capture)."""
    if screenshot:
        return None
    try:
        return StartupHintLease(gateway)
    except Exception:
        log.debug("startup OSD hint failed", exc_info=True)
        return None
