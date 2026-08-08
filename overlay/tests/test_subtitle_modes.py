"""JP/EN primary modes and background Japanese-track arrival."""

import threading
from pathlib import Path

import pytest
from PIL import Image

from overlay.app import subtitle_modes
from overlay.app.controller import Reader
from overlay.app.subtitles import SubtitleRender


class FakeIPC:
    def __init__(self, tracks=()):
        self.tracks = list(tracks)
        self.props = {"track-list": self.tracks, "pause": False, "secondary-sid": "no"}
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
        if args[:2] == ("set_property", "secondary-sid"):
            self.props["secondary-sid"] = args[2]
        if args[0] == "sub-add":
            self.tracks.append(
                {
                    "id": 9,
                    "type": "sub",
                    "lang": args[4],
                    "external": True,
                    "external-filename": args[1],
                }
            )
            if len(args) > 2 and args[2] == "select":  # mpv's "select" flag activates the new track
                self.props["sid"] = 9
                for track in self.tracks:
                    track["selected"] = track["id"] == 9
        if args[0] == "sub-remove":
            self.tracks[:] = [t for t in self.tracks if t.get("id") != args[1]]
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


def test_configure_releases_preselected_secondary_for_native_track_cycling():
    ipc = FakeIPC([EN.copy(), JP.copy()])
    startup = subtitle_modes.select_initial(ipc)
    ipc.props["secondary-sid"] = 1
    ipc.commands.clear()

    Reader(ipc).configure_subtitle_mode(startup)

    assert ("set_property", "secondary-sid", "no") in ipc.commands


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
    assert messages == ["subtitles: English (1/2)"]


def test_language_switch_releases_secondary_before_selecting_its_track(monkeypatch):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader._translate_on = True
    reader._translation_secondary_sid = 1
    ipc.props["secondary-sid"] = 1
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    monkeypatch.setattr(
        "overlay.app.embedded_subs.build_sub_index_for_current_track", lambda _reader: None
    )
    ipc.commands.clear()

    reader.toggle_subtitle_language()

    secondary_off = ipc.commands.index(("set_property", "secondary-sid", "no"))
    primary_english = ipc.commands.index(("set_property", "sid", 1))
    secondary_japanese = ipc.commands.index(("set_property", "secondary-sid", 2))
    assert secondary_off < primary_english < secondary_japanese


def test_secondary_sid_event_does_not_change_primary_language():
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader.subtitle_language = "en"
    reader._translation_secondary_sid = 2

    subtitle_modes.on_primary_changed(reader, 2)

    assert reader.subtitle_language == "en"


def test_primary_track_event_shows_language_and_counter(monkeypatch):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text: messages.append(text))

    reader._on_property_change({"name": "sid", "data": 1})

    assert messages == ["subtitles: English (1/2)"]


def test_hidden_translation_does_not_reserve_english_secondary():
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    ipc.commands.clear()

    subtitle_modes.release_secondary(reader)

    assert not [
        command for command in ipc.commands if command[:2] == ("set_property", "secondary-sid")
    ]


def test_translation_leases_english_only_while_visible(monkeypatch):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_draw_translation", lambda: None)
    ipc.commands.clear()

    reader.toggle_translation()
    reader.toggle_translation()

    secondary = [
        command[2] for command in ipc.commands if command[:2] == ("set_property", "secondary-sid")
    ]
    assert secondary == [1, "no"]


def test_primary_sid_event_updates_rendering_language():
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader._translate_on = True
    reader._observed = {"sid": 2}
    ipc.props["sid"] = 1
    ipc.commands.clear()

    reader._on_property_change({"name": "sid", "data": 1})

    assert reader.subtitle_language == "en"
    assert ("set_property", "secondary-sid", 2) in ipc.commands


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
        "overlay.app.subtitle_render.render_plain_subtitle",
        lambda *_args, **_kwargs: SubtitleRender(Image.new("RGBA", (20, 10)), []),
    )

    reader.set_subtitle("Readable English")

    assert reader.sub_text == "Readable English"
    assert reader.lines == [] and reader.tokens == [] and reader.boxes == []


def test_startup_japanese_arrival_replaces_untouched_english_fallback(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    rebuilt = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    monkeypatch.setattr(
        "overlay.app.embedded_subs.build_sub_index_for_current_track", rebuilt.append
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese")
    ipc.commands.clear()

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)

    assert ("sub-add", str(path), "auto", "", "jpn") in ipc.commands
    assert reader.jp_sid == 9 and reader.subtitle_language == "jp"
    assert ("set_property", "sid", 9) in ipc.commands
    assert rebuilt == [reader]
    assert messages == ["Japanese subtitles ready"]


def test_startup_japanese_arrival_preserves_track_changed_during_fetch(tmp_path, monkeypatch):
    other = {"id": 7, "type": "sub", "lang": "kor"}
    ipc = FakeIPC([EN.copy(), other])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese")

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    ipc.command("set_property", "sid", 7)
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)

    assert ipc.props["sid"] == 7
    assert reader.subtitle_language == "en"


