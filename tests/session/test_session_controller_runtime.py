import pytest
from session_builder import build_session
from util import FakeIPC, bare_gateway

from saitenka.app.bindings import SCROLL_UP_MSG
from saitenka.app.config import ReaderOptions
from saitenka.app.runtime import (
    COMMAND_SPECS,
    CommandExecutor,
    CommandOutcome,
    CommandPolicy,
    CommandRejection,
    CommandSpec,
    CueCommandState,
)
from saitenka.app.session.factory import (
    SessionInfrastructure,
    SessionServices,
    TooltipWorkMode,
)
from saitenka.runtime import CommandHandled, CommandReason, Owner, UserCommand
from saitenka.runtime.help import HelpCommand


def test_the_executor_runs_the_action_bound_to_an_accepted_command():
    handled: list[str] = []
    policy = CommandPolicy((CommandSpec("mine", Owner.INTERACTION, requires_cue=True),))
    executor = CommandExecutor(
        {"mine": lambda: handled.append("mined")},
        policy=policy,
    )

    result = executor.dispatch(
        UserCommand("mine"), cue_state=CueCommandState.ACTIVE, help_open=False
    )

    assert result.outcome == CommandOutcome.EXECUTED
    assert handled == ["mined"]


def test_a_spec_with_no_action_dispatches_to_unbound_rather_than_rejecting():
    """The negative control for "every spec is routed" (tests/session/test_bindings_registry.py).

    An unrouted command is not a rejection: the policy accepted it, so it is documented as
    working, bound to a key, and does nothing. `UNBOUND` is what makes that visible in the
    outcome stream instead of reading as a press that never arrived.
    """
    policy = CommandPolicy((CommandSpec("mine", Owner.INTERACTION, requires_cue=False),))
    executor = CommandExecutor({}, policy=policy)

    result = executor.dispatch(
        UserCommand("mine"), cue_state=CueCommandState.ACTIVE, help_open=False
    )

    assert result.outcome == CommandOutcome.UNBOUND
    assert result.rejection is None


def test_command_policy_rejects_unknown_message_without_execution():
    policy = CommandPolicy()

    decision = policy.decide(
        UserCommand("unknown"), cue_state=CueCommandState.ACTIVE, help_open=False
    )

    assert decision.rejection == CommandRejection.UNKNOWN


def test_command_policy_rejects_duplicate_permanent_route():
    spec = COMMAND_SPECS[0]

    with pytest.raises(ValueError, match="command spec already registered"):
        CommandPolicy((spec, spec))


@pytest.mark.parametrize(
    ("cue_state", "expected"),
    [
        (CueCommandState.NEVER_INSTALLED, None),
        (CueCommandState.ACTIVE, None),
        (CueCommandState.RETIRED_AFTER_ACTIVE, CommandRejection.CUE_RETIRED),
    ],
)
def test_command_policy_preserves_three_state_cue_eligibility(cue_state, expected):
    decision = CommandPolicy().decide(
        UserCommand("saitenka-copy-line"), cue_state=cue_state, help_open=False
    )

    assert decision.rejection == expected


def test_command_policy_keeps_cue_independent_command_eligible_after_retirement():
    decision = CommandPolicy().decide(
        UserCommand("saitenka-toggle-overlay"),
        cue_state=CueCommandState.RETIRED_AFTER_ACTIVE,
        help_open=False,
    )

    assert decision.intent is not None


def test_command_policy_rejects_non_help_command_while_help_is_open():
    decision = CommandPolicy().decide(
        UserCommand("saitenka-toggle-overlay"),
        cue_state=CueCommandState.ACTIVE,
        help_open=True,
    )

    assert decision.rejection == CommandRejection.HELP_MODAL


def test_command_policy_accepts_help_navigation_while_help_is_open():
    decision = CommandPolicy().decide(
        UserCommand("saitenka-help-next"),
        cue_state=CueCommandState.RETIRED_AFTER_ACTIVE,
        help_open=True,
    )

    assert decision.intent is not None


