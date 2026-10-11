"""Learning-annotation visibility remains independent of tooltip and playback state."""

import dicthelp
import pytest
from driver import Driver
from saitenka_wordstate import Scorer
from saitenka_wordstate.known import KnownWords
from util import FakeIPC, RecordingRasterProvider, press

from saitenka.app.config import KeyOptions, ReaderOptions, TooltipOptions
from saitenka.app.scoring import Coloring
from saitenka.app.session.factory import SessionInfrastructure, SessionServices
from saitenka.app.subtitle_raster import PillowRasterProvider
from saitenka.app.subtitle_render import SubtitleRenderer

pytestmark = [pytest.mark.integration, pytest.mark.timeout(5)]


def _reader(make_session, tmp_path, *, options=None):
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    words = dicthelp.term_zip(
        tmp_path / "words.zip", "Words", [["猫", "ねこ", ["cat"]], ["犬", "いぬ", ["dog"]]]
    )
    provider = RecordingRasterProvider(delegate=PillowRasterProvider())
    scorer = Coloring(
        Scorer(known=KnownWords.from_set(["猫"]), enable_freq=False, enable_jlpt=False)
    )
    reader = make_session(
        ipc,
        services=SessionServices(dictionaries=dicthelp.load_set([words]), scorer=scorer),
        infrastructure=SessionInfrastructure(renderer=SubtitleRenderer(provider)),
        options=options
        or ReaderOptions(
            tooltip=TooltipOptions(
                annotation_mode="hover", pause_on_tooltip=False, hover_switch_delay=10.0
            )
        ),
    )
    reader.graph.presentation.refresh_osd()
    reader.graph.commands.install_input()
    reader.graph.playback.observe("sub-text", "猫 犬")
    reader.pump()
    return reader, provider


def test_entering_word_reveals_before_tooltip_switch_dwell(make_session, tmp_path):
    reader, provider = _reader(make_session, tmp_path)
    ui = Driver(reader, instant=False)
    ui.move_to_word(0)
    assert ui.hover == 0 and ui.tip_shown
    reader.graph.tooltip.set_annotation_hover(revealed=False)
    assert provider.requests[-1].styles is None

    dog = next(
        i
        for i, token in enumerate(reader.graph.subtitle_presentation.cue.current.tokens)
        if token.surface == "犬"
    )
    ui.move_to_word(dog)

    assert provider.requests[-1].styles is not None
    assert ui.hover == 0
    assert ui.ipc.fire_runtime_timer("lifecycle:hover-switch")
    reader.pump()
    assert ui.hover == dog


def test_hover_presentation_transition_does_not_open_tooltip_or_pause(make_session, tmp_path):
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover", pause_on_tooltip=True))
    reader, provider = _reader(make_session, tmp_path, options=options)
    ui = Driver(reader, instant=False)
    ui.ipc.commands.clear()

    reader.graph.tooltip.set_annotation_hover(revealed=True)

    assert provider.requests[-1].styles is not None
    assert ui.hover == -1 and not ui.tip_shown
    assert not any(command[:2] == ("set_property", "pause") for command in ui.ipc.commands)


@pytest.mark.parametrize("reset", ["leave", "cue-change"])
def test_leaving_subtitle_restores_neutral_presentation(make_session, tmp_path, reset):
    reader, provider = _reader(make_session, tmp_path)
    ui = Driver(reader).move_to_word(0)
    assert provider.requests[-1].styles is not None

    if reset == "leave":
        ui.leave()
    else:
        reader.graph.playback.observe("sub-text", "犬")
        reader.pump()

    assert provider.requests[-1].styles is None


@pytest.mark.parametrize(
    ("key", "show_cue"),
    [(None, True), ("Ctrl+a", True), (None, False)],
    ids=["default", "custom", "retired-cue"],
)
def test_toggle_changes_presentation_without_playback_commands(
    monkeypatch, make_session, tmp_path, key, show_cue
):
    options = ReaderOptions(keys=KeyOptions(annotation_key=key)) if key else ReaderOptions()
    reader, provider = _reader(make_session, tmp_path, options=options)
    if not show_cue:
        reader.graph.playback.observe("sub-text", "")
        reader.pump()
    ipc = reader.graph.ipc
    if show_cue:
        assert provider.requests[-1].styles is not None
    toasts = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, *_a, **_k: toasts.append(text)
    )
    before = len(ipc.commands)

    press(reader, ipc, key or "Alt+a")

    assert (reader.graph.annotation.view.mode, toasts) == ("hover", ["annotations: hover-only"])
    if show_cue:
        assert provider.requests[-1].styles is None
    assert not any(
        command[0] in {"seek", "sub-seek"}
        or (
            command[0] == "set_property"
            and command[1] not in {"sub-visibility", "secondary-sub-visibility"}
        )
        for command in ipc.commands[before:]
    ), ipc.commands[before:]


def test_unknown_initial_annotation_mode_is_rejected(make_session):
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="invalid"))  # type: ignore[arg-type]
    ipc = FakeIPC()
    try:
        with pytest.raises(ValueError, match="unknown annotation mode"):
            make_session(ipc, options=options)
    finally:
        ipc.close_session_runtime()
