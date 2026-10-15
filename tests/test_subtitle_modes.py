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
from util import await_ready, session_gateway

from saitenka.app import bindings as app_bindings
from saitenka.app import subtitle_modes, subtitle_selection
from saitenka.app.features.translation import TranslationInputs
from saitenka.app.session.lifecycle import LiveState
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


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
            # mpv numbers each new track past every earlier one and never reuses an id.
            added = self.next_id = max(getattr(self, "next_id", 8) + 1, 9)
            self.tracks.append(
                {
                    "id": added,
                    "type": "sub",
                    "lang": args[4],
                    "external": True,
                    "external-filename": args[1],
                }
            )
            if len(args) > 2 and args[2] == "select":  # mpv's "select" flag activates the new track
                self.props["sid"] = added
                for track in self.tracks:
                    track["selected"] = track["id"] == added
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


#: Every worker lane must be quarantined before its artifacts disappear.
_LANES_BEFORE_ARTIFACTS = [
    "card-preview",
    "subtitle-fetch",
    "subtitle-picker",
    "colored-track",
    "track-preparation",
    "subtitle-geometry",
    "cue-annotation",
    "tooltip-render-ahead",
    "tooltip-engaged",
    "speculative-prefetch",
    "mask-atlas-startup",
    "capabilities",
    "interaction-metadata",
    "mining-operation",
    "mined-seed",
    "episode-analysis",
    "render-pool",
]


@pytest.mark.integration
@pytest.mark.timeout(5)
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
        assert sorted(order) == sorted(_LANES_BEFORE_ARTIFACTS)
        order.append("artifacts")

    def close_render_pool(*, wait):
        assert wait is False
        order.append("render-pool")

    ipc.close_runtime_job_lane = close_lane
    monkeypatch.setattr("saitenka.parallel.shutdown_shared_executor", close_render_pool)
    monkeypatch.setattr(shutil, "rmtree", remove_artifacts)

    ledger = reader.close()
    assert ledger.report() is None
    assert order[-1] == "artifacts"


def test_startup_prefers_japanese_and_remembers_both_tracks():
    ipc = FakeIPC([EN.copy(), JP.copy()])
    startup = subtitle_modes.select_initial(ipc)
    assert startup == subtitle_modes.SubtitleStartup(
        subtitle_modes.SubtitleTracks(jp_sid=2, en_sid=1), "jp"
    )
    assert ("set_property", "sid", 2) in ipc.commands


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


def test_provider_arrival_uses_the_configured_target_language(tmp_path, monkeypatch):
    ipc = FakeIPC()
    reader, jobs = reader_with_fetch_jobs(ipc, monkeypatch)
    reader.graph.cue.configure_subtitle_mode(
        subtitle_modes.select_initial(ipc, "fr", "de"),
        slang="fr",
        second_slang="de",
    )
    messages = []
    monkeypatch.setattr(
        reader.graph.notifications, "show", lambda text, *_args: messages.append(text)
    )
    path = tmp_path / "episode.fr.srt"
    path.write_text("Français", encoding="utf-8")
    reader.graph.subtitle_acquisition.configure_retry(None, target_language="fr")
    ipc.commands.clear()

    reader.graph.subtitle_acquisition.fetch_background(lambda: (path, "universal: ready"))
    jobs.finish()

    tracks = reader.graph.track_commands.current()
    assert (tracks.slang, tracks.jp_sid, tracks.language) == ("fr", 9, MAIN_LANG)
    assert ("sub-add", str(path), "auto", "", "fr") in ipc.commands
    assert messages == ["fr subtitles ready"]


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
    path = Path(tmp_path / "episode.ja.srt")
    path.write_text("Japanese", encoding="utf-8")

    reader.graph.subtitle_acquisition.fetch_background(lambda: (path, "jimaku: ready"))
    jobs.finish()

    assert reader.graph.track_commands.current().language == "jp"
    assert ("set_property", "sid", 9) in ipc.commands
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


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


