from __future__ import annotations

import ast
from pathlib import Path

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from saitenka.app.subtitle_ownership import (
    ActionKind,
    EventKind,
    Lifecycle,
    OwnershipAction,
    OwnershipContext,
    OwnershipEvent,
    OwnershipMode,
    OwnershipState,
    PixelOwner,
    Visibility,
    assert_ownership_invariants,
    reduce_ownership,
)


def _native_state() -> OwnershipState:
    return OwnershipState(
        context=OwnershipContext(0, 1, OwnershipMode.NATIVE_VISIBLE, "sid:2"),
        owner=PixelOwner.NATIVE,
        visibility=Visibility.TRUE,
        native_pixels_established=True,
        nonempty=True,
        geometry_ready=True,
    )


def test_geometry_degradation_cannot_select_legacy_pixels() -> None:
    state, actions = reduce_ownership(_native_state(), OwnershipEvent(EventKind.GEOMETRY_DEGRADED))

    assert state.owner == PixelOwner.NATIVE
    assert state.native_pixels_established
    assert actions == (OwnershipAction(ActionKind.CLEAR_INTERACTION, context=state.context),)


def test_only_current_false_readback_can_request_catastrophic_legacy() -> None:
    state, actions = reduce_ownership(
        _native_state(),
        OwnershipEvent(
            EventKind.MODE_CHANGED,
            context=OwnershipContext(0, 2, OwnershipMode.NATIVE_VISIBLE, "sid:2"),
        ),
    )
    assertion = actions[-1]
    assert assertion.kind == ActionKind.ASSERT_NATIVE_VISIBILITY

    stale_state, stale_actions = reduce_ownership(
        state,
        OwnershipEvent(
            EventKind.ASSERTION_RESULT,
            context=OwnershipContext(0, 1, OwnershipMode.NATIVE_VISIBLE, "sid:2"),
            effect_id=assertion.effect_id,
            visibility=Visibility.FALSE,
        ),
    )
    failed_state, failed_actions = reduce_ownership(
        state,
        OwnershipEvent(
            EventKind.ASSERTION_RESULT,
            context=state.context,
            effect_id=assertion.effect_id,
            visibility=Visibility.FALSE,
        ),
    )

    assert stale_state == state and stale_actions == ()
    assert failed_state.owner == PixelOwner.UNKNOWN
    assert failed_actions == (
        OwnershipAction(
            ActionKind.STAGE_LEGACY,
            assertion.effect_id,
            state.context,
        ),
    )

    committed, commit_actions = reduce_ownership(
        failed_state,
        OwnershipEvent(
            EventKind.LEGACY_STAGE_RESULT,
            context=failed_state.context,
            effect_id=assertion.effect_id,
            accepted=True,
        ),
    )
    assert committed.owner == PixelOwner.LEGACY
    assert commit_actions == ()


def test_failed_legacy_stage_rolls_back_to_bounded_retry() -> None:
    context = OwnershipContext(0, 1, OwnershipMode.NATIVE_VISIBLE, "sid:2")
    state, actions = reduce_ownership(
        OwnershipState(context=context, nonempty=True), OwnershipEvent(EventKind.ENSURE_MODE)
    )
    assertion_id = actions[-1].effect_id
    state, actions = reduce_ownership(
        state,
        OwnershipEvent(
            EventKind.ASSERTION_RESULT,
            context=context,
            effect_id=assertion_id,
            visibility=Visibility.FALSE,
        ),
    )
    state, retry = reduce_ownership(
        state,
        OwnershipEvent(
            EventKind.LEGACY_STAGE_RESULT,
            context=context,
            effect_id=actions[-1].effect_id,
            accepted=False,
        ),
    )

    assert state.owner == PixelOwner.UNKNOWN
    assert retry[0].kind == ActionKind.SCHEDULE_RETRY
    assert retry[0].delay_ms == 50


def test_false_readback_during_empty_interval_cannot_stage_legacy() -> None:
    context = OwnershipContext(0, 1, OwnershipMode.NATIVE_VISIBLE, "sid:2")
    state, actions = reduce_ownership(
        OwnershipState(context=context), OwnershipEvent(EventKind.ENSURE_MODE)
    )
    state, result_actions = reduce_ownership(
        state,
        OwnershipEvent(
            EventKind.ASSERTION_RESULT,
            context=context,
            effect_id=actions[-1].effect_id,
            visibility=Visibility.FALSE,
        ),
    )

    assert state.owner == PixelOwner.UNKNOWN
    assert result_actions[0].kind == ActionKind.SCHEDULE_RETRY
    assert all(action.kind != ActionKind.STAGE_LEGACY for action in result_actions)


def test_initial_false_that_becomes_true_never_stages_legacy() -> None:
    context = OwnershipContext(0, 1, OwnershipMode.NATIVE_VISIBLE, "sid:2")
    state, actions = reduce_ownership(
        OwnershipState(nonempty=True), OwnershipEvent(EventKind.MODE_CHANGED, context=context)
    )
    state, result_actions = reduce_ownership(
        state,
        OwnershipEvent(
            EventKind.ASSERTION_RESULT,
            context=context,
            effect_id=actions[-1].effect_id,
            visibility=Visibility.TRUE,
        ),
    )

    assert state.owner == PixelOwner.NATIVE
    assert state.native_pixels_established
    assert all(action.kind != ActionKind.STAGE_LEGACY for action in result_actions)


