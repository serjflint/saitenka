"""JP/EN primary modes and background Japanese-track arrival."""

import shutil
import threading
import time
from pathlib import Path

import pytest
from saitenka_subtitles import CueIndex, parse_srt
from saitenka_tokenize.languages import MAIN_LANG, SECOND_LANG, looks_japanese
from session_builder import TestSession, build_session
from util import FakeIPC as RuntimeFakeIPC
from util import RecordingRasterProvider, bare_gateway, session_gateway

from saitenka.app import bindings as app_bindings
from saitenka.app import subtitle_modes, subtitle_selection
from saitenka.app.features.translation import TranslationInputs
from saitenka.app.session.lifecycle import LiveState
from saitenka.app.subtitle_render import SubtitleRenderer
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner
from saitenka.runtime.events import SubtitleLanguageChanged, SubtitleSecondaryLeased


class FakeIPC(RuntimeFakeIPC):
    """Gateway-wired, and a small mpv track simulator on top: selecting a sid or adding/removing an
    external track updates ``track-list`` the way mpv does, so selection policy is exercised against
    state rather than against a recorded call list."""

    def __init__(self, tracks=()):
        super().__init__()
        self.tracks = list(tracks)
        self.props.update({"track-list": self.tracks, "pause": False, "secondary-sid": "no"})

    def command(self, *args):
        reply = super().command(*args)
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
        return reply


JP = {"id": 2, "type": "sub", "lang": "jpn"}
EN = {"id": 1, "type": "sub", "lang": "eng"}


def hold_translation(reader: TestSession) -> None:
    reader.graph.translation.toggle(
        TranslationInputs(
            surfaces_visible=False,
            tooltip_selected=False,
            secondary_text=None,
            osd=(1280, 720),
        )
    )


class FetchJobs:
    def __init__(self) -> None:
        self.accepted = []

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


def reader_with_fetch_jobs(ipc, monkeypatch) -> tuple[TestSession, FetchJobs]:
    jobs = FetchJobs()
    monkeypatch.setattr(subtitle_modes, "configure_runtime_job", lambda _ipc: jobs.submit)
    return build_session(ipc), jobs


def _drain_until(reader: TestSession, predicate) -> None:
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        reader.pump()
        time.sleep(0.001)
    assert predicate()


def test_subtitle_fetch_runs_off_the_event_thread_and_publishes_directly(monkeypatch, make_session):
    ipc = RuntimeFakeIPC()
    gateway = session_gateway(ipc)
    reader = make_session(ipc)
    event_thread = threading.get_ident()
    worker_thread = None
    messages = []

    def fetch():
        nonlocal worker_thread
        worker_thread = threading.get_ident()
        return None, "provider: no match"

    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda message, level: messages.append((message, level))
    )
    try:
        reader.graph.subtitle_acquisition.start(fetch)
        _drain_until(reader, lambda: bool(messages))
        assert worker_thread is not None and worker_thread != event_thread
        assert messages == [("provider: no match", "warn")]
    finally:
        reader.close()
        gateway.close()


def test_subtitle_fetch_lane_rejects_work_beyond_its_bound():
    ipc = RuntimeFakeIPC()
    gateway = bare_gateway(ipc)
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
    submitter = subtitle_modes.configure_runtime_job(ipc)
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
        ipc.close_runtime_job_lane("subtitle-fetch", timeout=1)
        gateway.close()


def test_newer_explicit_subtitle_choice_supersedes_older_completion(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)
    older = tmp_path / "older.ass"
    newer = tmp_path / "newer.ass"
    older.write_text("older", encoding="utf-8")
    newer.write_text("newer", encoding="utf-8")

    reader.graph.subtitle_acquisition.start(
        lambda: (older, "older"),
        force_select=True,
        name="picker-download",
    )
    reader.graph.subtitle_acquisition.start(
        lambda: (newer, "newer"),
        force_select=True,
        name="picker-download",
    )
    jobs.finish(1)
    jobs.finish(0)

    added = [command[1] for command in ipc.commands if command[0] == "sub-add"]
    assert added == [str(newer)]


