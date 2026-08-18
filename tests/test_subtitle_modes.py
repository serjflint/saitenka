"""JP/EN primary modes and background Japanese-track arrival."""

import shutil
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from util import FakeIPC as RuntimeFakeIPC
from util import runtime_gateway

from saitenka.app import subtitle_modes, subtitle_selection
from saitenka.app.controller import Reader
from saitenka.app.languages import MAIN_LANG, SECOND_LANG, looks_japanese
from saitenka.app.subtitles import SubtitleRender
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner
from saitenka.subtitles import CueIndex, parse_srt


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


class FetchJobs:
    def __init__(self, reader: Reader) -> None:
        self.reader = reader
        self.accepted = []
        reader._subtitle_fetch_submit = self.submit

    def submit(self, **kwargs) -> bool:
        self.accepted.append(kwargs)
        return True

    def finish(self, index: int = 0) -> None:
        accepted = self.accepted[index]
        result = subtitle_modes.run_fetch(accepted["request"], threading.Event())
        accepted["on_finished"](
            EffectFinished(
                EffectId(index + 1),
                Owner.SUBTITLE,
                accepted["identity"],
                EffectOutcome.SUCCEEDED,
                result=result,
            )
        )


def _drain_until(reader: Reader, predicate) -> None:
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        reader._drain_events()
        time.sleep(0.001)
    assert predicate()


def test_subtitle_fetch_runs_off_the_event_thread_and_publishes_directly(monkeypatch):
    ipc = RuntimeFakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc)
    event_thread = threading.get_ident()
    worker_thread = None
    messages = []

    def fetch():
        nonlocal worker_thread
        worker_thread = threading.get_ident()
        return None, "provider: no match"

    monkeypatch.setattr(reader, "_toast", lambda message, level: messages.append((message, level)))
    try:
        subtitle_modes.start_fetch(reader, fetch)
        _drain_until(reader, lambda: bool(messages))
        assert worker_thread is not None and worker_thread != event_thread
        assert messages == [("provider: no match", "warn")]
    finally:
        reader.close()
        gateway.close()


def test_subtitle_fetch_lane_rejects_work_beyond_its_bound():
    ipc = RuntimeFakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc)
    release = threading.Event()
    started = [threading.Event(), threading.Event()]
    start_lock = threading.Lock()
    start_index = 0

    def blocked_fetch():
        nonlocal start_index
        with start_lock:
            index = start_index
            start_index += 1
        started[index].set()
        assert release.wait(1)
        return None, "done"

    request = subtitle_modes.SubtitleFetchRequest(
        fetch=blocked_fetch,
        select_if_unchanged=False,
        initial_sid=None,
        replace=False,
        force_select=False,
    )
    submitter = reader._subtitle_fetch_submit
    assert submitter is not None
    outcomes = []
    try:
        accepted = [
            submitter(
                owner=Owner.SUBTITLE,
                identity=index,
                lane="subtitle-fetch",
                request=request,
                on_finished=outcomes.append,
            )
            for index in range(5)
        ]
        assert started[0].wait(1) and started[1].wait(1)
        assert accepted == [True, True, True, True, False]
        assert outcomes[-1].outcome is EffectOutcome.REJECTED
    finally:
        release.set()
        reader.close()
        gateway.close()