@pytest.mark.parametrize(
    ("lang", "wants", "expected"),
    [
        ("jpn", ["jp"], True),  # tag longer than want — low.startswith(want)
        ("jp", ["jpn"], True),  # want longer than tag — want.startswith(low)
        ("JA", ["ja"], True),  # case-insensitive
        ("Japanese", ["ja"], True),  # full language name
        ("ger", ["ja", "jpn", "jp"], False),  # unrelated tag never matches
        ("frp", ["fr"], False),  # a longer ISO code is not a regional fr tag
        (None, ["ja"], True),  # untagged track is a wildcard — load-bearing for foreign-only files
        ("", ["ja"], True),  # empty tag likewise matches
        ("ja", [""], False),  # empty want is ignored, not a wildcard
        ("ja", [], False),  # no wants → no match
    ],
)
def test_lang_matches_alias_and_wildcard_edges(lang, wants, expected):
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
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nTurn rock to sand.\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n岩を砂へ\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\n砂を岩へ\n",
        encoding="utf-8",
    )
    ipc.props["sub-text"] = "English on screen"
    ipc.tracks.append(
        {"id": 2, "type": "sub", "lang": None, "external": True, "external-filename": str(srt)}
    )
    _select(ipc, 2)

    reader.graph.playback.install_seed(dict(ipc.props))
    subtitle_modes.on_primary_changed(reader.graph.track_commands.ports(), 2)
    await_ready(
        lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
        "track was not indexed",
        pump=reader.pump,
    )

    assert reader.graph.track_commands.current().language == MAIN_LANG
    assert reader.graph.track_commands.current().jp_sid == 2
    assert (
        reader.graph.track_commands.navigation.current.sub_index is not None
    )  # indexed from the dropped file


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_track_preparation_reads_the_document_off_the_owner_thread(
    tmp_path, monkeypatch, make_session
):
    owner = threading.get_ident()
    path = tmp_path / "track.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:02,000\n本を読む\n", encoding="utf-8")
    ipc = FakeIPC(
        [{"id": 2, "type": "sub", "lang": "jpn", "external": True, "external-filename": str(path)}]
    )
    _select(ipc, 2)
    reader = make_session(ipc)
    reader.graph.playback.install_seed(dict(ipc.props))
    original = Path.open

    def read(self, *args, **kwargs):
        if self == path:
            assert threading.get_ident() != owner, "track document read on input thread"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read)

    reader.graph.cue.rebuild_sub_index()
    ipc.set_prop("pause", value=True)
    reader.pump()
    assert reader.graph.playback.value("pause") is True
    await_ready(
        lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
        "track was not indexed",
        pump=reader.pump,
    )
    assert reader.graph.track_commands.navigation.current.sub_index.cues[0].text == "本を読む"


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

    reader.graph.playback.install_seed(dict(ipc.props))
    reader.command(app_bindings.SUBTITLE_MARK_JP_MSG)
    await_ready(
        lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
        "track was not indexed",
        pump=reader.pump,
    )

    assert reader.graph.track_commands.current().language == MAIN_LANG
    assert reader.graph.track_commands.current().jp_sid == 2
    assert reader.graph.track_commands.navigation.current.sub_index is not None
    assert messages == ["Marked current subtitles as Japanese"]


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
    path = tmp_path / "ep.ja.srt"
    path.write_text("Japanese", encoding="utf-8")

    subtitle_modes._replace_target_track(reader.graph.track_commands.ports(), path, "resynced")

    assert (
        reader.graph.track_commands.navigation.current.sub_index is old
    )  # cues retained across the unresolved switch


def test_toggle_from_english_returns_to_japanese(monkeypatch, make_session):
    ipc = FakeIPC([EN.copy(), JP.copy()])
    reader = make_session(ipc)
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))  # JP active
    monkeypatch.setattr(reader.graph.notifications, "show", lambda *_args: None)

    reader.command(app_bindings.SUBTITLE_LANGUAGE_MSG)  # JP → EN
    ipc.commands.clear()
    reader.command(
        app_bindings.SUBTITLE_LANGUAGE_MSG
    )  # EN → JP exercises the return-to-Japanese branch

    assert reader.graph.track_commands.current().language == "jp"
    assert ("set_property", "sid", 2) in ipc.commands


def test_language_id_falls_back_to_the_on_screen_cue():
    assert subtitle_modes._sample_cue_text(None, "on screen") == "on screen"
    assert subtitle_modes._sample_cue_text(CueIndex([]), "on screen") == "on screen"


def test_language_id_bounds_how_many_cues_it_reads():
    """A feature-length index is thousands of cues; classifying needs a handful."""
    srt = "".join(
        f"{n}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n日{n}\n\n" for n in range(1, 31)
    )

    sample = subtitle_modes._sample_cue_text(CueIndex(parse_srt(srt)), "", limit=5)

    assert sample.split() == ["日1", "日2", "日3", "日4", "日5"]
