"""Lifecycle-owned OSD surfaces over revision-fenced runtime slots."""

from __future__ import annotations

import time
from threading import RLock
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome, Owner
from saitenka.runtime.surfaces import (
    SurfaceAction,
    SurfaceRuntime,
    SurfaceTransaction,
    SurfaceTransactionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    from PIL import Image

    from saitenka.mpvio.osd import Overlay, PreparedOverlay

_SURFACE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_SURFACE_TIMEOUT_S = 10.0
#: Where ids for inline-settled transactions start. They never reach the reactor's sequence, so they
#: only have to stay clear of it in a log read by a human.
_INLINE_EFFECT_ID_BASE = 1_000_000


def _payload_events(command: tuple[object, ...]) -> int:
    """ASS events in an `osd-overlay` write; 0 for a bitmap one, which mpv does not parse.

    The unit mpv is billed in: it walks the payload event by event, so a byte count would rate a
    cue carrying four underlines the same as one carrying none.
    """
    if command and command[0] == "osd-overlay":
        payload = next((part for part in command[3:4] if isinstance(part, str)), "")
        return len(payload.splitlines()) if payload else 0
    return 0


class LifecycleSurfaces:
    """Own loading and toast presentation transactions."""

    def __init__(self, overlay: Overlay) -> None:
        self._overlay = overlay
        self._runtime = SurfaceRuntime()
        # Reentrant: a transaction that settles inline runs its `on_settled` inside this section,
        # and a rollback settles by clearing the same slot — a second request from the same thread.
        # The revision fence already orders those two (the clear allocates the newer revision).
        self._request_lock = RLock()
        self._inline_effect_id = _INLINE_EFFECT_ID_BASE

    def present(
        self,
        img: Image.Image,
        x: int,
        y: int,
        *,
        oid: int,
        owner: Owner = Owner.PRESENTATION,
        on_settled: Callable[[bool], None] | None = None,
    ) -> SurfaceTransaction:
        return self._present(
            lambda revision: self._overlay.prepare(img, x, y, oid=oid, revision=revision),
            oid=oid,
            owner=owner,
            on_settled=on_settled,
        )

    def present_rgba(
        self,
        rgba: np.ndarray,
        x: int,
        y: int,
        *,
        oid: int,
        owner: Owner = Owner.PRESENTATION,
        on_settled: Callable[[bool], None] | None = None,
    ) -> SurfaceTransaction:
        """`present` for pixels that were composited as an array. Same fence, no PIL."""
        return self._present(
            lambda revision: self._overlay.prepare_rgba(rgba, x, y, oid=oid, revision=revision),
            oid=oid,
            owner=owner,
            on_settled=on_settled,
        )

    def _present(
        self,
        stage: Callable[[int], PreparedOverlay],
        *,
        oid: int,
        owner: Owner,
        on_settled: Callable[[bool], None] | None,
    ) -> SurfaceTransaction:
        with self._request_lock:
            transaction = self._runtime.request(str(oid), SurfaceAction.PRESENT)
            prepared = None
            try:
                prepared = stage(transaction.revision)
                self._submit(transaction, prepared.command, prepared, owner, on_settled)
            except _SURFACE_ERRORS:
                self._fail(transaction, prepared, on_settled)
        return transaction

    def remove(
        self,
        oid: int,
        *,
        owner: Owner = Owner.PRESENTATION,
        on_settled: Callable[[bool], None] | None = None,
    ) -> SurfaceTransaction:
        with self._request_lock:
            transaction = self._runtime.request(str(oid), SurfaceAction.REMOVE)
            try:
                command = ("overlay-remove", self._overlay.physical_oid(oid))
                self._submit(transaction, command, None, owner, on_settled)
            except _SURFACE_ERRORS:
                self._fail(transaction, None, on_settled)
        return transaction

    def close(self) -> None:
        # Attach leaves mpv alive. A synchronous remove is queued after every prior add and receives
        # its reply before IPC teardown, so lifecycle pixels cannot survive detachment.
        for oid in tuple(self._overlay.lifecycle_oids):
            with self._request_lock:
                transaction = self._runtime.request(str(oid), SurfaceAction.REMOVE)
                try:
                    reply = self._overlay.remove_lifecycle_now(oid)
                except _SURFACE_ERRORS:
                    self._fail(transaction, None)
                    continue
                succeeded = reply.get("error") in {None, "success"}
                if succeeded:
                    self._overlay.commit_remove(oid)
                self._runtime.finish(
                    SurfaceTransactionOutcome(
                        transaction,
                        EffectOutcome.SUCCEEDED if succeeded else EffectOutcome.FAILED,
                        None if succeeded else EffectError.INVALID_RESULT,
                    )
                )

    def set_visible(self, *, visible: bool) -> None:
        """Hide or restore every saitenka surface at once, retaining each one's desired state.

        Whole-surface, so it has no per-slot transaction to fence — but it is still presentation,
        and a feature reaching past this layer to the overlay for it is the thing the direct-mutation
        rule is about. Routed here so `Alt+o` talks to the surface layer like everything else.
        """
        self._overlay.set_visible(visible=visible)

    def repaint(self) -> None:
        """Re-issue every live surface so mpv composites and PRESENTS them.

        Paused, mpv throttles OSD updates until something pokes it (mpv #8172), so a draw that
        landed while paused sits invisible until the user jiggles the mouse. Re-adding is the poke.
        """
        self._overlay.repaint()

    def snapshot(self, oid: int):
        return self._runtime.snapshot(str(oid))

    def settled(self) -> bool:
        return self._runtime.settled()

    def _submit(
        self,
        transaction: SurfaceTransaction,
        command: tuple[object, ...],
        prepared: PreparedOverlay | None,
        owner: Owner = Owner.PRESENTATION,
        on_settled: Callable[[bool], None] | None = None,
    ) -> None:
        def finished(completion: EffectFinished) -> None:
            accepted = self._runtime.finish(
                SurfaceTransactionOutcome(transaction, completion.outcome, completion.error)
            )
            if not accepted:
                # Superseded by a newer revision for this slot: the pixels it staged are not ours
                # to commit, and its caller is no longer waiting on an answer.
                if prepared is not None:
                    self._overlay.discard_prepared(prepared)
                return
            committed = completion.outcome is EffectOutcome.SUCCEEDED
            if committed:
                if prepared is None:
                    self._overlay.commit_remove(int(transaction.slot))
                else:
                    self._overlay.commit_prepared(prepared)
            elif prepared is not None:
                self._overlay.discard_prepared(prepared)
            if on_settled is not None:
                on_settled(committed)

        self._route(owner, transaction, command, finished)

    def _route(
        self,
        owner: Owner,
        transaction: SurfaceTransaction,
        command: tuple[object, ...],
        finished: Callable[[EffectFinished], None],
    ) -> None:
        """Hand the command to the correlated-command port, or issue it inline and settle here.

        Timed end to end, because every other number on the draw path stops when the command leaves
        us. `subtitle_draw` costs 0.33 ms to *build* a payload; what mpv then does with it — parse
        the ASS, lay it out, composite — is on the far side of this call and was unmeasured, so a
        late paint left every span green. A JLPT-heavy cue hands over twenty-odd events per write.
        """
        started = time.perf_counter()
        settle = finished

        def timed(completion: EffectFinished) -> None:
            with otel_metrics.traced("surface_write") as span:
                span.set("round_trip_ms", round((time.perf_counter() - started) * 1000.0, 3))
                span.set(
                    "route", "inline" if self._overlay.runtime_submit is None else "correlated"
                )
                span.set("command", str(command[0]))
                span.set("slot", str(transaction.slot))
                span.set("outcome", completion.outcome.value)
                # The payload's size in the unit mpv pays per: an ASS write is parsed event by
                # event, so bytes alone would say nothing about a cue with four underlines.
                span.set("events", _payload_events(command))
            settle(completion)

        finished = timed

        submit = self._overlay.runtime_submit
        if submit is None:
            # No port: the command is issued and answered inline, so this layer settles the
            # transaction with an outcome it observed rather than one it was told.
            succeeded = self._overlay.apply_surface_command(command)
        elif submit(
            owner=owner,
            identity=transaction,
            command=command,
            timeout_s=_SURFACE_TIMEOUT_S,
            on_finished=finished,
        ):
            return
        else:
            succeeded = False  # the port refused — mpv is gone, and nothing was issued
        finished(self._inline_completion(owner, transaction, succeeded=succeeded))

    def _inline_completion(
        self, owner: Owner, transaction: SurfaceTransaction, *, succeeded: bool
    ) -> EffectFinished:
        self._inline_effect_id += 1
        return EffectFinished(
            EffectId(self._inline_effect_id),
            owner,
            transaction,
            EffectOutcome.SUCCEEDED if succeeded else EffectOutcome.FAILED,
            error=None if succeeded else EffectError.INVALID_RESULT,
        )

    def _fail(
        self,
        transaction: SurfaceTransaction,
        prepared: PreparedOverlay | None,
        on_settled: Callable[[bool], None] | None = None,
    ) -> None:
        self._runtime.finish(
            SurfaceTransactionOutcome(
                transaction,
                EffectOutcome.FAILED,
                EffectError.INTERNAL,
            )
        )
        if prepared is not None:
            self._overlay.discard_prepared(prepared)
        if on_settled is not None:
            on_settled(False)  # noqa: FBT003  # the settlement flag is the whole payload
