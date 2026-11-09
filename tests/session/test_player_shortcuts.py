"""Closing utility surfaces restores the player's original input bindings."""

from util import keybind_registry

from saitenka.app.bindings import OVERLAY_TOGGLE_MSG


def test_player_shortcuts_survive_help_and_tooltip_teardown(lookup_session):
    shortcuts = {
        "UP": "player-up",
        "DOWN": "player-down",
        "PGUP": "player-page",
        "ESC": "player-escape",
        "p": "player-pause",
    }
    ui, _anki = lookup_session(shortcuts=shortcuts)
    ui.subtitle("読む").hover_character(0).press("F1").press("ESC").press("ESC")

    bindings = keybind_registry(ui.ipc)
    assert {key: bindings.get(key) for key in shortcuts} == shortcuts


def test_player_escape_binding_survives_preview_and_tooltip_teardown(lookup_session):
    ui, _anki = lookup_session(preview=True, shortcuts={"ESC": OVERLAY_TOGGLE_MSG})
    ui.subtitle("読む").hover_character(0).add_entry().wait_for_preview()
    ui.close_preview().press("ESC")
    assert not ui.dictionary_images

    ui.press("ESC")

    assert ("show-text", "Saitenka hidden", 2000) in ui.ipc.commands
