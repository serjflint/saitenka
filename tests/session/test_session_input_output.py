"""Session lifecycle and command behavior observed at the mpv boundary."""

import pytest
from util import FakeIPC, overlay_frame

from saitenka.app import bindings, subtitle_adapter
from saitenka.app.config import KeyOptions, ReaderOptions
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.events import ConnectionLost, ConnectionReady, ConnectionReplaced


@pytest.fixture
def player(make_session):
    def create(*, properties=None, options=None):
        ipc = FakeIPC()
        ipc.props.update({"osd-dimensions": {"w": 1280, "h": 720}, **(properties or {})})
        session = make_session(
            ipc,
            options=(options or ReaderOptions()).with_overrides(prefetch=False),
            infrastructure=SessionInfrastructure(renderer=NullRenderer()),
        )
        session.start()
        session.pump()
        return session, ipc

    return create


def test_close_removes_help_published_by_the_started_session(player):
    session, ipc = player()
    session.command(bindings.HELP_TOGGLE_MSG)
    assert overlay_frame(ipc, OverlayId.HELP) is not None

    session.close()

    assert overlay_frame(ipc, OverlayId.HELP) is None


def test_cue_change_rejects_stale_commands_but_allows_global_toggle_then_recovers(
    player, monkeypatch
):
    session, ipc = player(
        properties={"sub-text": "old\nline", "sid": 1, "sub-start": 1.0, "sub-end": 2.0}
    )
    copied = []
    monkeypatch.setattr(subtitle_adapter, "copy_clipboard", copied.append)
    ipc.commands.clear()
    ipc.set_prop("sub-text", "new\nline")
    for message in (bindings.COPY_LINE_MSG, bindings.SUB_NEXT_MSG, bindings.OVERLAY_TOGGLE_MSG):
        ipc.emit({"event": "client-message", "args": [message]})

    session.pump()

    assert copied == []
    assert not any(command[0] == "sub-seek" for command in ipc.commands)
    assert ("show-text", "Saitenka hidden", 2000) in ipc.commands
    session.command(bindings.COPY_LINE_MSG)
    session.command(bindings.SUB_NEXT_MSG)
    session.command(bindings.OVERLAY_TOGGLE_MSG)
    assert copied == ["new\nline"]
    assert [command for command in ipc.commands if command[0] == "sub-seek"] == [("sub-seek", "1")]
    assert [command[1] for command in ipc.commands if command[0] == "show-text"] == [
        "Saitenka hidden",
        "Saitenka shown",
    ]


def test_help_pages_capture_overlay_toggle_and_closed_help_commands_do_nothing(player):
    session, ipc = player(properties={"osd-dimensions": {"w": 480, "h": 220}})
    before = len(ipc.commands)
    session.command(bindings.HELP_NEXT_MSG)
    session.command(bindings.HELP_CLOSE_MSG)
    assert not any(command[0] in {"overlay-add", "show-text"} for command in ipc.commands[before:])
    session.command(bindings.HELP_TOGGLE_MSG)
    first = overlay_frame(ipc, OverlayId.HELP)[0]
    session.command(bindings.OVERLAY_TOGGLE_MSG)
    assert overlay_frame(ipc, OverlayId.HELP)[0].tobytes() == first.tobytes()
    session.command(bindings.HELP_NEXT_MSG)
    assert overlay_frame(ipc, OverlayId.HELP)[0].tobytes() != first.tobytes()
    session.command(bindings.HELP_CLOSE_MSG)
    assert overlay_frame(ipc, OverlayId.HELP) is None
    assert not any(command[0] == "show-text" for command in ipc.commands)
    assert not any(
        command[0] in {"seek", "sub-seek"} or command[:2] == ("set_property", "pause")
        for command in ipc.commands
    )


