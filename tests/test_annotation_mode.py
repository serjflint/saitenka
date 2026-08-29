"""Learning-annotation visibility remains independent of tooltip and playback state."""

import pytest
import util
from session_builder import build_session
from util import RecordingRasterProvider, keybind_registry

from saitenka.app import bindings as app_bindings
from saitenka.app.bindings import ANNOTATION_MSG
from saitenka.app.config import KeyOptions, ReaderOptions, TooltipOptions
from saitenka.app.features.tooltip.tooltip import update_hover_impl
from saitenka.app.session.factory import (
    SessionInfrastructure,
    SessionServices,
)
from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer


class FakeIPC(util.FakeIPC):
    def __init__(self, props=None):
        super().__init__()
        self.props.update(props or {})


class _SpyRenderer(NullRenderer):
    """A draw strategy that records each draw request instead of rasterizing.

    The callback receives the `DrawRequest`, which is what the renderer actually sees — asserting
    against host attributes would be reaching back across the seam the request exists to close.
    Inherits the inert renderer so it answers the whole protocol; only `draw` is interesting here.
    """

    def __init__(self, on_draw):
        self._on_draw = on_draw

    def draw(self, request, _surfaces=None, _ipc=None, /, **_ports):
        self._on_draw(request)


def test_full_annotations_remain_the_default():
    reader = build_session(FakeIPC())

    assert reader.turn.annotation_controller.view.mode == "full"
    assert ReaderOptions().keys.annotation_key == "Alt+a"


def test_hover_mode_retains_scores_but_hides_them_from_render(monkeypatch):
    reader = build_session(
        FakeIPC(), options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover"))
    )
    reader.turn.playback_observation.install_seed({"sub-text": "猫"})
    reader.turn.subtitle_presentation.cue.replace_tokenized(lines=[[object()]])
    reader.turn.subtitle_presentation.cue.replace_tokenized(tokens=[object()])
    reader.turn.subtitle_presentation.cue.replace_tokenized(styles=["scored"])
    reader.turn.tooltip_controller.select(0)
    monkeypatch.setattr(reader.turn.ov, "show", lambda *_args, **_kwargs: None)
    provider = RecordingRasterProvider(size=(10, 10))
    reader.turn.subtitle_presentation.renderer = SubtitleRenderer(provider)

    reader.turn.subtitle_presentation.draw()
    reader.turn.tooltip_controller.set_annotation_hover(revealed=True)
    reader.turn.tooltip_controller.set_annotation_hover(revealed=False)

    assert reader.turn.subtitle_presentation.cue.current.styles == ["scored"]
    assert [(request.styles, request.hover) for request in provider.requests] == [
        (None, None),
        (["scored"], 0),
        (None, None),
    ]


def test_hover_mode_still_scores_each_new_cue(monkeypatch):
    class Scorer:
        def score_line(self, tokens):
            return [f"score:{token.surface}" for token in tokens]

    reader = build_session(
        FakeIPC(),
        services=SessionServices(
            scorer=Scorer(),
        ),
        options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover")),
    )
    monkeypatch.setattr(reader.turn.subtitle_presentation, "renderer", NullRenderer())

    reader.turn.cue_coordinator.set_subtitle("猫")

    assert reader.turn.subtitle_presentation.cue.current.styles == ["score:猫"]


def test_entering_word_reveals_before_tooltip_switch_dwell(monkeypatch):
    ipc = FakeIPC({"mouse-pos": {"hover": True, "x": 50, "y": 50}})
    reader = build_session(ipc, options=ReaderOptions().with_overrides(hover_switch_delay=10.0))
    reader.turn.subtitle_presentation.cue.replace_tokenized(tokens=[object(), object()])
    reader.turn.tooltip_controller.select(0)
    calls = []
    monkeypatch.setattr(reader.turn.tooltip_controller, "hit", lambda *_args: 1)
    monkeypatch.setattr(
        reader.turn.tooltip_controller,
        "set_annotation_hover",
        lambda *, revealed: calls.append(("style", revealed)),
    )
    update_hover_impl(
        reader.turn.tooltip_controller.tip_ports,
        reader.turn.tooltip_controller.hover_actions,
        reader.turn.tooltip_controller.hover_inputs,
    )

    assert calls == [("style", True)]
    # The switch is a decision the dwell has not made yet: the target is armed, the tooltip has not
    # moved. No stub stands in for the build — nothing calls it.
    assert reader.turn.tooltip_controller.hover_diagnostics().word_target == 1
    assert reader.turn.tooltip_controller.observation().selected == 0