def test_newer_explicit_subtitle_choice_supersedes_older_completion(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    jobs = FetchJobs(reader)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    older = tmp_path / "older.ass"
    newer = tmp_path / "newer.ass"
    older.write_text("older", encoding="utf-8")
    newer.write_text("newer", encoding="utf-8")

    subtitle_modes.start_fetch(
        reader,
        lambda: (older, "older"),
        force_select=True,
        name="picker-download",
    )
    subtitle_modes.start_fetch(
        reader,
        lambda: (newer, "newer"),
        force_select=True,
        name="picker-download",
    )
    jobs.finish(1)
    jobs.finish(0)

    added = [command[1] for command in ipc.commands if command[0] == "sub-add"]
    assert added == [str(newer)]


def test_closing_subtitle_lane_quarantines_blocked_fetch(monkeypatch):
    ipc = RuntimeFakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc)
    started = threading.Event()
    release = threading.Event()
    messages = []

    def fetch():
        started.set()
        assert release.wait(1)
        return None, "late"

    monkeypatch.setattr(reader, "_toast", lambda message, level: messages.append((message, level)))
    try:
        subtitle_modes.start_fetch(reader, fetch)
        assert started.wait(1)
        reader._stop.set()
        ipc.close_runtime_job_lane("subtitle-fetch", timeout=0)
        release.set()
        for _ in range(10):
            reader._drain_events()
        assert messages == []
        assert reader._subtitle_fetch_submit is not None
        assert not reader._subtitle_fetch_submit(
            owner=Owner.SUBTITLE,
            identity="after-close",
            lane="subtitle-fetch",
            request=subtitle_modes.SubtitleFetchRequest(
                fetch=lambda: (None, "unused"),
                select_if_unchanged=False,
                initial_sid=None,
                replace=False,
                force_select=False,
            ),
            on_finished=lambda _completion: None,
        )
    finally:
        release.set()
        reader.close()
        gateway.close()


def test_reader_close_quarantines_subtitle_lanes_before_artifact_removal(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    order = []

    def close_lane(name, _timeout):
        assert reader._stop.is_set()
        order.append(name)
        return True

    def remove_artifacts(_path, *, ignore_errors):
        assert ignore_errors
        assert order == [
            "subtitle-fetch",
            "subtitle-picker",
            "cue-annotation",
            "tooltip-render-ahead",
            "tooltip-engaged",
            "speculative-prefetch",
            "mask-atlas-startup",
        ]
        order.append("artifacts")

    ipc.close_runtime_job_lane = close_lane
    monkeypatch.setattr(shutil, "rmtree", remove_artifacts)

    reader.close()

    assert order == [
        "subtitle-fetch",
        "subtitle-picker",
        "cue-annotation",
        "tooltip-render-ahead",
        "tooltip-engaged",
        "speculative-prefetch",
        "mask-atlas-startup",
        "artifacts",
    ]


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
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", rebuilt.append
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
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _reader: None
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
    reader._playback = reader._projection.seed_all(reader._playback, {"sid": 2})
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
        reader.tokenizer,
        "tokenize",
        lambda _text: (_ for _ in ()).throw(AssertionError("English must not be tokenized")),
    )
    monkeypatch.setattr(
        "saitenka.app.subtitle_render.render_plain_subtitle",
        lambda *_args, **_kwargs: SubtitleRender(Image.new("RGBA", (20, 10)), []),
    )

    reader.set_subtitle("Readable English")

    assert reader.sub_text == "Readable English"
    assert reader.lines == [] and reader.tokens == [] and reader.boxes == []