def test_startup_japanese_arrival_is_selected_after_missing_both(tmp_path, monkeypatch):
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

    assert reader.subtitle_language == "jp"
    assert ("set_property", "sid", 9) in ipc.commands
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_background_japanese_selection_zeroes_stale_sub_delay(tmp_path, monkeypatch):
    """Auto-selecting our fetched track re-establishes authoritative timing (the file's own cue times),
    so a sub-delay mpv restored from watch-later must be zeroed — else it silently rides on top."""
    ipc = FakeIPC()
    ipc.props["sub-delay"] = 10.0  # stale offset a previous run/track left in mpv
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_toast", lambda *_a: None)
    monkeypatch.setattr(
        "overlay.app.embedded_subs.build_sub_index_for_current_track", lambda _r: None
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese")

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)

    assert reader.subtitle_language == "jp"
    assert ("set_property", "sub-delay", 0.0) in ipc.commands


def test_replace_track_zeroes_stale_sub_delay(tmp_path, monkeypatch):
    """The resync/retry swap re-times the FILE, so it owns timing — drop any persisted mpv sub-delay
    (the reported bug: a resynced cue looked wrong until sub-delay was hand-zeroed)."""
    ipc = FakeIPC([EN.copy(), JP.copy()])
    ipc.props["sub-delay"] = -7.5
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))  # jp mode
    monkeypatch.setattr(reader, "_toast", lambda *_a: None)
    monkeypatch.setattr(
        "overlay.app.embedded_subs.build_sub_index_for_current_track", lambda _r: None
    )
    path = Path(tmp_path / "episode.synced.srt")
    path.write_text("Japanese")

    subtitle_modes._replace_japanese_track(reader, path, "resynced")

    assert ("set_property", "sub-delay", 0.0) in ipc.commands


def test_runtime_retry_uses_current_media_and_coalesces_active_request(monkeypatch):
    ipc = FakeIPC([EN.copy()])
    ipc.props["path"] = "/videos/Show - 02.mkv"
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    started = threading.Event()
    release = threading.Event()
    paths = []
    messages = []
    monkeypatch.setattr(
        reader,
        "_toast",
        lambda text, kind="ok": messages.append((text, kind)),
    )

    def factory(video_path):
        paths.append(video_path)

        def fetch():
            started.set()
            assert release.wait(timeout=1)
            return None, "jimaku: no match; tsukihime: ambiguous"

        return fetch

    reader.configure_subtitle_retry(factory)
    reader.retry_japanese_subtitles()
    assert started.wait(timeout=1)
    reader.retry_japanese_subtitles()

    assert paths == ["/videos/Show - 02.mkv"]
    assert len(reader._subtitle_fetch_threads) == 1
    assert messages[:2] == [
        ("Searching Japanese subtitle providers…", "ok"),
        ("Subtitle sync already running", "warn"),
    ]
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)

    release.set()
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)
    assert messages[-1] == ("jimaku: no match; tsukihime: ambiguous", "warn")


def test_runtime_retry_reports_missing_provider_or_media(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    messages = []
    monkeypatch.setattr(
        reader,
        "_toast",
        lambda text, kind="ok": messages.append((text, kind)),
    )

    reader.retry_japanese_subtitles()  # no media at all → media error takes precedence
    ipc.props["path"] = "/videos/Show - 01.mkv"  # media present, but no external subs + no provider
    reader.retry_japanese_subtitles()

    assert messages == [
        ("No media loaded for subtitle search", "warn"),
        ("No Japanese subtitle providers enabled", "warn"),
    ]
    assert reader._subtitle_fetch_threads == []


def test_runtime_retry_success_retains_english_until_explicit_switch(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    ipc.props["path"] = "/videos/Show - 03.mkv"
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    path = tmp_path / "episode.ja.srt"
    path.write_text("Japanese")
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    reader.configure_subtitle_retry(lambda _video: lambda: (path, "tsukihime: added"))
    ipc.commands.clear()

    reader.retry_japanese_subtitles()
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)

    assert reader.subtitle_language == "en"
    assert ("sub-add", str(path), "auto", "", "jpn") in ipc.commands
    assert ("set_property", "sid", 1) in ipc.commands
    assert ("set_property", "sid", 9) not in ipc.commands
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)
    assert messages == [
        "Searching Japanese subtitle providers…",
        "Japanese subtitles ready — Alt+t to switch",
    ]


