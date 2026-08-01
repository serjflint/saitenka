"""Learning-annotation visibility remains independent of tooltip and playback state."""

import pytest
from PIL import Image

from overlay.app import controller
from overlay.app.config import KeyOptions, ReaderOptions, TooltipOptions
from overlay.app.controller import Reader
from overlay.app.subtitles import SubtitleRender
from overlay.app.tooltip import update_hover_impl


class FakeIPC:
    def __init__(self, props=None):
        self.props = props or {}
        self.commands = []

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}


def test_full_annotations_remain_the_default():
    reader = Reader(FakeIPC())

    assert reader.annotation_mode == "full"
    assert reader.annotation_key == "Alt+a"


def test_hover_mode_retains_scores_but_hides_them_from_render(monkeypatch):
    reader = Reader(
        FakeIPC(), options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover"))
    )
    reader.sub_text = "猫"
    reader.lines = [[object()]]
    reader.tokens = [object()]
    reader.styles = ["scored"]
    reader.hover = 0
    rendered = []
    monkeypatch.setattr(reader.ov, "show", lambda *_args, **_kwargs: None)

    def render(*_args, **kwargs):
        rendered.append(kwargs)
        return SubtitleRender(Image.new("RGBA", (10, 10)), [])

    monkeypatch.setattr(controller, "render_subtitle", render)

    reader._draw_subtitle()
    reader.set_annotation_hover(revealed=True)
    reader.set_annotation_hover(revealed=False)

    assert reader.styles == ["scored"]
    assert [(call["styles"], call["hover"]) for call in rendered] == [
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
    monkeypatch.setattr(reader, "_draw_subtitle", lambda: None)

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
    monkeypatch.setattr(reader, "set_hover", lambda index: calls.append(("tooltip", index)))

    update_hover_impl(reader)

    assert calls == [("style", True)]
    assert reader._word_target == 1


def test_hover_presentation_transition_does_not_open_tooltip_or_pause(monkeypatch):
    ipc = FakeIPC()
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover", pause_on_tooltip=False))
    reader = Reader(ipc, options=options)
    reader.tokens = [object()]
    redrawn = []
    monkeypatch.setattr(reader, "_draw_subtitle", lambda: redrawn.append(True))

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
    monkeypatch.setattr(reader, "_draw_subtitle", lambda: states.append(reader._annotation_hover))

    update_hover_impl(reader)

    assert states == [False]


def test_cue_change_resets_hover_only_presentation(monkeypatch):
    reader = Reader(
        FakeIPC(), options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover"))
    )
    reader._annotation_hover = True
    states = []
    monkeypatch.setattr(reader, "_draw_subtitle", lambda: states.append(reader._annotation_hover))

    reader.set_subtitle("猫")

    assert states == [False]


def test_toggle_changes_presentation_without_playback_commands(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader.sub_text = "猫"
    drawn = []
    toasts = []
    monkeypatch.setattr(reader, "_draw_subtitle", lambda: drawn.append(reader.annotation_mode))
    monkeypatch.setattr(reader, "_toast", lambda text: toasts.append(text))

    reader.toggle_annotation_mode()

    assert (reader.annotation_mode, drawn, toasts) == (
        "hover",
        ["hover"],
        ["annotations: hover-only"],
    )
    assert not any(command[0] in {"set_property", "seek", "sub-seek"} for command in ipc.commands)


def test_annotation_key_is_configurable():
    ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(annotation_key="Ctrl+a"))

    Reader(ipc, options=options)._register_keybinds()

    binds = {command[1]: command[2] for command in ipc.commands if command[0] == "keybind"}
    assert binds["Ctrl+a"] == "script-message saitenka-toggle-annotations"


def test_unknown_initial_annotation_mode_is_rejected():
    options = ReaderOptions(tooltip=TooltipOptions(annotation_mode="invalid"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unknown annotation mode"):
        Reader(FakeIPC(), options=options)
