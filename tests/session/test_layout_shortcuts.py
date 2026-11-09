import sys

import pytest
from driver import Driver
from tip_fakes import LinkingDS
from util import FakeIPC, keybind_registry, press

from saitenka.app.bindings import HELP_TOGGLE_MSG, active_bindings
from saitenka.app.config import KeyOptions, ReaderOptions
from saitenka.app.session.factory import SessionServices


@pytest.mark.parametrize(
    ("key", "russian"),
    [("Ctrl+h", "Ctrl+р"), ("Ctrl+Shift+m", "Ctrl+Ь"), ("Alt+T", "Alt+Е")],
)
def test_russian_shortcut_opens_the_same_help_surface(make_session, key, russian):
    ipc = FakeIPC()
    session = make_session(ipc, options=ReaderOptions(keys=KeyOptions(help_key=key)))
    session.graph.commands.install_input()

    press(session, ipc, russian)

    assert session.graph.help.state.open


def test_tooltip_aliases_retire_with_the_tooltip(make_session):
    ipc = FakeIPC()
    session = make_session(ipc, services=SessionServices(dictionaries=LinkingDS()))
    session.graph.commands.install_input()
    ipc.set_prop("sub-text", "読む")
    session.pump()
    ui = Driver(session).move_to_word(0)
    assert ui.tip_shown
    assert keybind_registry(ipc)["з"] == keybind_registry(ipc)["p"]

    ui.leave()
    ipc.fire_runtime_timer("lifecycle:tooltip-hide")
    session.pump()

    assert not ui.tip_shown
    assert "з" not in keybind_registry(ipc)
    assert "p" not in keybind_registry(ipc)


def test_explicit_cyrillic_shortcut_wins_over_automatic_alias(make_session):
    ipc = FakeIPC()
    keys = KeyOptions(help_key="Ctrl+ь", mine_key="Ctrl+m")
    session = make_session(ipc, options=ReaderOptions(keys=keys))
    session.graph.commands.install_input()

    press(session, ipc, "Ctrl+ь")

    assert session.graph.help.state.open
    assert keybind_registry(ipc)["Ctrl+ь"] == HELP_TOGGLE_MSG


def test_layout_aliases_do_not_duplicate_the_shortcut_catalog():
    keys = KeyOptions(help_key="Ctrl+h")
    references = active_bindings(keys, "global")
    assert [b.key for b in references if b.spec.message == HELP_TOGGLE_MSG] == ["Ctrl+h"]


@pytest.mark.parametrize(
    ("key", "russian"),
    [
        ("SHARP", "№"),
        ("0x6d", "ь"),
        ("Ctrl+Ctrl+h", "Ctrl+р"),
        ("ctrl+shift+h", "Ctrl+Р"),
        (";", "ж"),
        ("[", "х"),
        ("`", "]" if sys.platform == "darwin" else "ё"),
        ("\\", "ё" if sys.platform == "darwin" else "\\"),
    ],
)
def test_layout_aliases_cover_configured_modifiers_and_punctuation(make_session, key, russian):
    ipc = FakeIPC()
    session = make_session(
        ipc, options=ReaderOptions(keys=KeyOptions(help_key=key, sidebar_key="", analysis_key=""))
    )
    session.graph.commands.install_input()
    assert keybind_registry(ipc)[key] == HELP_TOGGLE_MSG
    assert keybind_registry(ipc)[russian] == HELP_TOGGLE_MSG


@pytest.mark.parametrize("key", ["Ctrl+0x44c", "ctrl+CTRL+ь"])
def test_explicit_equivalent_key_spelling_reserves_the_layout_alias(key):
    from saitenka.app.bindings import MINE_MSG, shortcut_bindings

    bindings = shortcut_bindings(KeyOptions(help_key=key), "global")

    assert all(b.key != "Ctrl+ь" for b in bindings if b.spec.message == MINE_MSG)
