from util import FakeIPC

from saitenka.app.reader_factory import ReaderServices, create_reader
from saitenka.app.runtime import CommandRouter, TickPipeline, TickStage


def test_command_router_dispatches_bound_feature_action():
    handled: list[str] = []
    router = CommandRouter({"mine": lambda: handled.append("mined")})

    assert router.dispatch("mine") is True
    assert handled == ["mined"]


def test_command_router_leaves_unknown_message_unclaimed():
    router = CommandRouter()

    assert router.dispatch("unknown") is False


def test_command_router_rejects_duplicate_feature_claim():
    router = CommandRouter({"mine": lambda: None})

    try:
        router.register("mine", lambda: None)
    except ValueError as exc:
        assert str(exc) == "script message already registered: mine"
    else:  # pragma: no cover - the assertion above is the contract
        raise AssertionError("duplicate command registration was accepted")


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
    services = ReaderServices(scorer="score", anki="anki", mining="mine", dictionaries="dict")

    reader = create_reader(FakeIPC(), services=services)

    assert (reader.scorer, reader.anki, reader.mine_cfg, reader.dict_set) == (
        "score",
        "anki",
        "mine",
        "dict",
    )