def test_a_failing_action_is_a_terminal_typed_outcome():
    def fail() -> None:
        raise RuntimeError("boom")

    spec = CommandSpec("fail", Owner.SESSION, requires_cue=False)
    executor = CommandExecutor(
        {"fail": fail},
        policy=CommandPolicy((spec,)),
    )

    result = executor.dispatch(
        UserCommand("fail"), cue_state=CueCommandState.NEVER_INSTALLED, help_open=False
    )

    assert result.outcome == CommandOutcome.FAILED
    assert result.error_type == "RuntimeError"


def test_reader_publishes_handler_failure_as_typed_runtime_outcome(request, monkeypatch):
    def fail() -> None:
        raise RuntimeError

    ipc = FakeIPC()
    reader = build_session(ipc)
    request.addfinalizer(reader.close)  # owns threads; a leak here exhausts the pool at -n auto
    spec = CommandSpec("fail", Owner.SESSION, requires_cue=False)
    monkeypatch.setattr(
        reader.graph.commands,
        "_commands",
        CommandExecutor({"fail": fail}, policy=CommandPolicy((spec,))),
    )
    outcomes: list[CommandHandled] = []
    publish = ipc.publish_command_outcome

    def capture(outcome: CommandHandled) -> None:
        outcomes.append(outcome)
        publish(outcome)

    monkeypatch.setattr(ipc, "publish_command_outcome", capture)

    reader.command("fail")

    assert outcomes == [
        CommandHandled(
            "fail",
            Owner.SESSION,
            CommandOutcome.FAILED,
            command_id=0,
            reason=CommandReason.INTERNAL,
        )
    ]


def test_inline_tooltip_work_is_selected_at_session_construction(request):
    reader = build_session(
        FakeIPC(), infrastructure=SessionInfrastructure(tooltip_work=TooltipWorkMode.INLINE)
    )
    request.addfinalizer(reader.close)

    assert not reader.graph.tooltip.metadata_deferred


def test_scroll_command_remains_eligible_while_help_is_open(monkeypatch, request):
    ipc = FakeIPC()
    reader = build_session(ipc)
    request.addfinalizer(reader.close)  # owns threads; a leak here exhausts the pool at -n auto
    reader.graph.help.store.dispatch(HelpCommand.TOGGLE)
    calls: list[int] = []
    monkeypatch.setattr(
        reader.graph.interaction.router,
        "route_scroll",
        lambda _wheel, steps: calls.append(steps),
    )
    outcomes: list[CommandHandled] = []
    publish = ipc.publish_command_outcome

    def capture(outcome: CommandHandled) -> None:
        outcomes.append(outcome)
        publish(outcome)

    monkeypatch.setattr(ipc, "publish_command_outcome", capture)

    reader.command(SCROLL_UP_MSG)

    assert calls == [-1]
    assert outcomes == [
        CommandHandled(
            SCROLL_UP_MSG,
            Owner.INTERACTION,
            CommandOutcome.EXECUTED,
            command_id=0,
        )
    ]


def test_gateway_translates_adjacent_scroll_messages_into_one_command():
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    commands: list[object] = []

    try:
        ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})
        ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})

        ipc.receive_session(0.0, commands.append)

        assert commands == [UserCommand(SCROLL_UP_MSG, command_id=1, coalesced_ids=(0,))]
    finally:
        gateway.close()


def test_reader_finishes_every_command_folded_into_a_scroll(request, monkeypatch):
    ipc = FakeIPC()
    reader = build_session(ipc)
    request.addfinalizer(reader.close)
    outcomes: list[CommandHandled] = []
    publish = ipc.publish_command_outcome

    def capture(outcome: CommandHandled) -> None:
        outcomes.append(outcome)
        publish(outcome)

    monkeypatch.setattr(ipc, "publish_command_outcome", capture)
    ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})
    ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})
    reader.pump()

    assert outcomes == [
        CommandHandled(
            SCROLL_UP_MSG,
            Owner.INTERACTION,
            CommandOutcome.EXECUTED,
            command_id=1,
        ),
        CommandHandled(
            SCROLL_UP_MSG,
            Owner.INTERACTION,
            CommandOutcome.SUPPRESSED,
            command_id=0,
            reason=CommandReason.COALESCED,
        ),
    ]