def test_closing_subtitle_lane_quarantines_blocked_fetch(monkeypatch, make_session):
    ipc = RuntimeFakeIPC()
    gateway = session_gateway(ipc)
    reader = make_session(ipc)
    started = threading.Event()
    release = threading.Event()
    messages = []

    def fetch():
        started.set()
        assert release.wait(1)
        return None, "late"

    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda message, level: messages.append((message, level))
    )
    try:
        reader.graph.subtitle_acquisition.start(fetch)
        assert started.wait(1)
        reader.request_stop()
        ipc.close_runtime_job_lane("subtitle-fetch", timeout=0)
        release.set()
        for _ in range(10):
            reader.pump()
        assert messages == []
    finally:
        release.set()
        reader.close()
        gateway.close()


#: Every lane the LANES phase closes, in the order `WORKER_LANE_PARTICIPANTS` declares.
_LANES_BEFORE_ARTIFACTS = [
    "subtitle-fetch",
    "subtitle-picker",
    "subtitle-geometry",
    "cue-annotation",
    "tooltip-render-ahead",
    "tooltip-engaged",
    "speculative-prefetch",
    "mask-atlas-startup",
    "capabilities",
    "interaction-metadata",
    "mined-seed",
    "episode-analysis",
    "render-pool",
]


def test_reader_close_quarantines_subtitle_lanes_before_artifact_removal(monkeypatch, make_session):
    ipc = FakeIPC()
    reader = make_session(ipc)
    order = []

    def close_lane(name, _timeout):
        assert reader.graph.lifecycle.state is LiveState.CLOSING
        order.append(name)
        return True

    def remove_artifacts(_path, *, ignore_errors):
        assert ignore_errors
        assert order == _LANES_BEFORE_ARTIFACTS
        order.append("artifacts")

    def close_render_pool(*, wait):
        assert wait is False
        order.append("render-pool")

    ipc.close_runtime_job_lane = close_lane
    monkeypatch.setattr("saitenka.parallel.shutdown_shared_executor", close_render_pool)
    monkeypatch.setattr(shutil, "rmtree", remove_artifacts)

    reader.close()

    assert order == [*_LANES_BEFORE_ARTIFACTS, "artifacts"]


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