def test_startup_japanese_arrival_replaces_untouched_english_fallback(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    jobs = FetchJobs(reader)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    rebuilt = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", rebuilt.append
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")
    ipc.commands.clear()

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    jobs.finish()

    assert ("sub-add", str(path), "auto", "", "jpn") in ipc.commands
    assert reader.jp_sid == 9 and reader.subtitle_language == "jp"
    assert ("set_property", "sid", 9) in ipc.commands
    assert rebuilt == [reader]
    assert messages == ["Japanese subtitles ready"]


def test_startup_japanese_arrival_preserves_track_changed_during_fetch(tmp_path, monkeypatch):
    other = {"id": 7, "type": "sub", "lang": "kor"}
    ipc = FakeIPC([EN.copy(), other])
    reader = Reader(ipc)
    jobs = FetchJobs(reader)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    ipc.command("set_property", "sid", 7)
    jobs.finish()

    assert ipc.props["sid"] == 7
    assert reader.subtitle_language == "en"


def test_startup_japanese_arrival_is_selected_after_missing_both(tmp_path, monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    jobs = FetchJobs(reader)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _reader: None
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    jobs.finish()

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
    jobs = FetchJobs(reader)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader, "_toast", lambda *_a: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _r: None
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")

    reader.fetch_japanese_subs_async(lambda: (path, "jimaku: ready"))
    jobs.finish()

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
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _r: None
    )
    path = Path(tmp_path / "episode.synced.srt")
    path.write_text("Japanese", encoding="utf-8")

    subtitle_modes._replace_japanese_track(reader, path, "resynced")

    assert ("set_property", "sub-delay", 0.0) in ipc.commands


def test_runtime_retry_uses_current_media_and_coalesces_active_request(monkeypatch):
    ipc = FakeIPC([EN.copy()])
    ipc.props["path"] = "/videos/Show - 02.mkv"
    reader = Reader(ipc)
    jobs = FetchJobs(reader)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
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
            return None, "jimaku: no match; tsukihime: ambiguous"

        return fetch

    reader.configure_subtitle_retry(factory)
    reader.retry_japanese_subtitles()
    reader.retry_japanese_subtitles()

    assert paths == ["/videos/Show - 02.mkv"]
    assert len(jobs.accepted) == 1
    assert messages[:2] == [
        ("Searching Japanese subtitle providers…", "ok"),
        ("Subtitle sync already running", "warn"),
    ]
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)

    jobs.finish()
    assert messages[-1] == ("jimaku: no match; tsukihime: ambiguous", "warn")


def test_runtime_retry_reports_missing_provider_or_media(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    jobs = FetchJobs(reader)
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
    assert jobs.accepted == []


def test_runtime_retry_success_retains_english_until_explicit_switch(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    ipc.props["path"] = "/videos/Show - 03.mkv"
    reader = Reader(ipc)
    jobs = FetchJobs(reader)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    path = tmp_path / "episode.ja.srt"
    path.write_text("Japanese")
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    reader.configure_subtitle_retry(lambda _video: lambda: (path, "tsukihime: added"))
    ipc.commands.clear()

    reader.retry_japanese_subtitles()
    jobs.finish()

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


def test_picker_force_select_activates_japanese_from_english(tmp_path, monkeypatch):
    """An explicit picker choice (``force_select``) selects the chosen source NOW even while English is
    on screen — the keep-current background contract is for unattended fetches, not a deliberate pick
    (regression: from English the pick fell through to the background add and left English up)."""
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))  # English fallback active
    assert reader.subtitle_language == "en"
    messages: list[str] = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_args: messages.append(text))
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _reader: None
    )
    path = tmp_path / "episode.ja.srt"
    path.write_text("Japanese", encoding="utf-8")
    ipc.commands.clear()

    subtitle_modes.apply_fetch_result(
        reader,
        subtitle_modes.SubtitleFetchResult(
            path=path,
            status="picker: chosen",
            select_if_unchanged=False,
            initial_sid=1,
            replace=True,
            force_select=True,
        ),
    )

    assert (
        reader.subtitle_language == "jp"
    )  # took over from English, unlike the background contract
    assert ("sub-add", str(path), "select", "", "jpn") in ipc.commands  # selected now, not "auto"
    assert reader.jp_sid == 9
    assert messages == ["Japanese subtitles selected"]


def test_runtime_retry_resyncs_current_subs_without_querying_providers(tmp_path, monkeypatch):
    # "Retry should just re-time": watching (mistimed) JP → re-sync the CURRENT srt in place (NO
    # provider query — you already have the subs) and swap the on-screen track for the re-timed file.
    from saitenka.app import resync as resync_mod

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
    jobs = FetchJobs(reader)
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
    jobs.finish()

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
    assert subtitle_selection.lang_matches(lang, wants) is expected


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


def _select(ipc, sid):
    ipc.props["sid"] = sid
    for track in ipc.tracks:
        track["selected"] = track.get("id") == sid
        if track["selected"]:
            track["main-selection"] = 0


