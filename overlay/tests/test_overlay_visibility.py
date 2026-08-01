"""Runtime visibility for every Saitenka-owned OSD surface."""

from PIL import Image

from overlay.app.config import KeyOptions, ReaderOptions
from overlay.app.controller import Reader
from overlay.app.overlay_ids import OverlayId
from overlay.mpvio.osd import Overlay


class FakeIPC:
    def __init__(self):
        self.commands: list[tuple] = []
        self.props = {"sub-visibility": False, "osd-level": 0}

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        if args[0] == "set_property":
            self.props[args[1]] = args[2]
        return {"error": "success"}


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


def test_alt_o_hides_saitenka_and_restores_native_osd():
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader.ov.show(_image(), oid=OverlayId.SUB)
    reader._register_keybinds()
    bindings = {
        command[1]: command[2].removeprefix("script-message ")
        for command in ipc.commands
        if command[0] == "keybind"
    }
    ipc.commands.clear()

    reader._handle(bindings["Alt+o"])

    assert ("overlay-remove", OverlayId.SUB) in ipc.commands
    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert ("set_property", "osd-level", 1) in ipc.commands
    reader.close()


def test_showing_overlay_restores_saitenka_subtitle_policy():
    ipc = FakeIPC()
    reader = Reader(ipc)

    reader.toggle_overlay()
    reader.toggle_overlay()

    assert ipc.props["sub-visibility"] is False
    assert ipc.props["osd-level"] == 0


def test_overlay_toggle_key_is_configurable():
    ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(overlay_toggle_key="Ctrl+o"))

    Reader(ipc, options=options)._register_keybinds()

    bindings = {command[1] for command in ipc.commands if command[0] == "keybind"}
    assert "Ctrl+o" in bindings and "Alt+o" not in bindings


def test_hiding_overlay_releases_translation_track_for_native_subtitle_cycling(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader.jp_sid = 2
    reader.en_sid = 1
    reader._observing = True
    reader._observed = {"sid": 2}
    reader._translate_on = True
    reader._translation_secondary_sid = 1
    monkeypatch.setattr(reader, "_draw_translation", lambda: None)

    reader.toggle_overlay()
    reader.toggle_overlay()

    secondary = [
        command[2] for command in ipc.commands if command[:2] == ("set_property", "secondary-sid")
    ]
    assert secondary == ["no", 1]
