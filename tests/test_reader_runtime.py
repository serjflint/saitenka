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


def test_reader_publishes_handler_failure_as_typed_runtime_outcome(request):
    def fail() -> None:
        raise RuntimeError

    ipc = FakeIPC()
    reader = create_reader(ipc)
    request.addfinalizer(reader.close)  # owns threads; a leak here exhausts the pool at -n auto
    spec = CommandSpec("fail", Owner.SESSION, requires_cue=False)
    reader.commands = LegacyCommandExecutor(
        {"fail": LegacyCommandBinding(fail, "work-package-5")},
        policy=CommandPolicy((spec,)),
    )

    reader._handle(UserCommand("fail"))

    assert ipc.runtime_outcomes == [
        CommandHandled("fail", Owner.SESSION, CommandOutcome.FAILED, reason=CommandReason.INTERNAL)
    ]


def test_scroll_command_remains_eligible_while_help_is_open(monkeypatch, request):
    ipc = FakeIPC()
    reader = create_reader(ipc)
    request.addfinalizer(reader.close)  # owns threads; a leak here exhausts the pool at -n auto
    reader.help.open = True
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
    try:
        ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})
        ipc.emit({"event": "client-message", "args": [SCROLL_UP_MSG]})

        reader._drain_events()

        assert handled == ["scroll"]
        assert gateway.snapshot.command_outcomes == 2
    finally:
        # Both own threads. Leaking a Reader and a gateway per run is survivable alone and is not
        # survivable at `-n auto`, where the accumulated lanes exhaust the thread pool and this test
        # fails somewhere unrelated to what it asserts.
        reader.close()
        gateway.close()


def test_composition_threads_grouped_optional_services(request):
    services = ReaderServices(
        scorer="score", anki="anki", mining="mine", dictionaries="dict", tts=True
    )

    reader = create_reader(FakeIPC(), services=services)

    request.addfinalizer(reader.close)  # owns threads; a leak here exhausts the pool at -n auto

    assert (reader.scorer, reader.anki, reader.mine_cfg, reader.dict_set) == (
        "score",
        "anki",
        "mine",
        "dict",
    )
    assert reader._tts_ok is True


def test_composition_injects_the_geometry_provider_the_reader_no_longer_picks() -> None:
    """The Reader used to construct `LibassGeometryBackend` itself when none was passed, so it was
    not injectable in the case that mattered — the shipping one. A host that picks its own provider
    cannot be handed a different one, which is what makes the conformance contract testable.
    """
    from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
    from saitenka.app.controller import Reader
    from saitenka.app.reader_factory import _geometry_backend

    assert _geometry_backend(SubtitleGeometryOptions(native_visible=False)) is None
    chosen = _geometry_backend(SubtitleGeometryOptions(native_visible=True))
    assert chosen is not None  # composition makes the choice
    chosen.close()

    options = ReaderOptions(
        subtitle_geometry=SubtitleGeometryOptions(native_visible=True), prefetch=False
    )
    direct = Reader(FakeIPC(), options=options)

    # …and the Reader does not. A render attempt in native mode produces neither a result nor an
    # error, which is the signature of "no provider at all" — a self-selected one would leave one
    # or the other behind.
    assert direct.subtitle_pipeline.render(_probe_request(direct)) is None
    assert direct.subtitle_pipeline.current is None
    assert direct.subtitle_pipeline.last_error is None
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
        generation=reader.subtitle_pipeline.generation,
        track_id=track_id,
        frame_id=SubtitleFrameId(track_id, (event_id,)),
        timestamp_ms=0,
        frame_size=(1920, 1080),
        storage_size=(1920, 1080),
        ass=b"[Script Info]\n",
    )


def test_an_idle_session_blocks_instead_of_polling():
    """WP6's whole point: with nothing happening, a turn costs a wait — not a spin.

    The old loop woke `1/poll_interval` times a second to ask whether anything had happened. This
    asserts the shape that replaced it: `pump` with a timeout returns only once the wait elapses,
    so an idle runtime does no domain work at all.
    """
    import time

    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_render import NullRenderer

    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())
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

    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_render import NullRenderer

    ipc = FakeIPC()
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    try:
        threading.Timer(
            0.02, lambda: ipc.emit({"event": "property-change", "name": "pause", "data": True})
        ).start()
        started = time.monotonic()
        assert reader.pump(2.0) is True
        assert time.monotonic() - started < 1.0  # woken by the event, not by the timeout
    finally:
        reader.close()
