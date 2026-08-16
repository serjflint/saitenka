"""Pure subtitle pixel-ownership policy.

Geometry availability never selects the subtitle pixel renderer.  Only an explicit mode
transition or a current assert-true/readback-false result may authorize legacy pixels.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

OWNERSHIP_RETRY_DELAYS_MS = (50, 250, 1_000)


class Lifecycle(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class OwnershipMode(StrEnum):
    NATIVE_VISIBLE = "native-visible"
    LEGACY_OVERLAY = "legacy-overlay"
    PASSTHROUGH = "passthrough"


class PixelOwner(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    NATIVE = "native"
    LEGACY = "legacy"


class Visibility(StrEnum):
    UNKNOWN = "unknown"
    TRUE = "true"
    FALSE = "false"


class EventKind(StrEnum):
    ENSURE_MODE = "ensure-mode"
    VERIFY_NATIVE = "verify-native"
    SELECTION_CHANGED = "selection-changed"
    CUE_CHANGED = "cue-changed"
    GEOMETRY_READY = "geometry-ready"
    GEOMETRY_DEGRADED = "geometry-degraded"
    ASSERTION_RESULT = "assertion-result"
    LEGACY_STAGE_RESULT = "legacy-stage-result"
    RETRY_DUE = "retry-due"
    MODE_CHANGED = "mode-changed"
    CONNECTION_REPLACED = "connection-replaced"
    CLOSE_REQUESTED = "close-requested"
    CLOSE_FINISHED = "close-finished"


class ActionKind(StrEnum):
    ASSERT_NATIVE_VISIBILITY = "assert-native-visibility"
    STAGE_LEGACY = "stage-legacy"
    CLEAR_LEGACY = "clear-legacy"
    CLEAR_INTERACTION = "clear-interaction"
    SHOW_MPV = "show-mpv"
    RESTORE_VISIBILITY = "restore-visibility"
    SCHEDULE_RETRY = "schedule-retry"
    CANCEL_RETRY = "cancel-retry"


@dataclass(frozen=True, slots=True)
class OwnershipContext:
    connection_epoch: int
    ownership_epoch: int
    mode: OwnershipMode
    selection: str | None


@dataclass(frozen=True, slots=True)
class OwnershipState:
    lifecycle: Lifecycle = Lifecycle.OPEN
    context: OwnershipContext = OwnershipContext(0, 0, OwnershipMode.NATIVE_VISIBLE, None)
    owner: PixelOwner = PixelOwner.UNKNOWN
    visibility: Visibility = Visibility.UNKNOWN
    native_pixels_established: bool = False
    nonempty: bool = False
    geometry_ready: bool = False
    next_effect_id: int = 1
    active_assertion_id: int | None = None
    active_effect_kind: ActionKind | None = None
    retry_attempts_used: int = 0
    retry_effect_id: int | None = None
    retry_suspended: bool = False
    retry_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class OwnershipEvent:
    kind: EventKind
    context: OwnershipContext | None = None
    effect_id: int | None = None
    visibility: Visibility = Visibility.UNKNOWN
    mode: OwnershipMode | None = None
    nonempty: bool | None = None
    accepted: bool | None = None


@dataclass(frozen=True, slots=True)
class OwnershipAction:
    kind: ActionKind
    effect_id: int | None = None
    context: OwnershipContext | None = None
    delay_ms: int | None = None


def _assert_native(state: OwnershipState) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    effect_id = state.next_effect_id
    new_state = replace(
        state,
        next_effect_id=effect_id + 1,
        active_assertion_id=effect_id,
        active_effect_kind=ActionKind.ASSERT_NATIVE_VISIBILITY,
        retry_effect_id=None,
        retry_suspended=False,
    )
    return new_state, (
        OwnershipAction(ActionKind.ASSERT_NATIVE_VISIBILITY, effect_id, state.context),
    )


def _schedule_retry(state: OwnershipState) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    if state.retry_attempts_used >= len(OWNERSHIP_RETRY_DELAYS_MS):
        return replace(state, retry_effect_id=None, retry_exhausted=True), ()
    effect_id = state.next_effect_id
    delay = OWNERSHIP_RETRY_DELAYS_MS[state.retry_attempts_used]
    new_state = replace(
        state,
        next_effect_id=effect_id + 1,
        retry_effect_id=effect_id,
        retry_attempts_used=state.retry_attempts_used + 1,
        retry_suspended=False,
    )
    return new_state, (OwnershipAction(ActionKind.SCHEDULE_RETRY, effect_id, state.context, delay),)


def _request_legacy(state: OwnershipState) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    effect_id = state.next_effect_id
    return replace(
        state,
        next_effect_id=effect_id + 1,
        active_assertion_id=effect_id,
        active_effect_kind=ActionKind.STAGE_LEGACY,
    ), (OwnershipAction(ActionKind.STAGE_LEGACY, effect_id, state.context),)


def _start_mode(state: OwnershipState) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    if state.context.mode == OwnershipMode.NATIVE_VISIBLE:
        return _assert_native(state)
    if state.context.mode == OwnershipMode.LEGACY_OVERLAY:
        return _request_legacy(state)
    return replace(state, owner=PixelOwner.NATIVE), (
        OwnershipAction(ActionKind.CLEAR_INTERACTION, context=state.context),
        OwnershipAction(ActionKind.CLEAR_LEGACY, context=state.context),
        OwnershipAction(ActionKind.SHOW_MPV, context=state.context),
    )


def _change_context(
    state: OwnershipState, event: OwnershipEvent
) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    context = event.context
    if context is None:
        return state, ()
    established = bool(
        event.kind == EventKind.SELECTION_CHANGED
        and context.mode == OwnershipMode.NATIVE_VISIBLE
        and state.native_pixels_established
    )
    new_state = replace(
        state,
        context=context,
        owner=PixelOwner.NATIVE if established else PixelOwner.UNKNOWN,
        visibility=Visibility.TRUE if established else Visibility.UNKNOWN,
        native_pixels_established=established,
        geometry_ready=False,
        active_assertion_id=None,
        active_effect_kind=None,
        retry_attempts_used=0,
        retry_effect_id=None,
        retry_suspended=False,
        retry_exhausted=False,
    )
    started, actions = _start_mode(new_state)
    return started, (OwnershipAction(ActionKind.CANCEL_RETRY, context=state.context), *actions)


def _ensure_mode(
    state: OwnershipState, *, verify: bool
) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    unavailable = state.owner == PixelOwner.LEGACY or state.retry_exhausted
    already_native = state.native_pixels_established and not verify
    wrong_mode = verify and state.context.mode != OwnershipMode.NATIVE_VISIBLE
    if unavailable or already_native or wrong_mode:
        return state, ()
    return _assert_native(state) if verify else _start_mode(state)


def _cue_changed(
    state: OwnershipState, event: OwnershipEvent
) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    new_state = replace(state, nonempty=bool(event.nonempty))
    if new_state.nonempty and state.retry_suspended and not state.retry_exhausted:
        return _schedule_retry(replace(new_state, retry_suspended=False))
    if new_state.nonempty:
        return new_state, ()
    actions = (OwnershipAction(ActionKind.CLEAR_INTERACTION, context=state.context),)
    if state.retry_effect_id is None:
        return new_state, actions
    return replace(new_state, retry_effect_id=None, retry_suspended=True), (
        OwnershipAction(ActionKind.CANCEL_RETRY, context=state.context),
        *actions,
    )


def _assertion_result(
    state: OwnershipState, event: OwnershipEvent
) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    if (
        event.context != state.context
        or event.effect_id != state.active_assertion_id
        or state.active_effect_kind != ActionKind.ASSERT_NATIVE_VISIBILITY
    ):
        return state, ()
    current = replace(state, visibility=event.visibility)
    if event.visibility == Visibility.TRUE:
        return replace(
            current,
            active_assertion_id=None,
            active_effect_kind=None,
            owner=PixelOwner.NATIVE,
            native_pixels_established=True,
            retry_attempts_used=0,
            retry_effect_id=None,
            retry_suspended=False,
            retry_exhausted=False,
        ), (OwnershipAction(ActionKind.CLEAR_LEGACY, context=state.context),)
    if event.visibility == Visibility.FALSE and state.nonempty:
        return replace(
            current,
            owner=PixelOwner.UNKNOWN,
            active_effect_kind=ActionKind.STAGE_LEGACY,
        ), (OwnershipAction(ActionKind.STAGE_LEGACY, state.active_assertion_id, state.context),)
    return _schedule_retry(
        replace(
            current,
            owner=PixelOwner.UNKNOWN,
            active_assertion_id=None,
            active_effect_kind=None,
        )
    )


def _legacy_stage_result(
    state: OwnershipState, event: OwnershipEvent
) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    if (
        event.context != state.context
        or event.effect_id != state.active_assertion_id
        or state.active_effect_kind != ActionKind.STAGE_LEGACY
    ):
        return state, ()
    current = replace(state, active_assertion_id=None, active_effect_kind=None)
    if event.accepted:
        return replace(current, owner=PixelOwner.LEGACY, visibility=Visibility.FALSE), ()
    return _schedule_retry(replace(current, owner=PixelOwner.UNKNOWN))


OwnershipResult = tuple[OwnershipState, tuple[OwnershipAction, ...]]
EventHandler = Callable[[OwnershipState, OwnershipEvent], OwnershipResult]


def _close_requested(state: OwnershipState, _event: OwnershipEvent) -> OwnershipResult:
    return replace(
        state,
        lifecycle=Lifecycle.CLOSING,
        active_assertion_id=None,
        active_effect_kind=None,
    ), (
        OwnershipAction(ActionKind.CANCEL_RETRY, context=state.context),
        OwnershipAction(ActionKind.CLEAR_INTERACTION, context=state.context),
        OwnershipAction(ActionKind.CLEAR_LEGACY, context=state.context),
        OwnershipAction(ActionKind.RESTORE_VISIBILITY, context=state.context),
    )


def _ensure_mode_event(state: OwnershipState, _event: OwnershipEvent) -> OwnershipResult:
    return _ensure_mode(state, verify=False)


def _verify_native_event(state: OwnershipState, _event: OwnershipEvent) -> OwnershipResult:
    return _ensure_mode(state, verify=True)


def _geometry_degraded(state: OwnershipState, _event: OwnershipEvent) -> OwnershipResult:
    return replace(state, geometry_ready=False), (
        OwnershipAction(ActionKind.CLEAR_INTERACTION, context=state.context),
    )


def _geometry_ready(state: OwnershipState, _event: OwnershipEvent) -> OwnershipResult:
    return replace(state, geometry_ready=True), ()


def _retry_due(state: OwnershipState, event: OwnershipEvent) -> OwnershipResult:
    if event.context != state.context or event.effect_id != state.retry_effect_id:
        return state, ()
    return _start_mode(replace(state, retry_effect_id=None))


_OPEN_HANDLERS: dict[EventKind, EventHandler] = {
    EventKind.CLOSE_REQUESTED: _close_requested,
    EventKind.ENSURE_MODE: _ensure_mode_event,
    EventKind.VERIFY_NATIVE: _verify_native_event,
    EventKind.CUE_CHANGED: _cue_changed,
    EventKind.GEOMETRY_DEGRADED: _geometry_degraded,
    EventKind.GEOMETRY_READY: _geometry_ready,
    EventKind.ASSERTION_RESULT: _assertion_result,
    EventKind.LEGACY_STAGE_RESULT: _legacy_stage_result,
    EventKind.RETRY_DUE: _retry_due,
}

_CONTEXT_EVENTS = frozenset(
    {
        EventKind.MODE_CHANGED,
        EventKind.CONNECTION_REPLACED,
        EventKind.SELECTION_CHANGED,
    }
)


def reduce_ownership(
    state: OwnershipState, event: OwnershipEvent
) -> tuple[OwnershipState, tuple[OwnershipAction, ...]]:
    """Reduce one ownership fact into state and ordered effects."""
    if state.lifecycle == Lifecycle.CLOSED:
        return state, ()
    if state.lifecycle == Lifecycle.CLOSING:
        if event.kind == EventKind.CLOSE_FINISHED:
            return replace(state, lifecycle=Lifecycle.CLOSED), ()
        return state, ()

    if event.kind in _CONTEXT_EVENTS:
        return _change_context(state, event)
    handler = _OPEN_HANDLERS.get(event.kind)
    return handler(state, event) if handler is not None else (state, ())


def assert_ownership_invariants(state: OwnershipState) -> None:
    """Executable invariants used by production and the stateful test."""
    if state.lifecycle == Lifecycle.CLOSED:
        assert state.active_assertion_id is None
    if state.owner == PixelOwner.NATIVE:
        assert state.context.mode != OwnershipMode.LEGACY_OVERLAY
    if state.native_pixels_established:
        assert state.context.mode == OwnershipMode.NATIVE_VISIBLE
    if state.retry_effect_id is not None:
        assert not state.retry_suspended
        assert 1 <= state.retry_attempts_used <= len(OWNERSHIP_RETRY_DELAYS_MS)
    if state.retry_exhausted:
        assert state.retry_attempts_used == len(OWNERSHIP_RETRY_DELAYS_MS)