def test_started_session_publishes_configured_forced_sections(player):
    _, ipc = player(
        options=ReaderOptions(
            keys=KeyOptions(
                help_key="Ctrl+h",
                hover_pause_key="Ctrl+q",
                sub_prev_key="Ctrl+1",
                sub_next_key="Ctrl+2",
                sub_replay_key="Ctrl+3",
            )
        )
    )
    sections = {command[1]: command for command in ipc.commands if command[0] == "define-section"}
    global_section = sections[bindings.GLOBAL_SECTION]
    mouse_section = sections[bindings.MOUSE_SECTION]
    assert global_section[3] == mouse_section[3] == "force"
    for section in (global_section, mouse_section):
        keys = [line.split(" ", 1)[0] for line in section[2].splitlines()]
        assert len(keys) == len(set(keys))
    global_keys = dict(line.split(" ", 1) for line in global_section[2].splitlines())
    assert (
        global_keys.items()
        >= {
            "Ctrl+h": "script-message saitenka-toggle-help",
            "Ctrl+q": "script-message saitenka-toggle-hover-pause",
            "Ctrl+1": "script-message saitenka-sub-prev",
            "Ctrl+2": "script-message saitenka-sub-next",
            "Ctrl+3": "script-message saitenka-sub-replay",
        }.items()
    )
    assert {"F1", "z", "Z", "x"}.isdisjoint(global_keys)
    assert "MBTN_LEFT script-message saitenka-click" in mouse_section[2].splitlines()
    assert "WHEEL_UP script-message saitenka-scroll-up" in mouse_section[2].splitlines()
    assert any(
        command[:2] == ("enable-section", bindings.GLOBAL_SECTION) for command in ipc.commands
    )
    assert not any(command[0] == "keybind" for command in ipc.commands)


@pytest.mark.parametrize(("selected", "target", "text"), [(1, 2, "hello"), (2, 1, "猫")])
def test_language_toggle_releases_secondary_before_selecting_target(player, selected, target, text):
    session, ipc = player(
        properties={
            "track-list": [
                {"id": 1, "type": "sub", "lang": "eng", "selected": selected == 1},
                {"id": 2, "type": "sub", "lang": "jpn", "selected": selected == 2},
            ],
            "sid": selected,
            "sub-text": text,
            "sub-start": 1.0,
            "sub-end": 2.0,
        }
    )
    ipc.commands.clear()

    session.command(bindings.SUBTITLE_LANGUAGE_MSG)

    assert [
        command
        for command in ipc.commands
        if command[:2] in {("set_property", "sid"), ("set_property", "secondary-sid")}
    ] == [
        ("set_property", "secondary-sid", "no"),
        ("set_property", "sid", target),
    ]


def test_connection_replacement_restores_help_input_and_close_retires_it(player):
    session, ipc = player()
    assert ipc.publish_runtime_event(ConnectionLost(0))
    session.pump()
    session.command(bindings.HELP_TOGGLE_MSG)
    assert overlay_frame(ipc, OverlayId.HELP) is None
    assert ipc.publish_runtime_event(ConnectionReady(1))
    assert ipc.publish_runtime_event(ConnectionReplaced(1))
    session.pump()

    session.command(bindings.HELP_TOGGLE_MSG)

    assert overlay_frame(ipc, OverlayId.HELP) is not None
    session.close()
    assert overlay_frame(ipc, OverlayId.HELP) is None


@pytest.mark.parametrize(
    ("command", "surface"),
    [(bindings.SIDEBAR_MSG, OverlayId.SIDEBAR), (bindings.ANALYSIS_MSG, OverlayId.ANALYSIS)],
)
def test_feature_toggle_publishes_then_removes_its_surface(player, command, surface):
    session, ipc = player()
    session.command(command)
    assert overlay_frame(ipc, surface) is not None
    session.command(command)
    assert overlay_frame(ipc, surface) is None


def test_started_session_observes_cues_and_warns_on_empty_copy(player):
    session, ipc = player()
    assert "sub-text" in {
        command[2] for command in ipc.commands if command[0] == "observe_property"
    }
    session.command(bindings.COPY_LINE_MSG)
    assert overlay_frame(ipc, OverlayId.TOAST) is not None


@pytest.mark.parametrize(
    ("command", "delta"),
    [(bindings.SUB_PREV_MSG, "-1"), (bindings.SUB_NEXT_MSG, "1"), (bindings.SUB_REPLAY_MSG, "0")],
)
def test_subtitle_navigation_commands_emit_the_requested_seek(player, command, delta):
    session, ipc = player()
    ipc.commands.clear()
    session.command(command)
    assert [item for item in ipc.commands if item[0] == "sub-seek"] == [("sub-seek", delta)]
