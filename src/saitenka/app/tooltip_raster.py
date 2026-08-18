"""Bounded tooltip viewport raster jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

log = logging.getLogger(__name__)


class RasterPanel(Protocol):
    def viewport(self, scroll: int, view_h: int, *, scale: float = 1.0) -> object: ...

    def render_ahead(
        self,
        scroll: int,
        view_h: int,
        *,
        direction: int,
        should_cancel: Callable[[], bool],
        scale: float = 1.0,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class RenderAheadRequest:
    panel: RasterPanel
    scroll: int
    view_h: int
    direction: int
    scale: float
    superseded: threading.Event


@dataclass(frozen=True, slots=True)
class RenderAheadIdentity:
    sequence: int
    generation: int
    panel_id: int
    scroll: int
    job_id: int | None


@dataclass(slots=True)
class RenderAheadState:
    sequence: int = 0
    inflight: tuple[RenderAheadIdentity, RenderAheadRequest] | None = None
    pending: tuple[RenderAheadIdentity, RenderAheadRequest] | None = None
    closed: bool = False


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool: ...


def run_render_ahead(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, RenderAheadRequest):
        raise TypeError("invalid render-ahead request")
    should_cancel = lambda: cancelled.is_set() or request.superseded.is_set()  # noqa: E731
    if should_cancel():
        return None
    try:
        if request.scale > 1.0:
            request.panel.viewport(request.scroll, request.view_h, scale=request.scale)
        if should_cancel():
            return None
        request.panel.render_ahead(
            request.scroll,
            request.view_h,
            direction=request.direction,
            should_cancel=should_cancel,
            scale=request.scale,
        )
        if request.scale > 1.0 and not should_cancel():
            request.panel.render_ahead(
                request.scroll,
                request.view_h,
                direction=request.direction,
                should_cancel=should_cancel,
                scale=1.0,
            )
    except Exception:
        log.debug("render-ahead failed", exc_info=True)
        raise
    return request


def configure_runtime_job(ipc) -> JobSubmitter | None:
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(
        "tooltip-render-ahead",
        JobLanePolicy(capacity=1),
        run_render_ahead,
    ):
        return None
    return ipc.submit_runtime_job


def request(
    state: RenderAheadState,
    work: RenderAheadRequest,
    *,
    generation: int,
    job_id: int | None,
    submit: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> bool:
    if state.closed or submit is None:
        return False
    if state.inflight is not None:
        state.inflight[1].superseded.set()
    if state.pending is not None:
        state.pending[1].superseded.set()
    state.sequence += 1
    identity = RenderAheadIdentity(state.sequence, generation, id(work.panel), work.scroll, job_id)
    state.pending = (identity, work)
    _submit_pending(state, submit, on_finished)
    return True


def finish(
    state: RenderAheadState,
    completion: EffectFinished,
    submit: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> tuple[RenderAheadIdentity, RenderAheadRequest, bool] | None:
    current = state.inflight
    if current is None or completion.identity != current[0]:
        return None
    state.inflight = None
    _submit_pending(state, submit, on_finished)
    return (
        current[0],
        current[1],
        completion.outcome is EffectOutcome.SUCCEEDED and not current[1].superseded.is_set(),
    )


def cancel(state: RenderAheadState) -> None:
    if state.inflight is not None:
        state.inflight[1].superseded.set()
    if state.pending is not None:
        state.pending[1].superseded.set()
    state.pending = None


def close(state: RenderAheadState) -> None:
    cancel(state)
    state.closed = True
    state.inflight = None


def _submit_pending(
    state: RenderAheadState,
    submit: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> None:
    if state.closed or state.inflight is not None or state.pending is None or submit is None:
        return
    current = state.pending
    state.pending = None
    state.inflight = current
    accepted = submit(
        owner=Owner.INTERACTION,
        identity=current[0],
        lane="tooltip-render-ahead",
        request=current[1],
        on_finished=on_finished,
    )
    if not accepted and state.inflight == current:
        state.inflight = None
        log.debug("render-ahead admission rejected")
