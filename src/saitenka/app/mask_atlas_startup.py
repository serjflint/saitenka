"""Bounded startup activation for the optional persistent glyph atlas."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from saitenka.mask_atlas import MaskAtlas

_LANE = "mask-atlas-startup"


@dataclass(frozen=True, slots=True)
class MaskAtlasRequest:
    enabled: bool
    path: Path


@dataclass(frozen=True, slots=True)
class OpenedMaskAtlas:
    atlas: MaskAtlas


@dataclass(slots=True)
class ActivationState:
    generation: int = 0
    inflight: bool = False
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def open_mask_atlas(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, MaskAtlasRequest):
        raise TypeError("invalid mask-atlas request")
    if not request.enabled or cancelled.is_set() or not request.path.exists():
        return None
    from saitenka.mask_atlas import MaskAtlas

    atlas = MaskAtlas.open(request.path)
    if atlas is None:
        return None
    if cancelled.is_set():
        atlas.close()
        return None
    return OpenedMaskAtlas(atlas)


def configure_runtime_job(ipc) -> JobSubmitter | None:
    return configure_lane(
        ipc,
        _LANE,
        JobLanePolicy(capacity=1, workers=1),
        open_mask_atlas,
    )


def request(
    state: ActivationState,
    activation: MaskAtlasRequest,
    submit: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> bool:
    if submit is None:
        return False
    with state.lock:
        if state.closed or state.inflight:
            return False
        state.generation += 1
        generation = state.generation
        state.inflight = True
    accepted = submit(
        owner=Owner.SESSION,
        identity=(_LANE, generation),
        lane=_LANE,
        request=activation,
        on_finished=on_finished,
    )
    if accepted:
        return True
    with state.lock:
        if generation == state.generation:
            state.inflight = False
    return False


def finish(state: ActivationState, completion: EffectFinished) -> OpenedMaskAtlas | None:
    identity = completion.identity
    result = completion.result
    generation = (
        identity[1]
        if isinstance(identity, tuple)
        and len(identity) == 2
        and identity[0] == _LANE
        and isinstance(identity[1], int)
        else None
    )
    with state.lock:
        current = (
            generation == state.generation
            and generation is not None
            and state.inflight
            and not state.closed
        )
        if current:
            state.inflight = False
    if current and completion.outcome is EffectOutcome.SUCCEEDED:
        return result if isinstance(result, OpenedMaskAtlas) else None
    if isinstance(result, OpenedMaskAtlas):
        result.atlas.close()
    return None


def close(state: ActivationState) -> None:
    with state.lock:
        state.closed = True
        state.generation += 1
        state.inflight = False
