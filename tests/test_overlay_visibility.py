"""Runtime visibility for every Saitenka-owned OSD surface."""

import util
from PIL import Image
from util import keybind_registry

from saitenka.app.config import KeyOptions, ReaderOptions
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.session_controller import SessionController
from saitenka.mpvio.osd import Overlay
from saitenka.runtime.events import SubtitleSecondaryLeased, SubtitleTracksDiscovered


class FakeIPC(util.FakeIPC):
    def __init__(self):
        super().__init__()
        self.props["sub-visibility"] = False  # osd-level left to mpv; toggle no longer manages it
        util.runtime_gateway(self)

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
    reader = SessionController(ipc)
    reader.ov.show(_image(), oid=OverlayId.SUB)
    reader._register_keybinds()
    bindings = keybind_registry(ipc)
    ipc.commands.clear()

    reader._handle(bindings["Alt+o"])

    assert ("overlay-remove", OverlayId.SUB) in ipc.commands
    assert ("set_property", "sub-visibility", True) in ipc.commands
    # osd-level is NOT touched — it stays at mpv's default (1) so native OSD messages work throughout
    assert not any(c[:2] == ("set_property", "osd-level") for c in ipc.commands)
    reader.close()


def test_showing_overlay_restores_saitenka_subtitle_policy():
    ipc = FakeIPC()
    reader = SessionController(ipc)

    reader.toggle_overlay()
    reader.toggle_overlay()

    assert ipc.props["sub-visibility"] is False
    assert "osd-level" not in ipc.props  # toggle never manages osd-level anymore


def test_overlay_toggle_key_is_configurable():
    ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(overlay_toggle_key="Ctrl+o"))

    SessionController(ipc, options=options)._register_keybinds()

    bindings = set(keybind_registry(ipc))
    assert "Ctrl+o" in bindings and "Alt+o" not in bindings


def test_hiding_overlay_releases_translation_track_for_native_subtitle_cycling(monkeypatch):
    ipc = FakeIPC()
    reader = SessionController(ipc)
    reader.declare_subtitle(SubtitleTracksDiscovered(2, 1))
    reader._observing = True
    reader._playback = reader._projection.seed_all(reader._playback, {"sid": 2})
    reader.translate_on = True
    reader.declare_subtitle(SubtitleSecondaryLeased(1))
    monkeypatch.setattr(reader, "draw_translation", lambda: None)

    reader.toggle_overlay()
    reader.toggle_overlay()

    secondary = [
        command[2] for command in ipc.commands if command[:2] == ("set_property", "secondary-sid")
    ]
    assert secondary == ["no", 1]
