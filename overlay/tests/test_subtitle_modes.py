"""JP/EN primary modes and background Japanese-track arrival."""

from pathlib import Path

from PIL import Image

from overlay.app import subtitle_modes
from overlay.app.controller import Reader
from overlay.app.subtitles import SubtitleRender


class FakeIPC:
    def __init__(self, tracks=()):
        self.tracks = list(tracks)
        self.props = {"track-list": self.tracks, "pause": False}
        self.commands: list[tuple] = []

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        if args[:2] == ("set_property", "sid"):
            self.props["sid"] = args[2]
            for track in self.tracks:
                track["selected"] = track["id"] == args[2]
                if track["selected"]:
                    track["main-selection"] = 0
        if args[0] == "sub-add":
            self.tracks.append({"id": 9, "type": "sub", "lang": args[4], "external": True})
        return {"data": None}


JP = {"id": 2, "type": "sub", "lang": "jpn"}
EN = {"id": 1, "type": "sub", "lang": "eng"}


def test_startup_prefers_japanese_and_remembers_both_tracks():
    ipc = FakeIPC([EN.copy(), JP.copy()])
    startup = subtitle_modes.select_initial(ipc)
    assert startup == subtitle_modes.SubtitleStartup(
        subtitle_modes.SubtitleTracks(jp_sid=2, en_sid=1), "jp"
    )
    assert ("set_property", "sid", 2) in ipc.commands


def test_startup_falls_back_to_english_when_japanese_is_missing():
    ipc = FakeIPC([EN.copy()])
    startup = subtitle_modes.select_initial(ipc)
    assert startup.active == "en"
    assert startup.tracks == subtitle_modes.SubtitleTracks(jp_sid=None, en_sid=1)
    assert ("set_property", "sid", 1) in ipc.commands


def test_startup_with_no_subtitles_fails_softly():
    ipc = FakeIPC()
    startup = subtitle_modes.select_initial(ipc)
    assert startup.active is None
    assert not [c for c in ipc.commands if c[:2] == ("set_property", "sid")]


def test_language_switch_changes_only_existing_target_and_rebuilds_index(monkeypatch):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader._translate_on = True
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    rebuilt = []
    monkeypatch.setattr(
        "overlay.app.embedded_subs.build_sub_index_for_current_track", rebuilt.append
    )
    ipc.commands.clear()

    reader.toggle_subtitle_language()

    assert reader.subtitle_language == "en"
    assert reader._translate_on is True
    assert ("set_property", "sid", 1) in ipc.commands
    assert ("set_property", "secondary-sid", 2) in ipc.commands
    assert rebuilt == [reader]
    assert not [c for c in ipc.commands if c[0] in {"seek", "sub-seek"}]
    assert not [c for c in ipc.commands if c[:2] == ("set_property", "pause")]
    assert messages == ["subtitle mode: EN"]


def test_unavailable_language_keeps_current_mode(monkeypatch):
    ipc = FakeIPC([JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, kind="ok": messages.append((text, kind)))
    ipc.commands.clear()

    reader.toggle_subtitle_language()

    assert reader.subtitle_language == "jp"
    assert not [c for c in ipc.commands if c[:2] == ("set_property", "sid")]
    assert messages == [("EN subtitles unavailable", "warn")]


def test_english_primary_is_plain_and_noninteractive(monkeypatch):
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(
        "overlay.app.controller.tokenize",
        lambda _text: (_ for _ in ()).throw(AssertionError("English must not be tokenized")),
    )
    monkeypatch.setattr(
        "overlay.app.controller.render_plain_subtitle",
        lambda *_args, **_kwargs: SubtitleRender(Image.new("RGBA", (20, 10)), []),
    )

    reader.set_subtitle("Readable English")

    assert reader.sub_text == "Readable English"
    assert reader.lines == [] and reader.tokens == [] and reader.boxes == []


def test_background_japanese_arrival_adds_without_selecting(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese")
    ipc.commands.clear()

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)

    assert ("sub-add", str(path), "auto", "", "jpn") in ipc.commands
    assert reader.jp_sid == 9 and reader.subtitle_language == "en"
    assert ("set_property", "sid", 1) in ipc.commands
    assert ("set_property", "sid", 9) not in ipc.commands
    assert messages == ["Japanese subtitles ready — Alt+t to switch"]


def test_background_japanese_arrival_can_be_selected_after_missing_both(tmp_path, monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    monkeypatch.setattr(
        "overlay.app.embedded_subs.build_sub_index_for_current_track", lambda _reader: None
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese")

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)
    ipc.commands.clear()
    reader.toggle_subtitle_language()

    assert reader.subtitle_language == "jp"
    assert ("set_property", "sid", 9) in ipc.commands
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)