def test_dropped_untagged_sub_is_adopted_as_japanese_and_indexed(tmp_path):
    # Drag-'n'-drop: mpv adds an UNTAGGED external sub ("unknown language") and makes it primary after
    # startup already fell back to English. It must be adopted as the Japanese primary so cues color
    # (not the plain English path), and indexed from the file on disk.
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert reader.subtitle_language == SECOND_LANG  # only English present at attach
    srt = tmp_path / "dropped.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n岩を砂へ\n", encoding="utf-8")
    ipc.tracks.append(
        {"id": 2, "type": "sub", "lang": None, "external": True, "external-filename": str(srt)}
    )
    _select(ipc, 2)

    subtitle_modes.on_primary_changed(reader, 2)

    assert reader.subtitle_language == MAIN_LANG
    assert reader.jp_sid == 2
    assert reader._sub_index is not None  # indexed from the dropped file


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("岩を砂へ 砂を岩へ", True),  # kanji + hiragana
        ("ソウルソサエティ", True),  # katakana
        ("ﾊﾝｶｸ", True),  # half-width katakana
        ("Turn rock to sand.", False),  # Latin
        ("", False),  # empty
        ("12:34 — ♪", False),  # digits/punctuation/symbols only
    ],
)
def test_looks_japanese_detects_script_by_content(text, expected):
    assert looks_japanese(text) is expected


