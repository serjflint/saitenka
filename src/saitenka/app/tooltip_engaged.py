"""Bounded background work for the tooltip the user is engaging now."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from saitenka import otel_metrics
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.popups import Panel
    from saitenka.app.prefetch import TipScale
    from saitenka.app.tokenize import Token

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HoverRequest:
    token: Token
    inflected: str
    mined: bool
    key: tuple
    cap: int
    phrase: tuple = ()
    nested: bool = False
    tail: str = ""
    job_id: int | None = None


@dataclass(frozen=True, slots=True)
class NavigateRequest:
    query: str
    origin: int


@dataclass(frozen=True, slots=True)
class OpenRequest:
    source: str
    query: str
    anchor: tuple[float, float, float]
    origin: int
    mined: bool = False


EngagedRequest = HoverRequest | NavigateRequest | OpenRequest


@dataclass(frozen=True, slots=True)
class HoverReady:
    key: tuple
    nested: bool
    tail: str
    job_id: int | None


@dataclass(frozen=True, slots=True)
class NavigateReady:
    origin: int
    panel: Panel | None


@dataclass(frozen=True, slots=True)
class OpenReady:
    source: str
    query: str
    anchor: tuple[float, float, float]
    origin: int


EngagedResult = HoverReady | NavigateReady | OpenReady


@dataclass(frozen=True, slots=True)
class EngagedIdentity:
    sequence: int
    generation: int
    kind: str


@dataclass(frozen=True, slots=True)
class EngagedWork:
    request: EngagedRequest
    superseded: threading.Event


@dataclass(slots=True)
class EngagedState:
    sequence: int = 0
    inflight: tuple[EngagedIdentity, EngagedWork] | None = None
    pending: tuple[EngagedIdentity, EngagedWork] | None = None
    closed: bool = False


class EngagedBackend(Protocol):
    def hover(self, request: HoverRequest, should_cancel: Callable[[], bool]) -> None: ...

    def navigate(
        self, request: NavigateRequest, should_cancel: Callable[[], bool]
    ) -> Panel | None: ...

    def open(self, request: OpenRequest, should_cancel: Callable[[], bool]) -> None: ...


class EngagedHost(Protocol):
    nested_max_frac: float
    tip_scale: TipScale

    def _panel_for(self, token, inflected, **kwargs) -> Panel: ...

    def _worker_seed_head(self, panel, token, inflected, **kwargs) -> bool: ...

    def _precompose_head(self, panel, token, inflected, **kwargs) -> None: ...

    def _mem_fill(self, token, inflected, **kwargs) -> None: ...

    def _cap_for(self, fraction: float) -> int: ...

    def _navigated_panel(self, query: str) -> Panel | None: ...

    def _engaged_open_panel(self, source: str, query: str, **kwargs) -> tuple | None: ...


class ReaderEngagedBackend:
    """Background rendering adapter over the panel/cache seams."""

    def __init__(self, host: object) -> None:
        self._reader = cast("EngagedHost", host)

    def hover(self, request: HoverRequest, should_cancel: Callable[[], bool]) -> None:
        reader = self._reader
        panel = reader._panel_for(
            request.token,
            request.inflected,
            min_h=request.cap,
            mined=request.mined,
            nested=request.nested,
            extra_terms=request.phrase,
        )
        if should_cancel():
            return
        if not request.nested:
            if not reader._worker_seed_head(
                panel,
                request.token,
                request.inflected,
                mined=request.mined,
                cap=request.cap,
            ):
                reader._precompose_head(
                    panel,
                    request.token,
                    request.inflected,
                    mined=request.mined,
                    cap=request.cap,
                )
                if not should_cancel():
                    reader._mem_fill(request.token, request.inflected, mined=request.mined)
            return
        view_h = min(panel.full_height, reader._cap_for(reader.nested_max_frac))
        if view_h <= 0 or should_cancel():
            return
        scale = reader.tip_scale.raster
        if scale > 1.0:
            panel.viewport(0, view_h, overscan=view_h, scale=scale)
        if not should_cancel():
            panel.viewport(0, view_h, overscan=view_h)

    def navigate(self, request: NavigateRequest, should_cancel: Callable[[], bool]) -> Panel | None:
        reader = self._reader
        panel = reader._navigated_panel(request.query)
        if panel is None or should_cancel():
            return panel
        panel.render_head(reader.tip_scale.cap)
        view_h = min(panel.full_height, reader.tip_scale.cap)
        if view_h <= 0 or should_cancel():
            return panel
        scale = reader.tip_scale.raster
        if scale > 1.0:
            panel.viewport(0, view_h, overscan=view_h, scale=scale)
        if not should_cancel():
            panel.viewport(0, view_h, overscan=view_h)
        return panel

    def open(self, request: OpenRequest, should_cancel: Callable[[], bool]) -> None:
        reader = self._reader
        built = reader._engaged_open_panel(request.source, request.query, mined=request.mined)
        if built is None or should_cancel():
            return
        panel = built[0]
        view_h = min(panel.full_height, reader._cap_for(reader.nested_max_frac))
        if view_h <= 0:
            return
        scale = reader.tip_scale.raster
        if scale > 1.0:
            panel.viewport(0, view_h, overscan=view_h, scale=scale)
        if not should_cancel():
            panel.viewport(0, view_h, overscan=view_h)


def run_engaged(
    work: object, cancelled: threading.Event, backend: EngagedBackend
) -> EngagedResult | None:
    if not isinstance(work, EngagedWork):
        raise TypeError("invalid engaged-tooltip request")
    should_cancel = lambda: cancelled.is_set() or work.superseded.is_set()  # noqa: E731
    if should_cancel():
        return None
    request = work.request
    try:
        if isinstance(request, HoverRequest):
            with otel_metrics.traced(
                "prefetch_decode", kind="engaged_nested" if request.nested else "engaged"
            ):
                backend.hover(request, should_cancel)
            if should_cancel():
                return None
            return HoverReady(request.key, request.nested, request.tail, request.job_id)
        if isinstance(request, NavigateRequest):
            with otel_metrics.traced("prefetch_decode", kind="engaged_nav"):
                panel = backend.navigate(request, should_cancel)
            return None if should_cancel() else NavigateReady(request.origin, panel)
        if isinstance(request, OpenRequest):
            with otel_metrics.traced("prefetch_decode", kind="engaged_open"):
                backend.open(request, should_cancel)
            return (
                None
                if should_cancel()
                else OpenReady(request.source, request.query, request.anchor, request.origin)
            )
    except Exception:
        log.debug("engaged tooltip work failed", exc_info=True)
        raise
    raise TypeError("invalid engaged-tooltip request")


def configure_runtime_job(ipc, backend: EngagedBackend) -> JobSubmitter | None:
    return configure_lane(
        ipc,
        "tooltip-engaged",
        JobLanePolicy(capacity=1),
        lambda request, cancelled: run_engaged(request, cancelled, backend),
    )


def submit(
    state: EngagedState,
    request: EngagedRequest,
    *,
    generation: int,
    submitter: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> bool:
    if state.closed or submitter is None:
        return False
    if state.inflight is not None:
        state.inflight[1].superseded.set()
    if state.pending is not None:
        state.pending[1].superseded.set()
    state.sequence += 1
    identity = EngagedIdentity(state.sequence, generation, type(request).__name__)
    state.pending = (identity, EngagedWork(request, threading.Event()))
    accepted, _rejected = _submit_pending(state, submitter, on_finished)
    return accepted


def finish(
    state: EngagedState,
    completion: EffectFinished,
    submitter: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> (
    tuple[
        EngagedIdentity,
        EngagedRequest,
        EngagedResult | None,
        bool,
        bool,
        tuple[EngagedIdentity, EngagedRequest] | None,
    ]
    | None
):
    current = state.inflight
    if current is None or completion.identity != current[0]:
        return None
    state.inflight = None
    _accepted, rejected = _submit_pending(state, submitter, on_finished)
    result = completion.result
    superseded = current[1].superseded.is_set()
    succeeded = (
        completion.outcome is EffectOutcome.SUCCEEDED
        and isinstance(result, HoverReady | NavigateReady | OpenReady)
        and not superseded
    )
    published = result if isinstance(result, HoverReady | NavigateReady | OpenReady) else None
    return (
        current[0],
        current[1].request,
        published if succeeded else None,
        succeeded,
        superseded,
        rejected,
    )


def cancel(state: EngagedState) -> None:
    if state.inflight is not None:
        state.inflight[1].superseded.set()
    if state.pending is not None:
        state.pending[1].superseded.set()
    state.pending = None


def close(state: EngagedState) -> None:
    cancel(state)
    state.closed = True
    state.inflight = None


def _submit_pending(
    state: EngagedState,
    submitter: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> tuple[bool, tuple[EngagedIdentity, EngagedRequest] | None]:
    if state.closed or state.inflight is not None or state.pending is None or submitter is None:
        return state.inflight is not None, None
    current = state.pending
    state.pending = None
    state.inflight = current
    accepted = submitter(
        owner=Owner.INTERACTION,
        identity=current[0],
        lane="tooltip-engaged",
        request=current[1],
        on_finished=on_finished,
    )
    if not accepted and state.inflight == current:
        state.inflight = None
        log.debug("engaged-tooltip admission rejected")
        return False, (current[0], current[1].request)
    return accepted, None
