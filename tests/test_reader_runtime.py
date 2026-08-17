import pytest
from util import FakeIPC, runtime_gateway

from saitenka.app.bindings import SCROLL_UP_MSG
from saitenka.app.reader_factory import ReaderServices, create_reader
from saitenka.app.runtime import (
    COMMAND_SPECS,
    CommandOutcome,
    CommandPolicy,
    CommandRejection,
    CommandSpec,
    CueCommandState,
    LegacyCommandBinding,
    LegacyCommandExecutor,
    LegacyPickerRepeatGuard,
    TickPipeline,
    TickStage,
)
from saitenka.runtime import CommandHandled, CommandReason, Owner, UserCommand


def test_legacy_command_executor_dispatches_accepted_feature_action():
    handled: list[str] = []
    policy = CommandPolicy((CommandSpec("mine", Owner.INTERACTION, requires_cue=True),))
    executor = LegacyCommandExecutor(
        {"mine": LegacyCommandBinding(lambda: handled.append("mined"), "work-package-5")},
        policy=policy,
    )

    result = executor.dispatch(
        UserCommand("mine"), cue_state=CueCommandState.ACTIVE, help_open=False
    )

    assert result.outcome == CommandOutcome.EXECUTED
    assert handled == ["mined"]


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


def test_legacy_command_failure_is_a_terminal_typed_outcome():
    def fail() -> None:
        raise RuntimeError("boom")

    spec = CommandSpec("fail", Owner.SESSION, requires_cue=False)
    executor = LegacyCommandExecutor(
        {"fail": LegacyCommandBinding(fail, "work-package-5")},
        policy=CommandPolicy((spec,)),
    )

    result = executor.dispatch(
        UserCommand("fail"), cue_state=CueCommandState.NEVER_INSTALLED, help_open=False
    )

    assert result.outcome == CommandOutcome.FAILED
    assert result.error_type == "RuntimeError"


def test_legacy_picker_repeat_guard_emits_bounded_suppression_outcome():
    guard = LegacyPickerRepeatGuard("picker")

    assert guard.inspect(UserCommand("picker")) is None
    suppressed = guard.inspect(UserCommand("picker"))

    assert suppressed is not None
    assert suppressed.event() == CommandHandled(
        "picker",
        Owner.INTERACTION,
        CommandOutcome.SUPPRESSED,
        reason=CommandReason.LEGACY_REPEAT,
    )


def test_reader_publishes_handler_failure_as_typed_runtime_outcome():
    def fail() -> None:
        raise RuntimeError

    ipc = FakeIPC()
    reader = create_reader(ipc)
    spec = CommandSpec("fail", Owner.SESSION, requires_cue=False)
    reader.commands = LegacyCommandExecutor(
        {"fail": LegacyCommandBinding(fail, "work-package-5")},
        policy=CommandPolicy((spec,)),
    )

    reader._handle(UserCommand("fail"))

    assert ipc.runtime_outcomes == [
        CommandHandled("fail", Owner.SESSION, CommandOutcome.FAILED, reason=CommandReason.INTERNAL)
    ]


def test_scroll_command_remains_eligible_while_help_is_open(monkeypatch):
    ipc = FakeIPC()
    reader = create_reader(ipc)
    reader._help_open = True
    calls: list[int] = []
    monkeypatch.setattr(
        "saitenka.app.surfaces.route_scroll", lambda _reader, steps: calls.append(steps)
    )

    reader._handle(UserCommand(SCROLL_UP_MSG))

    assert calls == [-1]
    assert ipc.runtime_outcomes == [
        CommandHandled(
            SCROLL_UP_MSG,
            Owner.INTERACTION,
            CommandOutcome.EXECUTED,
        )
    ]


def test_runtime_coalesces_scroll_once_and_finishes_every_admitted_command():
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = create_reader(ipc)
    handled: list[str] = []
    spec = CommandSpec(SCROLL_UP_MSG, Owner.INTERACTION, requires_cue=False)
    reader.commands = LegacyCommandExecutor(
        {SCROLL_UP_MSG: LegacyCommandBinding(lambda: handled.append("scroll"), "work-package-5")},
        policy=CommandPolicy((spec,)),
    )
    ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})
    ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})

    reader._drain_events()

    assert handled == ["scroll"]
    assert gateway.snapshot.command_outcomes == 2


def test_tick_pipeline_preserves_assembly_order():
    events: list[str] = []
    pipeline = TickPipeline(
        (
            TickStage("subtitles", lambda: events.append("subtitles")),
            TickStage("tooltip", lambda: events.append("tooltip")),
            TickStage("prefetch", lambda: events.append("prefetch")),
        )
    )

    pipeline.run()

    assert events == ["subtitles", "tooltip", "prefetch"]


def test_tick_pipeline_rejects_ambiguous_duplicate_phase():
    stages = (TickStage("tooltip", lambda: None), TickStage("tooltip", lambda: None))

    try:
        TickPipeline(stages)
    except ValueError as exc:
        assert str(exc) == "tick stage already registered: tooltip"
    else:  # pragma: no cover - the assertion above is the contract
        raise AssertionError("duplicate tick stage was accepted")


def test_composition_threads_grouped_optional_services():
    services = ReaderServices(
        scorer="score", anki="anki", mining="mine", dictionaries="dict", tts=True
    )

    reader = create_reader(FakeIPC(), services=services)

    assert (reader.scorer, reader.anki, reader.mine_cfg, reader.dict_set) == (
        "score",
        "anki",
        "mine",
        "dict",
    )
    assert reader._tts_ok is True