def test_runtime_retry_resyncs_current_subs_without_querying_providers(tmp_path, monkeypatch):
    # "Retry should just re-time": watching (mistimed) JP → re-sync the CURRENT srt in place (NO
    # provider query — you already have the subs) and swap the on-screen track for the re-timed file.
    from overlay.app import resync as resync_mod

    current = tmp_path / "ep3.ja.srt"
    current.write_text("1\n00:00:02,000 --> 00:00:03,000\nJP\n", encoding="utf-8")
    jp_external = {
        "id": 2,
        "type": "sub",
        "lang": "jpn",
        "external": True,
        "external-filename": str(current),
    }
    ipc = FakeIPC([EN.copy(), jp_external])
    ipc.props["path"] = "/videos/Show - 03.mkv"
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert reader.subtitle_language == "jp"
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    reader.configure_subtitle_retry(  # the provider factory must NOT be called
        lambda _v: (_ for _ in ()).throw(AssertionError("queried providers on re-sync"))
    )
    resynced = []

    def fake_resync_current(video, sub, **_kw):
        resynced.append((str(video), str(sub)))
        sub.write_text(
            "1\n00:00:05,000 --> 00:00:06,000\nJP\n", encoding="utf-8"
        )  # re-timed in place
        return sub

    monkeypatch.setattr(resync_mod, "resync_current", fake_resync_current)
    ipc.commands.clear()

    reader.retry_japanese_subtitles()
    reader._subtitle_fetch_threads[0].join(timeout=1)
    subtitle_modes.apply_fetch_results(reader)

    # video is wrapped in Path before resync → compare the OS-native form (Windows uses backslashes)
    assert resynced == [(str(Path("/videos/Show - 03.mkv")), str(current))]  # CURRENT sub, no fetch
    assert ("sub-remove", 2) in ipc.commands  # stale track dropped
    assert (
        "sub-add",
        str(current),
        "select",
        "",
        "jpn",
    ) in ipc.commands  # re-timed file re-selected
    assert reader.jp_sid == 9 and reader.subtitle_language == "jp"
    assert reader._sub_index is not None  # rebuilt against the re-timed cues
    # single-cue sub → window too small → falls back to a whole-file re-sync (still no provider query)
    assert "Re-timing subtitles from here…" in messages


@pytest.mark.parametrize(
    ("lang", "wants", "expected"),
    [
        ("jpn", ["jp"], True),  # tag longer than want — low.startswith(want)
        ("jp", ["jpn"], True),  # want longer than tag — want.startswith(low)
        ("JA", ["ja"], True),  # case-insensitive
        ("Japanese", ["ja"], True),  # full language name
        ("ger", ["ja", "jpn", "jp"], False),  # unrelated tag never matches
        (None, ["ja"], True),  # untagged track is a wildcard — load-bearing for foreign-only files
        ("", ["ja"], True),  # empty tag likewise matches
        ("ja", [""], False),  # empty want is ignored, not a wildcard
        ("ja", [], False),  # no wants → no match
    ],
)
def test_lang_matches_prefix_rule_and_wildcard_edges(lang, wants, expected):
    assert subtitle_modes.lang_matches(lang, wants) is expected


def test_foreign_only_tracks_use_the_selected_one_as_primary():
    ipc = FakeIPC(
        [
            {"id": 4, "type": "sub", "lang": "kor"},
            {"id": 3, "type": "sub", "lang": "ger", "selected": True, "main-selection": 0},
        ]
    )
    assert subtitle_modes.discover_tracks(ipc) == subtitle_modes.SubtitleTracks(jp_sid=3, en_sid=4)


def test_foreign_only_tracks_default_to_first_when_none_selected():
    ipc = FakeIPC(
        [{"id": 5, "type": "sub", "lang": "ger"}, {"id": 6, "type": "sub", "lang": "kor"}]
    )
    assert subtitle_modes.discover_tracks(ipc) == subtitle_modes.SubtitleTracks(jp_sid=5, en_sid=6)


def test_single_foreign_track_has_no_secondary():
    ipc = FakeIPC([{"id": 7, "type": "sub", "lang": "ger"}])
    assert subtitle_modes.discover_tracks(ipc) == subtitle_modes.SubtitleTracks(
        jp_sid=7, en_sid=None
    )


def test_announce_names_a_japanese_track(monkeypatch):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    seen = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: seen.append(text))

    subtitle_modes.announce_track(reader, 2)

    assert seen == ["subtitles: Japanese (2/2)"]


def test_announce_passes_through_an_unknown_language(monkeypatch):
    ipc = FakeIPC([{"id": 3, "type": "sub", "lang": "ger"}])
    reader = Reader(ipc)
    seen = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: seen.append(text))

    subtitle_modes.announce_track(reader, 3)

    assert seen == ["subtitles: ger (1/1)"]


def test_toggle_from_english_returns_to_japanese(monkeypatch):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))  # JP active
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    monkeypatch.setattr(
        "overlay.app.embedded_subs.build_sub_index_for_current_track", lambda _reader: None
    )

    reader.toggle_subtitle_language()  # JP → EN
    ipc.commands.clear()
    reader.toggle_subtitle_language()  # EN → JP exercises the return-to-Japanese branch

    assert reader.subtitle_language == "jp"
    assert ("set_property", "sid", 2) in ipc.commands
