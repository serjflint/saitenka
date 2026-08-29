"""Runtime visibility for every Saitenka-owned OSD surface."""

import util
from PIL import Image
from session_builder import build_session
from util import keybind_registry

from saitenka.app import bindings as app_bindings
from saitenka.app.config import KeyOptions, ReaderOptions
from saitenka.app.overlay_ids import OverlayId
from saitenka.mpvio.osd import Overlay
from saitenka.runtime.events import SubtitleSecondaryLeased, SubtitleTracksDiscovered


class FakeIPC(util.FakeIPC):
    def __init__(self):
        super().__init__()
        self.props["sub-visibility"] = False  # osd-level left to mpv; toggle no longer manages it
        util.session_gateway(self)

    def command(self, *args):
        reply = super().command(*args)
        if args[0] == "set_property":
            self.props[args[1]] = args[2]
        return reply


def _image(color: str = "red") -> Image.Image:
    return Image.new("RGBA", (2, 2), color)


def test_hidden_overlay_caches_draw_without_uploading():
    ipc = FakeIPC()
    overlay = Overlay(ipc)
    overlay.set_visible(visible=False)
    ipc.commands.clear()

    overlay.show(_image(), oid=OverlayId.SUB)

    assert not [command for command in ipc.commands if command[0] == "overlay-add"]
    overlay.close()


def test_showing_overlay_restores_latest_hidden_draw():
    ipc = FakeIPC()
    overlay = Overlay(ipc)
    overlay.set_visible(visible=False)
    overlay.show(_image("red"), oid=OverlayId.SUB)
    overlay.show(_image("blue"), oid=OverlayId.SUB)
    ipc.commands.clear()

    overlay.set_visible(visible=True)

    adds = [command for command in ipc.commands if command[0] == "overlay-add"]
    assert len(adds) == 1 and adds[0][1] == OverlayId.SUB
    overlay.close()


def test_alt_o_hides_saitenka_and_restores_native_subs():
    ipc = FakeIPC()
    reader = build_session(ipc)
    reader.graph.overlay.show(_image(), oid=OverlayId.SUB)
    reader.graph.commands.install_input()
    bindings = keybind_registry(ipc)
    ipc.commands.clear()

    reader.command(bindings["Alt+o"])

    assert ("overlay-remove", OverlayId.SUB) in ipc.commands
    assert ("set_property", "sub-visibility", True) in ipc.commands
    # osd-level is NOT touched — it stays at mpv's default (1) so native OSD messages work throughout
    assert not any(c[:2] == ("set_property", "osd-level") for c in ipc.commands)
    reader.close()


def test_showing_overlay_restores_saitenka_subtitle_policy():
    ipc = FakeIPC()
    reader = build_session(ipc)

    reader.command(app_bindings.OVERLAY_TOGGLE_MSG)
    reader.command(app_bindings.OVERLAY_TOGGLE_MSG)

    assert ipc.props["sub-visibility"] is False
    assert "osd-level" not in ipc.props  # toggle never manages osd-level anymore


def test_overlay_toggle_key_is_configurable():
    ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(overlay_toggle_key="Ctrl+o"))

    build_session(ipc, options=options).graph.commands.install_input()

    bindings = set(keybind_registry(ipc))
    assert "Ctrl+o" in bindings and "Alt+o" not in bindings


def test_hiding_overlay_releases_translation_track_for_native_subtitle_cycling():
    ipc = FakeIPC()
    reader = build_session(ipc)
    reader.graph.track_commands.declare(SubtitleTracksDiscovered(2, 1))
    reader.graph.playback.install_seed({"sid": 2})
    reader.command(app_bindings.TRANS_MSG)
    reader.graph.track_commands.declare(SubtitleSecondaryLeased(1))
    ipc.commands.clear()

    reader.command(app_bindings.OVERLAY_TOGGLE_MSG)
    assert reader.graph.translation.state.drawn is None
    reader.command(app_bindings.OVERLAY_TOGGLE_MSG)

    secondary = [
        command[2] for command in ipc.commands if command[:2] == ("set_property", "secondary-sid")
    ]
    assert secondary == ["no", 1]
