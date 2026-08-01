"""In-player help uses effective bindings and remains playback-neutral."""

from overlay.app import help_overlay, tooltip
from overlay.app.bindings import (
    BOOKMARK_MSG,
    HELP_CLOSE_MSG,
    HELP_NEXT_MSG,
    HELP_TOGGLE_MSG,
    MINE_MSG,
    SUB_NEXT_MSG,
    active_bindings,
)
from overlay.app.config import KeyOptions, ReaderOptions
from overlay.app.controller import Reader
from overlay.app.overlay_ids import OverlayId
from overlay.render.help import render_page


class FakeIPC:
    def __init__(self):
        self.commands: list[tuple] = []
        self.props: dict[str, object] = {}

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}


def _entries(reader: Reader):
    return [
        (section.title, entry)
        for page in help_overlay.document_for(reader).pages
        for section in page.sections
        for entry in section.entries
    ]


def test_default_and_configured_help_keys_are_registered():
    default_ipc = FakeIPC()
    Reader(default_ipc)._register_keybinds()
    default_binds = {command[1]: command[2] for command in default_ipc.commands}
    assert default_binds["F1"] == "script-message saitenka-toggle-help"

    custom_ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(help_key="Ctrl+h"))
    Reader(custom_ipc, options=options)._register_keybinds()
    custom_binds = {command[1]: command[2] for command in custom_ipc.commands}
    assert custom_binds["Ctrl+h"] == "script-message saitenka-toggle-help"
    assert "F1" not in custom_binds


def test_help_document_uses_effective_catalog_and_context_labels():
    options = ReaderOptions(
        keys=KeyOptions(
            help_key="Ctrl+h",
            translate_key="e",
            analysis_key="Ctrl+d",
            sub_next_key="Ctrl+RIGHT",
            mine_key="Ctrl+x",
        )
    )
    reader = Reader(FakeIPC(), anki=object(), options=options)
    entries = _entries(reader)
    by_label = {entry.label: entry for _section, entry in entries}
    sections = {section for section, _entry in entries}

    assert by_label["Shortcut reference"].key == "Ctrl+h"
    assert by_label["Show English translation"].key == "e"
    assert by_label["Next subtitle"].key == "Ctrl+RIGHT"
    assert by_label["Episode analysis"].key == "Ctrl+d"
    assert by_label["Mine hovered word"].key == "Ctrl+x"
    assert by_label["Close tooltip"].context == "tooltip only"
    assert by_label["Pause / resume (SyncPlay)"].source == "mpv"
    assert by_label["Cycle primary subtitles"].key == "j / Shift+J"
    assert by_label["Toggle native primary subtitles"].key == "v"
    assert by_label["Toggle native secondary subtitles"].key == "Alt+v"
    assert {
        "Essentials & language",
        "Subtitle navigation",
        "Capture & mining",
        "Tooltip actions",
        "Useful mpv controls",
    } <= sections

    expected = {
        (binding.key, binding.spec.message) for binding in active_bindings(reader, "global")
    }
    reader._register_keybinds()
    actual = {
        (command[1], command[2].removeprefix("script-message "))
        for command in reader.ipc.commands
        if command[0] == "keybind"
    }
    assert actual == expected


def test_small_osd_pages_and_repeats_navigation_hints():
    reader = Reader(FakeIPC(), anki=object())
    reader.osd = (480, 220)
    document = help_overlay.document_for(reader)

    assert len(document.pages) > 1
    assert all(page.footer == "F1 / Esc close  ·  PgUp/PgDn or wheel" for page in document.pages)
    for index, page in enumerate(document.pages):
        image = render_page(
            page,
            width=document.width,
            height=document.height,
            index=index,
            total=len(document.pages),
        )
        assert image.size == (document.width, document.height)
        assert image.width <= reader.osd[0]
        assert image.height <= reader.osd[1]


def test_toggle_navigation_and_escape_are_playback_neutral():
    ipc = FakeIPC()
    reader = Reader(ipc, anki=object())
    reader.osd = (480, 220)

    reader._handle(HELP_TOGGLE_MSG)
    assert any(command[:2] == ("keybind", "ESC") for command in ipc.commands)
    assert any(command[:2] == ("overlay-add", OverlayId.HELP) for command in ipc.commands)

    adds_before = sum(command[0] == "overlay-add" for command in ipc.commands)
    reader._handle(HELP_NEXT_MSG)
    assert sum(command[0] == "overlay-add" for command in ipc.commands) == adds_before + 1
    reader._handle(HELP_CLOSE_MSG)
    assert any(command[:2] == ("overlay-remove", OverlayId.HELP) for command in ipc.commands)

    forbidden = {"add", "sub-add", "sub-seek", "loadfile", "seek", "set_property"}
    assert not [command for command in ipc.commands if command[0] in forbidden]


def test_help_suppresses_actions_and_hover_then_restores_hover(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc, anki=object())
    hover_updates: list[str] = []
    actions: list[str] = []
    monkeypatch.setattr(tooltip, "update_hover", lambda _reader: hover_updates.append("hover"))
    monkeypatch.setattr(reader, "mine_current", lambda: actions.append("mine"))
    monkeypatch.setattr(reader, "toggle_bookmark", lambda: actions.append("bookmark"))

    reader._handle(HELP_TOGGLE_MSG)
    reader._update_hover()
    reader._handle(MINE_MSG)
    reader._handle(BOOKMARK_MSG)
    reader._handle(SUB_NEXT_MSG)
    assert hover_updates == []
    assert actions == []
    assert not [command for command in ipc.commands if command[0] == "sub-seek"]

    reader._handle(HELP_CLOSE_MSG)
    reader._update_hover()
    assert hover_updates == ["hover"]


def test_closing_help_restores_active_tooltip_escape_binding():
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader._bind_tip_keys()

    reader._handle(HELP_TOGGLE_MSG)
    reader._handle(HELP_CLOSE_MSG)

    esc_commands = [command for command in ipc.commands if command[:2] == ("keybind", "ESC")]
    assert esc_commands[-1] == ("keybind", "ESC", "script-message saitenka-tip-close")


def test_tooltip_teardown_does_not_steal_escape_while_help_is_open():
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader._bind_tip_keys()

    reader._handle(HELP_TOGGLE_MSG)
    reader._unbind_tip_keys()

    esc_commands = [command for command in ipc.commands if command[:2] == ("keybind", "ESC")]
    assert esc_commands[-1] == ("keybind", "ESC", "script-message saitenka-help-close")
    reader._handle(HELP_CLOSE_MSG)
    assert any(command[:2] == ("overlay-remove", OverlayId.HELP) for command in ipc.commands)