def test_hover_presentation_transition_does_not_open_tooltip_or_pause(monkeypatch):
    ipc = FakeIPC()
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover", pause_on_tooltip=False))
    reader = build_session(ipc, options=options)
    reader.turn.subtitle_presentation.cue.replace_tokenized(tokens=[object()])
    redrawn = []
    monkeypatch.setattr(
        reader.turn.subtitle_presentation,
        "renderer",
        _SpyRenderer(lambda _rd: redrawn.append(True)),
    )

    reader.turn.tooltip_controller.set_annotation_hover(revealed=True)

    assert reader.turn.tooltip_controller.observation().selected == -1
    assert redrawn == [True]
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_leaving_subtitle_restores_neutral_presentation(monkeypatch):
    ipc = FakeIPC({"mouse-pos": {"hover": False, "x": 50, "y": 50}})
    reader = build_session(
        ipc, options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover"))
    )
    reader.turn.subtitle_presentation.cue.replace_tokenized(tokens=[object()])
    reader.turn.annotation_controller.set_hover_revealed(revealed=True)
    states = []
    monkeypatch.setattr(
        reader.turn.subtitle_presentation,
        "renderer",
        _SpyRenderer(lambda rq: states.append(rq.annotation_visible)),
    )

    update_hover_impl(
        reader.turn.tooltip_controller.tip_ports,
        reader.turn.tooltip_controller.hover_actions,
        reader.turn.tooltip_controller.hover_inputs,
    )

    assert states == [False]


def test_cue_change_resets_hover_only_presentation(monkeypatch):
    reader = build_session(
        FakeIPC(), options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover"))
    )
    reader.turn.annotation_controller.set_hover_revealed(revealed=True)
    states = []
    monkeypatch.setattr(
        reader.turn.subtitle_presentation,
        "renderer",
        _SpyRenderer(lambda rq: states.append(rq.annotation_visible)),
    )

    reader.turn.cue_coordinator.set_subtitle("猫")

    assert states == [False]


def test_toggle_changes_presentation_without_playback_commands(monkeypatch):
    ipc = FakeIPC()
    reader = build_session(ipc)
    reader.turn.playback_observation.install_seed({"sub-text": "猫"})
    drawn = []
    toasts = []
    monkeypatch.setattr(
        reader.turn.subtitle_presentation,
        "renderer",
        _SpyRenderer(lambda rq: drawn.append(rq.annotation_visible)),
    )
    monkeypatch.setattr(
        reader.turn.notifications, "show", lambda text, *_a, **_k: toasts.append(text)
    )

    reader.turn.command_runtime.handle(app_bindings.ANNOTATION_MSG)

    assert (reader.turn.annotation_controller.view.mode, drawn, toasts) == (
        "hover",
        [False],  # hover-only with the cursor away -> annotations not visible in the request
        ["annotations: hover-only"],
    )
    assert not any(command[0] in {"set_property", "seek", "sub-seek"} for command in ipc.commands)


def test_toggle_remains_available_while_cue_identity_is_retired():
    reader = build_session(FakeIPC(), infrastructure=SessionInfrastructure(renderer=NullRenderer()))
    reader.turn.cue_coordinator.mark_identity_installed()

    reader.turn.command_runtime.handle(ANNOTATION_MSG)

    assert reader.turn.annotation_controller.view.mode == "hover"


def test_annotation_key_is_configurable():
    ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(annotation_key="Ctrl+a"))

    build_session(ipc, options=options).turn.command_runtime.install_input()

    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}
    assert binds["Ctrl+a"] == "script-message saitenka-toggle-annotations"


def test_unknown_initial_annotation_mode_is_rejected():
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="invalid"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unknown annotation mode"):
        build_session(FakeIPC(), options=options)