def test_dropped_untagged_english_sub_stays_plain_not_japanese(tmp_path):
    # Content-based ID: an UNTAGGED English sub (Latin script) the user drops must NOT be miscolored as
    # Japanese — it stays the plain secondary, unlike an untagged Japanese sub.
    ipc = FakeIPC([JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert reader.subtitle_language == MAIN_LANG
    srt = tmp_path / "dropped.en.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTurn rock to sand.\n", encoding="utf-8")
    ipc.tracks.append(
        {"id": 3, "type": "sub", "lang": None, "external": True, "external-filename": str(srt)}
    )
    _select(ipc, 3)

    subtitle_modes.on_primary_changed(reader, 3)

    assert reader.subtitle_language == SECOND_LANG
    assert reader.en_sid == 3
    assert reader.jp_sid == 2  # the Japanese track is not overwritten


def test_manual_switch_to_untagged_track_is_adopted_as_japanese():
    # The same rule for a manual native track cycle (mpv's `j` key) to an untagged embedded track:
    # no file to index, but the render language flips to Japanese so the cue colors.
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    ipc.tracks.append({"id": 3, "type": "sub", "lang": ""})  # empty tag == untagged
    _select(ipc, 3)

    subtitle_modes.on_primary_changed(reader, 3)

    assert reader.subtitle_language == MAIN_LANG
    assert reader.jp_sid == 3


def test_newly_primary_english_tagged_track_is_secondary_not_japanese():
    # The guard against the false wildcard: a real English tag stays the known-language secondary and
    # is NOT adopted as Japanese, even though lang_matches(None, EN_LANGS) would wildcard-match.
    ipc = FakeIPC([JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert reader.subtitle_language == MAIN_LANG
    ipc.tracks.append({"id": 5, "type": "sub", "lang": "eng"})
    _select(ipc, 5)

    subtitle_modes.on_primary_changed(reader, 5)

    assert reader.subtitle_language == SECOND_LANG
    assert reader.en_sid == 5
    assert reader.jp_sid == 2  # the original JP track is untouched


def test_subs_turned_off_adopt_no_track():
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    before = reader.subtitle_language

    subtitle_modes.on_primary_changed(reader, None)

    assert reader.subtitle_language == before
    assert reader.jp_sid is None


def test_force_current_as_japanese_overrides_classification(tmp_path, monkeypatch):
    # The keybind override: force the CURRENT track to Japanese even when it is tagged English (so it
    # would auto-classify as the secondary), letting the user correct a wrong guess from within mpv.
    ipc = FakeIPC([EN.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    srt = tmp_path / "manual.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n岩を砂へ\n", encoding="utf-8")
    ipc.tracks.append(
        {"id": 2, "type": "sub", "lang": "eng", "external": True, "external-filename": str(srt)}
    )
    _select(ipc, 2)
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, *_a: messages.append(text))
    monkeypatch.setattr(reader, "set_subtitle", lambda *_a: None)

    reader.mark_current_subtitle_japanese()

    assert reader.subtitle_language == MAIN_LANG
    assert reader.jp_sid == 2
    assert reader._sub_index is not None
    assert messages == ["Marked current subtitles as Japanese"]


def test_force_current_as_japanese_with_no_track_warns(monkeypatch):
    ipc = FakeIPC()
    reader = Reader(ipc)
    messages = []
    monkeypatch.setattr(reader, "_toast", lambda text, kind="ok": messages.append((text, kind)))

    reader.mark_current_subtitle_japanese()

    assert messages == [("No subtitle track to mark", "warn")]


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


def _one_cue_index() -> CueIndex:
    return CueIndex(parse_srt("1\n00:00:01,000 --> 00:00:02,000\n本\n"))


def test_track_switch_retains_cues_when_the_new_track_cannot_resolve(tmp_path, monkeypatch):
    """A replace whose rebuild can't resolve the just-added track yet must RETAIN the prior cues,
    not blank them — the transient track-switch window must never drop a good index."""
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    old = _one_cue_index()
    reader._sub_index = old
    monkeypatch.setattr(reader, "_toast", lambda *_a: None)
    monkeypatch.setattr(  # the new track isn't resolvable at this instant
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _r: None
    )
    path = tmp_path / "ep.ja.srt"
    path.write_text("Japanese", encoding="utf-8")

    subtitle_modes._replace_japanese_track(reader, path, "resynced")

    assert reader._sub_index is old  # cues retained across the unresolved switch


def test_load_sub_index_retains_prior_cues_on_parse_failure(tmp_path):
    reader = Reader(FakeIPC())
    old = _one_cue_index()
    reader._sub_index = old

    reader.load_sub_index(tmp_path / "missing.srt")  # unreadable → load_index returns None

    assert reader._sub_index is old  # a failed parse never blanks a good index


def test_resync_replace_does_not_clobber_the_primary_when_english_is_active(tmp_path, monkeypatch):
    """A retime (`replace`) only swaps the JP-primary slot when JP is actually primary; from English
    it routes to the non-disruptive background add, so it can never overwrite the wrong slot."""
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader.subtitle_language = SECOND_LANG  # English on screen
    replaced: list = []
    monkeypatch.setattr(
        subtitle_modes, "_replace_japanese_track", lambda *a, **_k: replaced.append(a)
    )
    monkeypatch.setattr(reader, "_toast", lambda *_a: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _r: None
    )
    path = tmp_path / "ep.ja.srt"
    path.write_text("Japanese", encoding="utf-8")

    subtitle_modes.apply_fetch_result(
        reader,
        subtitle_modes.SubtitleFetchResult(
            path=path, status="resynced", select_if_unchanged=False, initial_sid=1, replace=True
        ),
    )

    assert replaced == []  # never clobbered the primary slot from English
    assert reader.subtitle_language == SECOND_LANG


def test_toggle_from_english_returns_to_japanese(monkeypatch):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = Reader(ipc)
    reader.configure_subtitle_mode(subtitle_modes.select_initial(ipc))  # JP active
    monkeypatch.setattr(reader, "_toast", lambda *_args: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda _reader: None
    )

    reader.toggle_subtitle_language()  # JP → EN
    ipc.commands.clear()
    reader.toggle_subtitle_language()  # EN → JP exercises the return-to-Japanese branch

    assert reader.subtitle_language == "jp"
    assert ("set_property", "sid", 2) in ipc.commands