def test_empty_intervals_do_not_reset_retry_budget() -> None:
    context = OwnershipContext(0, 1, OwnershipMode.NATIVE_VISIBLE, "sid:2")
    state = OwnershipState(context=context, nonempty=True)
    state, actions = reduce_ownership(state, OwnershipEvent(EventKind.ENSURE_MODE))

    state, actions = reduce_ownership(
        state,
        OwnershipEvent(
            EventKind.ASSERTION_RESULT,
            context=context,
            effect_id=actions[-1].effect_id,
            visibility=Visibility.UNKNOWN,
        ),
    )
    scheduled: list[int] = []
    for expected_attempts in (1, 2, 3):
        assert state.retry_attempts_used == expected_attempts
        assert actions[-1].delay_ms == (50, 250, 1_000)[expected_attempts - 1]
        retry_id = actions[-1].effect_id
        assert retry_id is not None
        scheduled.append(retry_id)
        state, _ = reduce_ownership(state, OwnershipEvent(EventKind.CUE_CHANGED, nonempty=False))
        state, actions = reduce_ownership(
            state, OwnershipEvent(EventKind.CUE_CHANGED, nonempty=True)
        )
        if expected_attempts < 3:
            assert actions[-1].kind == ActionKind.SCHEDULE_RETRY
        else:
            assert actions == ()
            assert state.retry_exhausted
        stale_state, stale_actions = reduce_ownership(
            state,
            OwnershipEvent(EventKind.RETRY_DUE, context=context, effect_id=retry_id),
        )
        assert stale_state == state and stale_actions == ()

    assert len(scheduled) == 3


def test_subtitle_pixel_writes_are_allow_listed_to_the_ownership_executor() -> None:
    app_root = Path("src/saitenka/app")
    violations: list[str] = []
    legacy_bypasses: list[str] = []
    for source_path in app_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "command"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "set_property"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "sub-visibility"
                and source_path.name != "subtitle_render.py"
            ):
                violations.append(f"{source_path}:{node.lineno}")
            if isinstance(node.func, ast.Attribute) and node.func.attr == (
                "_use_legacy_subtitle_renderer"
            ):
                legacy_bypasses.append(f"{source_path}:{node.lineno}")

    assert violations == []
    assert legacy_bypasses == []


def test_close_is_monotonic() -> None:
    closing, actions = reduce_ownership(_native_state(), OwnershipEvent(EventKind.CLOSE_REQUESTED))
    changed, changed_actions = reduce_ownership(
        closing,
        OwnershipEvent(
            EventKind.MODE_CHANGED,
            context=OwnershipContext(1, 2, OwnershipMode.LEGACY_OVERLAY, "sid:9"),
        ),
    )
    closed, _ = reduce_ownership(changed, OwnershipEvent(EventKind.CLOSE_FINISHED))

    assert closing.lifecycle == Lifecycle.CLOSING
    assert ActionKind.RESTORE_VISIBILITY in {action.kind for action in actions}
    assert changed == closing and changed_actions == ()
    assert closed.lifecycle == Lifecycle.CLOSED


class OwnershipStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.state = OwnershipState()
        self.last_actions: tuple[OwnershipAction, ...] = ()

    def apply(self, event: OwnershipEvent) -> None:
        previous = self.state
        self.state, self.last_actions = reduce_ownership(self.state, event)
        if previous.native_pixels_established and event.kind in {
            EventKind.CUE_CHANGED,
            EventKind.GEOMETRY_DEGRADED,
            EventKind.GEOMETRY_READY,
        }:
            assert self.state.native_pixels_established
            assert all(action.kind != ActionKind.STAGE_LEGACY for action in self.last_actions)

    @rule(nonempty=st.booleans())
    def cue(self, *, nonempty: bool) -> None:
        self.apply(OwnershipEvent(EventKind.CUE_CHANGED, nonempty=nonempty))

    @rule()
    def degrade_geometry(self) -> None:
        self.apply(OwnershipEvent(EventKind.GEOMETRY_DEGRADED))

    @rule()
    def geometry_ready(self) -> None:
        self.apply(OwnershipEvent(EventKind.GEOMETRY_READY))

    @rule(mode=st.sampled_from(tuple(OwnershipMode)))
    def change_mode(self, mode: OwnershipMode) -> None:
        context = OwnershipContext(
            self.state.context.connection_epoch,
            self.state.context.ownership_epoch + 1,
            mode,
            self.state.context.selection,
        )
        self.apply(OwnershipEvent(EventKind.MODE_CHANGED, context=context))

    @rule(visibility=st.sampled_from(tuple(Visibility)))
    def resolve_assertion(self, visibility: Visibility) -> None:
        effect_id = self.state.active_assertion_id
        if (
            effect_id is None
            or self.state.active_effect_kind != ActionKind.ASSERT_NATIVE_VISIBILITY
        ):
            return
        self.apply(
            OwnershipEvent(
                EventKind.ASSERTION_RESULT,
                context=self.state.context,
                effect_id=effect_id,
                visibility=visibility,
            )
        )

    @rule(accepted=st.booleans())
    def resolve_legacy_stage(self, *, accepted: bool) -> None:
        effect_id = self.state.active_assertion_id
        if effect_id is None or self.state.active_effect_kind != ActionKind.STAGE_LEGACY:
            return
        self.apply(
            OwnershipEvent(
                EventKind.LEGACY_STAGE_RESULT,
                context=self.state.context,
                effect_id=effect_id,
                accepted=accepted,
            )
        )

    @rule()
    def fire_retry(self) -> None:
        effect_id = self.state.retry_effect_id
        if effect_id is None:
            return
        self.apply(
            OwnershipEvent(
                EventKind.RETRY_DUE,
                context=self.state.context,
                effect_id=effect_id,
            )
        )

    @rule()
    def close(self) -> None:
        self.apply(OwnershipEvent(EventKind.CLOSE_REQUESTED))

    @invariant()
    def state_invariants_hold(self) -> None:
        assert_ownership_invariants(self.state)


TestOwnershipStateMachine = OwnershipStateMachine.TestCase
