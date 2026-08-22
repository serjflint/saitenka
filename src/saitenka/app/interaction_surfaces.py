"""Revision-fenced presentation for the interactive slots (tooltip, nested popup).

Shares :class:`SurfaceRuntime` with the lifecycle path — one fence, so "which paint is current" has
a single representation — but keeps the interactive transport: BGRA staged on the presenter thread,
newest-wins per overlay id, so a 60 FPS scroll never blocks the reactor. The lifecycle path's
synchronous ``prepare`` (a temp file per revision) would.

What the fence adds is the answer the transport cannot give: a paint acknowledgement arriving after
a newer paint or a hide was requested is *stale*, and settling its caller would report pixels that
are no longer on screen. `InteractionJobs` does not cover this — it is telemetry keyed by intent
kind ("tooltip" / "scroll"), a different fact from what a slot currently shows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.runtime.effects import EffectOutcome
from saitenka.runtime.surfaces import (
    SurfaceAction,
    SurfaceRuntime,
    SurfaceTransaction,
    SurfaceTransactionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

_ACCEPTED_REPLIES = {None, "success", "deferred"}


class InteractionSurfaces:
    """Own the tooltip and nested-popup presentation transactions."""

    def __init__(self, overlay) -> None:
        self._overlay = overlay
        self._runtime = SurfaceRuntime()

    def present_bgra(
        self,
        view: np.ndarray,
        x: int,
        y: int,
        *,
        oid: int,
        on_settled: Callable[..., None] | None = None,
    ) -> SurfaceTransaction:
        transaction = self._runtime.request(str(oid), SurfaceAction.PRESENT)

        def presented(reply: dict) -> None:
            self._finish(transaction, reply, on_settled)

        self._overlay.show_bgra_interactive(view, x, y, oid=oid, on_presented=presented)
        return transaction

    def remove(self, oid: int) -> SurfaceTransaction:
        transaction = self._runtime.request(str(oid), SurfaceAction.REMOVE)
        reply = self._overlay.hide_interactive(oid)
        self._finish(transaction, reply if isinstance(reply, dict) else {}, None)
        return transaction

    def snapshot(self, oid: int):
        return self._runtime.snapshot(str(oid))

    def settled(self) -> bool:
        return self._runtime.settled()

    def _finish(
        self,
        transaction: SurfaceTransaction,
        reply: dict,
        on_settled: Callable[..., None] | None,
    ) -> None:
        succeeded = reply.get("error") in _ACCEPTED_REPLIES
        accepted = self._runtime.finish(
            SurfaceTransactionOutcome(
                transaction,
                EffectOutcome.SUCCEEDED if succeeded else EffectOutcome.FAILED,
            )
        )
        # A superseded revision's caller is no longer waiting on an answer, and reporting one would
        # describe pixels a newer paint has already replaced.
        if accepted and on_settled is not None:
            on_settled(painted=succeeded)