def test_composition_threads_grouped_optional_services(request):
    from types import SimpleNamespace

    from saitenka.app.anki import MineConfig

    scorer = SimpleNamespace(score_line=lambda _tokens: [])
    anki = object()
    mining = MineConfig()
    services = SessionServices(
        scorer=scorer, anki=anki, mining=mining, dictionaries="dict", tts=True
    )

    reader = build_session(FakeIPC(), services=services)

    request.addfinalizer(reader.close)  # owns threads; a leak here exhausts the pool at -n auto

    target = reader.graph.mining.active_target
    assert target is not None
    assert (
        reader.graph.profile.scorer,
        target.anki,
        target.config,
        reader.graph.profile.profile.dict_set,
    ) == (
        scorer,
        anki,
        mining,
        "dict",
    )
    assert reader.graph.tooltip.panel_style.speak_button is True


def test_composition_injects_the_geometry_provider_the_reader_no_longer_picks() -> None:
    """The SessionController used to construct `LibassGeometryBackend` itself when none was passed, so it was
    not injectable in the case that mattered — the shipping one. A host that picks its own provider
    cannot be handed a different one, which is what makes the conformance contract testable.
    """
    from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
    from saitenka.app.session.factory import _geometry_backend

    assert _geometry_backend(SubtitleGeometryOptions(native_visible=False)) is None
    chosen = _geometry_backend(SubtitleGeometryOptions(native_visible=True))
    assert chosen is not None  # composition makes the choice
    chosen.close()

    options = ReaderOptions(
        subtitle_geometry=SubtitleGeometryOptions(native_visible=True), prefetch=False
    )
    direct = build_session(FakeIPC(), options=options)

    # The factory-selected provider receives the request. This deliberately incomplete probe is
    # rejected by the provider; a missing provider would return without recording an error.
    assert direct.graph.subtitle_presentation.pipeline.render(_probe_request(direct)) is None
    assert direct.graph.subtitle_presentation.pipeline.last_error is not None
    direct.close()


def _probe_request(reader):
    from saitenka.subtitles import (
        GeometryRequest,
        SubtitleEventId,
        SubtitleFrameId,
        SubtitleTrackId,
    )

    track_id = SubtitleTrackId("probe")
    event_id = SubtitleEventId(track_id, 0, 1_000, 0, 0)
    return GeometryRequest(
        generation=reader.graph.subtitle_presentation.pipeline.generation,
        track_id=track_id,
        frame_id=SubtitleFrameId(track_id, (event_id,)),
        timestamp_ms=0,
        frame_size=(1920, 1080),
        storage_size=(1920, 1080),
        ass=b"[Script Info]\n",
    )


def test_an_idle_session_blocks_instead_of_polling():
    """An idle turn waits for input instead of polling domain state."""
    import time

    from util import FakeIPC

    from saitenka.app.subtitle_render import NullRenderer

    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    try:
        started = time.monotonic()
        assert reader.pump(0.05) is True
        assert time.monotonic() - started >= 0.04  # it waited; it did not return immediately
    finally:
        reader.close()


def test_an_event_wakes_the_wait_early():
    """The negative control for the test above — a blocking wait that never wakes is a hang.

    Without this, `pump` could satisfy "it blocked" by simply always sleeping the full timeout,
    which would make every interaction feel like a 50 ms stutter.
    """
    import threading
    import time

    from util import FakeIPC

    from saitenka.app.subtitle_render import NullRenderer

    ipc = FakeIPC()
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    try:
        threading.Timer(
            0.02, lambda: ipc.emit({"event": "property-change", "name": "pause", "data": True})
        ).start()
        started = time.monotonic()
        assert reader.pump(2.0) is True
        assert time.monotonic() - started < 1.0  # woken by the event, not by the timeout
    finally:
        reader.close()
