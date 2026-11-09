"""Closing utility surfaces restores the player's original input bindings."""

from util import keybind_registry


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
