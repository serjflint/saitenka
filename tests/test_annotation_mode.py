"""Learning-annotation visibility remains independent of tooltip and playback state."""

import pytest
import util
from util import RecordingRasterProvider, keybind_registry

from saitenka.app.bindings import ANNOTATION_MSG
from saitenka.app.config import KeyOptions, ReaderOptions, TooltipOptions
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
from saitenka.app.tooltip import update_hover_impl


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
    reader = Reader(FakeIPC())

    assert reader.annotation_mode == "full"
    assert reader.keys.annotation_key == "Alt+a"


def test_hover_mode_retains_scores_but_hides_them_from_render(monkeypatch):
    reader = Reader(
        FakeIPC(), options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover"))
    )
    reader.sub_text = "猫"
    reader.lines = [[object()]]
    reader.tokens = [object()]
    reader.styles = ["scored"]
    reader.hover = 0
    monkeypatch.setattr(reader.ov, "show", lambda *_args, **_kwargs: None)
    provider = RecordingRasterProvider(size=(10, 10))
    reader.renderer = SubtitleRenderer(provider)

    reader._draw_subtitle()
    reader.set_annotation_hover(revealed=True)
    reader.set_annotation_hover(revealed=False)

    assert reader.styles == ["scored"]
    assert [(request.styles, request.hover) for request in provider.requests] == [
        (None, None),
        (["scored"], 0),
        (None, None),
    ]


def test_hover_mode_still_scores_each_new_cue(monkeypatch):
    class Scorer:
        def score_line(self, tokens):
            return [f"score:{token.surface}" for token in tokens]

    reader = Reader(
        FakeIPC(),
        scorer=Scorer(),
        options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover")),
    )
    monkeypatch.setattr(reader, "renderer", NullRenderer())

    reader.set_subtitle("猫")

    assert reader.styles == ["score:猫"]


def test_entering_word_reveals_before_tooltip_switch_dwell(monkeypatch):
    ipc = FakeIPC({"mouse-pos": {"hover": True, "x": 50, "y": 50}})
    reader = Reader(ipc, hover_switch_delay=10.0)
    reader.tokens = [object(), object()]
    reader.hover = 0
    calls = []
    monkeypatch.setattr(reader, "_hit", lambda *_args: 1)
    monkeypatch.setattr(
        reader,
        "set_annotation_hover",
        lambda *, revealed: calls.append(("style", revealed)),
    )
    update_hover_impl(reader)

    assert calls == [("style", True)]
    # The switch is a decision the dwell has not made yet: the target is armed, the tooltip has not
    # moved. No stub stands in for the build — nothing calls it.
    assert reader._word_target == 1
    assert reader.hover == 0


def test_hover_presentation_transition_does_not_open_tooltip_or_pause(monkeypatch):
    ipc = FakeIPC()
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover", pause_on_tooltip=False))
    reader = Reader(ipc, options=options)
    reader.tokens = [object()]
    redrawn = []
    monkeypatch.setattr(reader, "renderer", _SpyRenderer(lambda _rd: redrawn.append(True)))

    reader.set_annotation_hover(revealed=True)

    assert reader.hover == -1
    assert redrawn == [True]
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_leaving_subtitle_restores_neutral_presentation(monkeypatch):
    ipc = FakeIPC({"mouse-pos": {"hover": False, "x": 50, "y": 50}})
    reader = Reader(ipc, options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover")))
    reader.tokens = [object()]
    reader._annotation_hover = True
    states = []
    monkeypatch.setattr(
        reader, "renderer", _SpyRenderer(lambda rq: states.append(rq.annotation_visible))
    )

    update_hover_impl(reader)

    assert states == [False]


def test_cue_change_resets_hover_only_presentation(monkeypatch):
    reader = Reader(
        FakeIPC(), options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover"))
    )
    reader._annotation_hover = True
    states = []
    monkeypatch.setattr(
        reader, "renderer", _SpyRenderer(lambda rq: states.append(rq.annotation_visible))
    )

    reader.set_subtitle("猫")

    assert states == [False]


def test_toggle_changes_presentation_without_playback_commands(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader.sub_text = "猫"
    drawn = []
    toasts = []
    monkeypatch.setattr(
        reader, "renderer", _SpyRenderer(lambda rq: drawn.append(rq.annotation_visible))
    )
    monkeypatch.setattr(reader, "_toast", lambda text, *_a, **_k: toasts.append(text))

    reader.toggle_annotation_mode()

    assert (reader.annotation_mode, drawn, toasts) == (
        "hover",
        [False],  # hover-only with the cursor away -> annotations not visible in the request
        ["annotations: hover-only"],
    )
    assert not any(command[0] in {"set_property", "seek", "sub-seek"} for command in ipc.commands)


def test_toggle_remains_available_while_cue_identity_is_retired():
    reader = Reader(FakeIPC(), renderer=NullRenderer())
    reader._cue_identity_ever_installed = True
    reader._cue_retired = True

    reader._handle(ANNOTATION_MSG)

    assert reader.annotation_mode == "hover"


def test_annotation_key_is_configurable():
    ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(annotation_key="Ctrl+a"))

    Reader(ipc, options=options)._register_keybinds()

    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}
    assert binds["Ctrl+a"] == "script-message saitenka-toggle-annotations"


def test_unknown_initial_annotation_mode_is_rejected():
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="invalid"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unknown annotation mode"):
        Reader(FakeIPC(), options=options)
