"""Lifecycle-owned OSD surfaces over revision-fenced runtime slots."""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

from saitenka.runtime import EffectError, EffectFinished, EffectOutcome, Owner
from saitenka.runtime.surfaces import (
    SurfaceAction,
    SurfaceRuntime,
    SurfaceTransaction,
    SurfaceTransactionOutcome,
)

if TYPE_CHECKING:
    from PIL import Image

    from saitenka.mpvio.osd import Overlay, PreparedOverlay

_SURFACE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


class LifecycleSurfaces:
    """Own loading and toast presentation transactions."""

    def __init__(self, overlay: Overlay) -> None:
        self._overlay = overlay
        self._runtime = SurfaceRuntime()
        self._request_lock = Lock()

    def present(self, img: Image.Image, x: int, y: int, *, oid: int) -> SurfaceTransaction:
        with self._request_lock:
            transaction = self._runtime.request(str(oid), SurfaceAction.PRESENT)
            prepared = None
            try:
                prepared = self._overlay.prepare(img, x, y, oid=oid, revision=transaction.revision)
                self._submit(transaction, prepared.command, prepared)
            except _SURFACE_ERRORS:
                self._fail(transaction, prepared)
        return transaction

    def remove(self, oid: int) -> SurfaceTransaction:
        with self._request_lock:
            transaction = self._runtime.request(str(oid), SurfaceAction.REMOVE)
            try:
                self._submit(transaction, ("overlay-remove", self._overlay.physical_oid(oid)), None)
            except _SURFACE_ERRORS:
                self._fail(transaction, None)
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

    def snapshot(self, oid: int):
        return self._runtime.snapshot(str(oid))

    def _submit(
        self,
        transaction: SurfaceTransaction,
        command: tuple[object, ...],
        prepared: PreparedOverlay | None,
    ) -> None:
        def finished(completion: EffectFinished) -> None:
            accepted = self._runtime.finish(
                SurfaceTransactionOutcome(transaction, completion.outcome, completion.error)
            )
            if not accepted:
                if prepared is not None:
                    self._overlay.discard_prepared(prepared)
                return
            if completion.outcome is EffectOutcome.SUCCEEDED:
                if prepared is None:
                    self._overlay.commit_remove(int(transaction.slot))
                else:
                    self._overlay.commit_prepared(prepared)
            elif prepared is not None:
                self._overlay.discard_prepared(prepared)

        self._overlay.submit_surface_transaction(
            owner=Owner.PRESENTATION,
            identity=transaction,
            command=command,
            on_finished=finished,
        )

    def _fail(self, transaction: SurfaceTransaction, prepared: PreparedOverlay | None) -> None:
        self._runtime.finish(
            SurfaceTransactionOutcome(
                transaction,
                EffectOutcome.FAILED,
                EffectError.INTERNAL,
            )
        )
        if prepared is not None:
            self._overlay.discard_prepared(prepared)