def test_configure_releases_preselected_secondary_for_native_track_cycling(make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    startup = subtitle_modes.select_initial(ipc)
    ipc.props["secondary-sid"] = 1
    ipc.commands.clear()

    make_session(ipc).graph.cue.configure_subtitle_mode(startup)

    assert ("set_property", "secondary-sid", "no") in ipc.commands


def test_language_switch_changes_only_existing_target_and_rebuilds_index(monkeypatch, make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    hold_translation(reader)
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, *_args: messages.append(text)
    )
    rebuilt = []
    monkeypatch.setattr(
        reader.graph.track_commands, "rebuild_index", lambda: rebuilt.append("rebuilt")
    )
    ipc.commands.clear()

    reader.command(app_bindings.SUBTITLE_LANGUAGE_MSG)

    assert reader.graph.track_commands.current().language == "en"
    assert reader.graph.translation.state.held
    assert ("set_property", "sid", 1) in ipc.commands
    assert ("set_property", "secondary-sid", 2) in ipc.commands
    assert rebuilt == ["rebuilt"]
    assert not [c for c in ipc.commands if c[0] in {"seek", "sub-seek"}]
    assert not [c for c in ipc.commands if c[:2] == ("set_property", "pause")]
    assert messages == ["subtitles: English (1/2)"]


def test_language_switch_releases_secondary_before_selecting_its_track(monkeypatch, make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader.command(app_bindings.TRANS_MSG)
    reader.graph.track_commands.declare(SubtitleSecondaryLeased(1))
    ipc.props["secondary-sid"] = 1
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )
    ipc.commands.clear()

    reader.command(app_bindings.SUBTITLE_LANGUAGE_MSG)

    secondary_off = ipc.commands.index(("set_property", "secondary-sid", "no"))
    primary_english = ipc.commands.index(("set_property", "sid", 1))
    secondary_japanese = ipc.commands.index(("set_property", "secondary-sid", 2))
    assert secondary_off < primary_english < secondary_japanese


def test_secondary_sid_event_does_not_change_primary_language(make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader.graph.track_commands.declare(SubtitleLanguageChanged("en"))
    reader.graph.track_commands.declare(SubtitleSecondaryLeased(2))

    subtitle_modes.on_primary_changed(reader.graph.track_commands.ports(), 2)

    assert reader.graph.track_commands.current().language == "en"


def test_primary_track_event_shows_language_and_counter(monkeypatch, make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    monkeypatch.setattr(reader.graph.notifications, "show", lambda text: messages.append(text))

    reader.graph.playback.observe_event({"name": "sid", "data": 1})

    assert messages == ["subtitles: English (1/2)"]


def test_hidden_translation_does_not_reserve_english_secondary(make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    ipc.commands.clear()

    subtitle_modes.release_secondary(reader.graph.track_commands.ports())

    assert not [
        command for command in ipc.commands if command[:2] == ("set_property", "secondary-sid")
    ]


def test_translation_leases_english_only_while_visible(make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    ipc.commands.clear()

    reader.command(app_bindings.TRANS_MSG)
    reader.command(app_bindings.TRANS_MSG)

    secondary = [
        command[2] for command in ipc.commands if command[:2] == ("set_property", "secondary-sid")
    ]
    assert secondary == [1, "no"]


def test_primary_sid_event_updates_rendering_language(make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    hold_translation(reader)
    reader.graph.playback.install_seed({"sid": 2})
    ipc.props["sid"] = 1
    ipc.commands.clear()

    reader.graph.playback.observe_event({"name": "sid", "data": 1})

    assert reader.graph.track_commands.current().language == "en"
    assert ("set_property", "secondary-sid", 2) in ipc.commands


def test_unavailable_language_keeps_current_mode(monkeypatch, make_session):
    ipc = FakeIPC([JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, kind="ok": messages.append((text, kind))
    )
    ipc.commands.clear()

    reader.command(app_bindings.SUBTITLE_LANGUAGE_MSG)

    assert reader.graph.track_commands.current().language == "jp"
    assert not [c for c in ipc.commands if c[:2] == ("set_property", "sid")]
    assert messages == [("EN subtitles unavailable", "warn")]


def test_unavailable_configured_translation_language_names_it(monkeypatch, make_session):
    ipc = FakeIPC([JP.copy(), EN.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(
        subtitle_modes.select_initial(ipc, second_slang="de"),
        second_slang="de",
    )
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, kind="ok": messages.append((text, kind))
    )

    reader.command(app_bindings.SUBTITLE_LANGUAGE_MSG)

    assert reader.graph.track_commands.current().language == "jp"
    assert messages == [("DE subtitles unavailable", "warn")]


def test_english_primary_is_plain_and_noninteractive(monkeypatch, make_session):
    ipc = FakeIPC([EN.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(
        reader.graph.profile.profile.tokenizer,
        "tokenize",
        lambda _text: (_ for _ in ()).throw(AssertionError("English must not be tokenized")),
    )
    provider = RecordingRasterProvider()
    reader.graph.subtitle_presentation.renderer = SubtitleRenderer(provider)

    reader.graph.cue.set_subtitle("Readable English")

    assert reader.graph.playback.cue.text == "Readable English"
    assert (
        reader.graph.subtitle_presentation.cue.current.lines == []
        and reader.graph.subtitle_presentation.cue.current.tokens == []
        and reader.graph.subtitle_presentation.cue.current.boxes == []
    )
    assert provider.styles == ["plain"]


def test_startup_japanese_arrival_replaces_untouched_english_fallback(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    messages = []
    rebuilt = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, *_args: messages.append(text)
    )
    monkeypatch.setattr(
        reader.graph.track_commands, "rebuild_index", lambda: rebuilt.append("rebuilt")
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")
    ipc.commands.clear()

    reader.graph.subtitle_acquisition.fetch_background(lambda: (path, "jimaku: ready"))
    jobs.finish()

    assert ("sub-add", str(path), "auto", "", "jpn") in ipc.commands
    assert (
        reader.graph.track_commands.current().jp_sid == 9
        and reader.graph.track_commands.current().language == "jp"
    )
    assert ("set_property", "sid", 9) in ipc.commands
    assert rebuilt == ["rebuilt"]
    assert messages == ["Japanese subtitles ready"]


def test_startup_japanese_arrival_preserves_track_changed_during_fetch(tmp_path, monkeypatch):
    other = {"id": 7, "type": "sub", "lang": "kor"}
    ipc = FakeIPC([EN.copy(), other])
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")

    reader.graph.subtitle_acquisition.fetch_background(lambda: (path, "jimaku: ready"))
    ipc.command("set_property", "sid", 7)
    jobs.finish()

    assert ipc.props["sid"] == 7
    assert reader.graph.track_commands.current().language == "en"


def test_startup_japanese_arrival_is_selected_after_missing_both(tmp_path, monkeypatch):
    ipc = FakeIPC()
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")

    reader.graph.subtitle_acquisition.fetch_background(lambda: (path, "jimaku: ready"))
    jobs.finish()

    assert reader.graph.track_commands.current().language == "jp"
    assert ("set_property", "sid", 9) in ipc.commands
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_background_japanese_selection_zeroes_stale_sub_delay(tmp_path, monkeypatch):
    """Auto-selecting our fetched track re-establishes authoritative timing (the file's own cue times),
    so a sub-delay mpv restored from watch-later must be zeroed — else it silently rides on top."""
    ipc = FakeIPC()
    ipc.props["sub-delay"] = 10.0  # stale offset a previous run/track left in mpv
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_a: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")

    reader.graph.subtitle_acquisition.fetch_background(lambda: (path, "jimaku: ready"))
    jobs.finish()

    assert reader.graph.track_commands.current().language == "jp"
    assert ("set_property", "sub-delay", 0.0) in ipc.commands


def test_replace_track_zeroes_stale_sub_delay(tmp_path, monkeypatch, make_session):
    """The resync/retry swap re-times the FILE, so it owns timing — drop any persisted mpv sub-delay
    (the reported bug: a resynced cue looked wrong until sub-delay was hand-zeroed)."""
    ipc = FakeIPC([EN.copy(), JP.copy()])
    ipc.props["sub-delay"] = -7.5
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))  # jp mode
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_a: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )
    path = Path(tmp_path / "episode.synced.srt")
    path.write_text("Japanese", encoding="utf-8")

    subtitle_modes._replace_japanese_track(reader.graph.track_commands.ports(), path, "resynced")

    assert ("set_property", "sub-delay", 0.0) in ipc.commands


def test_runtime_retry_uses_current_media_and_coalesces_active_request(monkeypatch):
    ipc = FakeIPC([EN.copy()])
    ipc.props["path"] = "/videos/Show - 02.mkv"
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    paths = []
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications,
        "show",
        lambda text, kind="ok": messages.append((text, kind)),
    )

    def factory(video_path):
        paths.append(video_path)

        def fetch():
            return None, "jimaku: no match; tsukihime: ambiguous"

        return fetch

    reader.graph.subtitle_acquisition.configure_retry(factory)
    reader.command(app_bindings.SUBTITLE_RETRY_MSG)
    reader.command(app_bindings.SUBTITLE_RETRY_MSG)

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
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications,
        "show",
        lambda text, kind="ok": messages.append((text, kind)),
    )

    reader.command(
        app_bindings.SUBTITLE_RETRY_MSG
    )  # no media at all → media error takes precedence
    ipc.props["path"] = "/videos/Show - 01.mkv"  # media present, but no external subs + no provider
    reader.command(app_bindings.SUBTITLE_RETRY_MSG)

    assert messages == [
        ("No media loaded for subtitle search", "warn"),
        ("No Japanese subtitle providers enabled", "warn"),
    ]
    assert jobs.accepted == []


def test_runtime_retry_success_retains_english_until_explicit_switch(tmp_path, monkeypatch):
    ipc = FakeIPC([EN.copy()])
    ipc.props["path"] = "/videos/Show - 03.mkv"
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    path = tmp_path / "episode.ja.srt"
    path.write_text("Japanese")
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, *_args: messages.append(text)
    )
    reader.graph.subtitle_acquisition.configure_retry(
        lambda _video: lambda: (path, "tsukihime: added")
    )
    ipc.commands.clear()

    reader.command(app_bindings.SUBTITLE_RETRY_MSG)
    jobs.finish()

    assert reader.graph.track_commands.current().language == "en"
    assert ("sub-add", str(path), "auto", "", "jpn") in ipc.commands
    assert ("set_property", "sid", 1) in ipc.commands
    assert ("set_property", "sid", 9) not in ipc.commands
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)
    assert messages == [
        "Searching Japanese subtitle providers…",
        "Japanese subtitles ready — Alt+t to switch",
    ]


def test_picker_force_select_activates_japanese_from_english(tmp_path, monkeypatch, make_session):
    """An explicit picker choice (``force_select``) selects the chosen source NOW even while English is
    on screen — the keep-current background contract is for unattended fetches, not a deliberate pick
    (regression: from English the pick fell through to the background add and left English up)."""
    ipc = FakeIPC([EN.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(
        subtitle_modes.select_initial(ipc)
    )  # English fallback active
    assert reader.graph.track_commands.current().language == "en"
    messages: list[str] = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, *_args: messages.append(text)
    )
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )
    path = tmp_path / "episode.ja.srt"
    path.write_text("Japanese", encoding="utf-8")
    ipc.commands.clear()

    subtitle_modes.apply_fetch_result(
        reader.graph.track_commands.ports(),
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
        reader.graph.track_commands.current().language == "jp"
    )  # took over from English, unlike the background contract
    assert ("sub-add", str(path), "select", "", "jpn") in ipc.commands  # selected now, not "auto"
    assert reader.graph.track_commands.current().jp_sid == 9
    assert messages == ["Japanese subtitles selected"]


def test_runtime_retry_keeps_current_subs_when_window_retiming_fails(tmp_path, monkeypatch):
    # A failed local alignment must keep the selected file and fail visibly. Whole-file fallback can
    # mis-correlate a different region and, for ASS input, used to overwrite it with SRT bytes.
    from saitenka.app import resync as resync_mod

    current = tmp_path / "ep3.ja.ass"
    original = "[Events]\nDialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,猫\n"
    current.write_text(original, encoding="utf-8")
    jp_external = {
        "id": 2,
        "type": "sub",
        "lang": "jpn",
        "external": True,
        "external-filename": str(current),
    }
    ipc = FakeIPC([EN.copy(), jp_external])
    ipc.props["path"] = "/videos/Show - 03.mkv"
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert reader.graph.track_commands.current().language == "jp"
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, *_args: messages.append(text)
    )
    reader.graph.subtitle_acquisition.configure_retry(  # the provider factory must NOT be called
        lambda _v: (_ for _ in ()).throw(AssertionError("queried providers on re-sync"))
    )
    monkeypatch.setattr(resync_mod, "resync_window", lambda *_a, **_k: None)
    ipc.commands.clear()

    reader.command(app_bindings.SUBTITLE_RETRY_MSG)
    jobs.finish()

    assert current.read_text(encoding="utf-8") == original
    assert ("sub-remove", 2) not in ipc.commands
    assert not any(command[0] == "sub-add" for command in ipc.commands)
    assert reader.graph.track_commands.current().jp_sid == 2
    assert messages == [
        "Re-timing subtitles from here…",
        "Subtitle retiming failed — current subtitles kept",
    ]


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


def test_discovery_uses_the_configured_second_language():
    ipc = FakeIPC(
        [
            {"id": 6, "type": "sub", "lang": "fra"},
            {"id": 7, "type": "sub", "lang": "eng"},
            {"id": 8, "type": "sub", "lang": "deu"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "de") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=8
    )


def test_discovery_falls_back_from_a_region_tag_to_the_base_track_language():
    ipc = FakeIPC(
        [
            {"id": 6, "type": "sub", "lang": "fra"},
            {"id": 8, "type": "sub", "lang": "deu"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "de-CH") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=8
    )


def test_discovery_prefers_an_exact_region_before_its_base_language():
    ipc = FakeIPC(
        [
            {"id": 1, "type": "sub", "lang": "pt-PT"},
            {"id": 2, "type": "sub", "lang": "pt-BR"},
            {"id": 6, "type": "sub", "lang": "fra"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "pt-BR") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=2
    )


def test_discovery_prefers_a_regional_iso_alias_before_the_alias_base():
    ipc = FakeIPC(
        [
            {"id": 1, "type": "sub", "lang": "por-PT"},
            {"id": 2, "type": "sub", "lang": "por-BR"},
            {"id": 6, "type": "sub", "lang": "fra"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "pt-BR") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=2
    )


def test_discovery_prefers_the_exact_english_region_through_its_iso_alias():
    ipc = FakeIPC(
        [
            {"id": 1, "type": "sub", "lang": "eng-US"},
            {"id": 2, "type": "sub", "lang": "eng-GB"},
            {"id": 6, "type": "sub", "lang": "fra"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "en-GB") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=2
    )


def test_discovery_matches_iso_639_two_and_three_letter_aliases():
    ipc = FakeIPC(
        [
            {"id": 6, "type": "sub", "lang": "fra"},
            {"id": 8, "type": "sub", "lang": "spa"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "es-MX") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=8
    )


def test_discovery_matches_a_regional_three_letter_preference_to_a_two_letter_tag():
    ipc = FakeIPC(
        [
            {"id": 6, "type": "sub", "lang": "fra"},
            {"id": 8, "type": "sub", "lang": "de-CH"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "deu-CH") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=8
    )


def test_discovery_checks_configured_tracks_before_untagged_fallback():
    ipc = FakeIPC(
        [
            {"id": 6, "type": "sub", "lang": "fra"},
            {"id": 9, "type": "sub"},
            {"id": 8, "type": "sub", "lang": "deu"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "de") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=8
    )


def test_discovery_does_not_substitute_a_tagged_unconfigured_translation_language():
    ipc = FakeIPC(
        [
            {"id": 6, "type": "sub", "lang": "fra"},
            {"id": 7, "type": "sub", "lang": "eng"},
        ]
    )

    assert subtitle_modes.discover_tracks(ipc, "fr", "de") == subtitle_modes.SubtitleTracks(
        jp_sid=6, en_sid=None
    )


def _select(ipc, sid):
    ipc.props["sid"] = sid
    for track in ipc.tracks:
        track["selected"] = track.get("id") == sid
        if track["selected"]:
            track["main-selection"] = 0


def test_dropped_untagged_sub_is_adopted_as_japanese_and_indexed(tmp_path, make_session):
    # Drag-'n'-drop: mpv adds an UNTAGGED external sub ("unknown language") and makes it primary after
    # startup already fell back to English. It must be adopted as the Japanese primary so cues color
    # (not the plain English path), and indexed from the file on disk.
    ipc = FakeIPC([EN.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert (
        reader.graph.track_commands.current().language == SECOND_LANG
    )  # only English present at attach
    srt = tmp_path / "dropped.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n岩を砂へ\n", encoding="utf-8")
    ipc.tracks.append(
        {"id": 2, "type": "sub", "lang": None, "external": True, "external-filename": str(srt)}
    )
    _select(ipc, 2)

    subtitle_modes.on_primary_changed(reader.graph.track_commands.ports(), 2)

    assert reader.graph.track_commands.current().language == MAIN_LANG
    assert reader.graph.track_commands.current().jp_sid == 2
    assert (
        reader.graph.track_commands.navigation.current.sub_index is not None
    )  # indexed from the dropped file


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


def test_dropped_untagged_english_sub_stays_plain_not_japanese(tmp_path, make_session):
    # Content-based ID: an UNTAGGED English sub (Latin script) the user drops must NOT be miscolored as
    # Japanese — it stays the plain secondary, unlike an untagged Japanese sub.
    ipc = FakeIPC([JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert reader.graph.track_commands.current().language == MAIN_LANG
    srt = tmp_path / "dropped.en.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTurn rock to sand.\n", encoding="utf-8")
    ipc.tracks.append(
        {"id": 3, "type": "sub", "lang": None, "external": True, "external-filename": str(srt)}
    )
    _select(ipc, 3)

    subtitle_modes.on_primary_changed(reader.graph.track_commands.ports(), 3)

    assert reader.graph.track_commands.current().language == SECOND_LANG
    assert reader.graph.track_commands.current().en_sid == 3
    assert (
        reader.graph.track_commands.current().jp_sid == 2
    )  # the Japanese track is not overwritten


def test_manual_switch_to_untagged_track_is_adopted_as_japanese(make_session):
    # The same rule for a manual native track cycle (mpv's `j` key) to an untagged embedded track:
    # no file to index, but the render language flips to Japanese so the cue colors.
    ipc = FakeIPC([EN.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    ipc.tracks.append({"id": 3, "type": "sub", "lang": ""})  # empty tag == untagged
    _select(ipc, 3)

    subtitle_modes.on_primary_changed(reader.graph.track_commands.ports(), 3)

    assert reader.graph.track_commands.current().language == MAIN_LANG
    assert reader.graph.track_commands.current().jp_sid == 3


def test_newly_primary_english_tagged_track_is_secondary_not_japanese(make_session):
    # The guard against the false wildcard: a real English tag stays the known-language secondary and
    # is NOT adopted as Japanese, even though lang_matches(None, EN_LANGS) would wildcard-match.
    ipc = FakeIPC([JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    assert reader.graph.track_commands.current().language == MAIN_LANG
    ipc.tracks.append({"id": 5, "type": "sub", "lang": "eng"})
    _select(ipc, 5)

    subtitle_modes.on_primary_changed(reader.graph.track_commands.ports(), 5)

    assert reader.graph.track_commands.current().language == SECOND_LANG
    assert reader.graph.track_commands.current().en_sid == 5
    assert reader.graph.track_commands.current().jp_sid == 2  # the original JP track is untouched


def test_subs_turned_off_adopt_no_track(make_session):
    ipc = FakeIPC([EN.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    before = reader.graph.track_commands.current().language

    subtitle_modes.on_primary_changed(reader.graph.track_commands.ports(), None)

    assert reader.graph.track_commands.current().language == before
    assert reader.graph.track_commands.current().jp_sid is None


def test_force_current_as_japanese_overrides_classification(tmp_path, monkeypatch, make_session):
    # The keybind override: force the CURRENT track to Japanese even when it is tagged English (so it
    # would auto-classify as the secondary), letting the user correct a wrong guess from within mpv.
    ipc = FakeIPC([EN.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    srt = tmp_path / "manual.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n岩を砂へ\n", encoding="utf-8")
    ipc.tracks.append(
        {"id": 2, "type": "sub", "lang": "eng", "external": True, "external-filename": str(srt)}
    )
    _select(ipc, 2)
    messages = []
    monkeypatch.setattr(reader.graph.notifications, "show", lambda text, *_a: messages.append(text))
    monkeypatch.setattr(reader.graph.cue, "set_subtitle", lambda *_a, **_k: None)

    reader.command(app_bindings.SUBTITLE_MARK_JP_MSG)

    assert reader.graph.track_commands.current().language == MAIN_LANG
    assert reader.graph.track_commands.current().jp_sid == 2
    assert reader.graph.track_commands.navigation.current.sub_index is not None
    assert messages == ["Marked current subtitles as Japanese"]


def test_force_current_as_japanese_with_no_track_warns(monkeypatch, make_session):
    ipc = FakeIPC()
    reader = make_session(ipc)
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, kind="ok": messages.append((text, kind))
    )

    reader.command(app_bindings.SUBTITLE_MARK_JP_MSG)

    assert messages == [("No subtitle track to mark", "warn")]


def test_announce_names_a_japanese_track(monkeypatch, make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    seen = []
    monkeypatch.setattr(reader.graph.notifications, "show", lambda text, *_args: seen.append(text))

    subtitle_modes.announce_track(reader.graph.track_commands.ports(), 2)

    assert seen == ["subtitles: Japanese (2/2)"]


def test_announce_passes_through_an_unknown_language(monkeypatch, make_session):
    ipc = FakeIPC([{"id": 3, "type": "sub", "lang": "ger"}])
    reader = make_session(ipc)
    seen = []
    monkeypatch.setattr(reader.graph.notifications, "show", lambda text, *_args: seen.append(text))

    subtitle_modes.announce_track(reader.graph.track_commands.ports(), 3)

    assert seen == ["subtitles: ger (1/1)"]


def _one_cue_index() -> CueIndex:
    return CueIndex(parse_srt("1\n00:00:01,000 --> 00:00:02,000\n本\n"))


def test_track_switch_retains_cues_when_the_new_track_cannot_resolve(
    tmp_path, monkeypatch, make_session
):
    """A replace whose rebuild can't resolve the just-added track yet must RETAIN the prior cues,
    not blank them — the transient track-switch window must never drop a good index."""
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    old = _one_cue_index()
    reader.graph.track_commands.navigation.current.sub_index = old
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_a: None)
    monkeypatch.setattr(  # the new track isn't resolvable at this instant
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )
    path = tmp_path / "ep.ja.srt"
    path.write_text("Japanese", encoding="utf-8")

    subtitle_modes._replace_japanese_track(reader.graph.track_commands.ports(), path, "resynced")

    assert (
        reader.graph.track_commands.navigation.current.sub_index is old
    )  # cues retained across the unresolved switch


def test_load_sub_index_retains_prior_cues_on_parse_failure(tmp_path, make_session):
    reader = make_session(FakeIPC())
    old = _one_cue_index()
    reader.graph.track_commands.navigation.current.sub_index = old

    reader.graph.subtitle_navigation.load_index(
        tmp_path / "missing.srt"
    )  # unreadable → load_index returns None

    assert (
        reader.graph.track_commands.navigation.current.sub_index is old
    )  # a failed parse never blanks a good index


def test_resync_replace_does_not_clobber_the_primary_when_english_is_active(
    tmp_path, monkeypatch, make_session
):
    """A retime (`replace`) only swaps the JP-primary slot when JP is actually primary; from English
    it routes to the non-disruptive background add, so it can never overwrite the wrong slot."""
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader.graph.track_commands.declare(SubtitleLanguageChanged(SECOND_LANG))  # English on screen
    replaced: list = []
    monkeypatch.setattr(
        subtitle_modes, "_replace_japanese_track", lambda *a, **_k: replaced.append(a)
    )
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_a: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )
    path = tmp_path / "ep.ja.srt"
    path.write_text("Japanese", encoding="utf-8")

    subtitle_modes.apply_fetch_result(
        reader.graph.track_commands.ports(),
        subtitle_modes.SubtitleFetchResult(
            path=path, status="resynced", select_if_unchanged=False, initial_sid=1, replace=True
        ),
    )

    assert replaced == []  # never clobbered the primary slot from English
    assert reader.graph.track_commands.current().language == SECOND_LANG


def test_toggle_from_english_returns_to_japanese(monkeypatch, make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))  # JP active
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)
    monkeypatch.setattr(
        "saitenka.app.embedded_subs.build_sub_index_for_current_track", lambda *_a: None
    )

    reader.command(app_bindings.SUBTITLE_LANGUAGE_MSG)  # JP → EN
    ipc.commands.clear()
    reader.command(
        app_bindings.SUBTITLE_LANGUAGE_MSG
    )  # EN → JP exercises the return-to-Japanese branch

    assert reader.graph.track_commands.current().language == "jp"
    assert ("set_property", "sid", 2) in ipc.commands


def test_language_id_samples_the_index_when_one_is_loaded():
    """Content-based classification for an untagged track. The parsed index is preferred over mpv's
    on-screen cue because one visible line is a coin flip and twenty are not."""
    index = CueIndex(
        parse_srt(
            "1\n00:00:01,000 --> 00:00:02,000\n日本語\n\n2\n00:00:03,000 --> 00:00:04,000\n字幕\n"
        )
    )

    assert subtitle_modes._sample_cue_text(index, "on screen") == "日本語 字幕"


def test_language_id_falls_back_to_the_on_screen_cue():
    assert subtitle_modes._sample_cue_text(None, "on screen") == "on screen"
    assert subtitle_modes._sample_cue_text(CueIndex([]), "on screen") == "on screen"


def test_language_id_survives_a_track_with_nothing_to_sample():
    """mpv reports `sub-text` as None before a track resolves, and `looks_japanese("")` must be the
    answer rather than an AttributeError on the startup path."""
    assert subtitle_modes._sample_cue_text(None, "") == ""


def test_language_id_bounds_how_many_cues_it_reads():
    """A feature-length index is thousands of cues; classifying needs a handful."""
    srt = "".join(
        f"{n}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n日{n}\n\n" for n in range(1, 31)
    )

    sample = subtitle_modes._sample_cue_text(CueIndex(parse_srt(srt)), "", limit=5)

    assert sample.split() == ["日1", "日2", "日3", "日4", "日5"]
