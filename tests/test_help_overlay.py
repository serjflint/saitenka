"""In-player help uses effective bindings and remains playback-neutral."""

from util import FakeIPC, keybind_registry

from saitenka.app import tooltip
from saitenka.app.bindings import (
    BOOKMARK_MSG,
    HELP_CLOSE_MSG,
    HELP_NEXT_MSG,
    HELP_TOGGLE_MSG,
    MINE_MSG,
    SUB_NEXT_MSG,
    active_bindings,
)
from saitenka.app.config import KeyOptions, PanelOptions, ReaderOptions
from saitenka.app.controller import Reader
from saitenka.app.overlay_ids import OverlayId
from saitenka.render.help import render_page


def _entries(reader: Reader):
    return [
        (section.title, entry)
        for page in reader._help_document().pages
        for section in page.sections
        for entry in section.entries
    ]


def test_default_and_configured_help_keys_are_registered():
    default_ipc = FakeIPC()
    Reader(default_ipc)._register_keybinds()
    assert keybind_registry(default_ipc)["F1"] == "saitenka-toggle-help"

    custom_ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(help_key="Ctrl+h"))
    Reader(custom_ipc, options=options)._register_keybinds()
    custom_binds = keybind_registry(custom_ipc)
    assert custom_binds["Ctrl+h"] == "saitenka-toggle-help"
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
        (binding.key, binding.spec.message) for binding in active_bindings(reader.keys, "global")
    }
    reader._register_keybinds()
    # The registry spans the global section AND the forced mouse one, so compare against the
    # global scope alone — this asserts the catalog, not which section a key landed in.
    assert set(keybind_registry(reader.ipc).items()) >= expected


def test_small_osd_pages_and_repeats_navigation_hints():
    reader = Reader(FakeIPC(), anki=object())
    reader.osd = (480, 220)
    document = reader._help_document()

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


def test_ui_scale_enlarges_help_document():
    normal = Reader(FakeIPC(), options=ReaderOptions())
    enlarged = Reader(FakeIPC(), options=ReaderOptions(panels=PanelOptions(scale=1.5)))
    normal.osd = enlarged.osd = (1920, 1080)

    normal_document = normal._help_document()
    enlarged_document = enlarged._help_document()

    assert enlarged_document.width > normal_document.width
    assert enlarged_document.height > normal_document.height
    assert enlarged_document.scale == 1.5


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


def test_the_reference_lists_only_bindings_that_opt_in() -> None:
    """`help_entries` is a filter over bindings, checkable without a session — which is the point
    of it no longer taking one."""
    from types import SimpleNamespace

    from saitenka.app.help_overlay import help_entries

    def binding(key: str, *, shown: bool):
        spec = SimpleNamespace(
            section="s", label=key, context="c", source="src", show_in_help=shown
        )
        return SimpleNamespace(key=key, spec=spec)

    entries = help_entries([binding("a", shown=True), binding("b", shown=False)])

    assert [entry.key for entry in entries] == ["a"]


def test_the_footer_names_the_configured_close_key() -> None:
    """The reference is the only place a user learns the key, so it must not hard-code one."""
    from saitenka.app.help_overlay import help_footer

    assert help_footer("Alt+/").startswith("Alt+/")
