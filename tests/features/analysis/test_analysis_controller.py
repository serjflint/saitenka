"""Episode analysis runs off-thread and its overlay never mutates playback."""

import threading

import pytest
from session_builder import build_session
from util import FakeIPC, await_ready, drain_for, runtime_gateway

from saitenka.app.bindings import ANALYSIS_MSG
from saitenka.app.features.analysis import analysis_controller
from saitenka.app.features.analysis.episode_analysis import cue_result
from saitenka.app.features.profiles.dependencies import DependencyBundle
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.scoring import Scorer
from saitenka.app.session.controller import SessionController
from saitenka.app.session.factory import SessionServices
from saitenka.app.wordlists import KnownWords
from saitenka.render.analysis import render_analysis
from saitenka.runtime.events import (
    SubtitleLanguageChanged,
    SubtitleStartupConfigured,
    SubtitleTracksDiscovered,
)
from saitenka.subtitles import Cue, CueIndex


@pytest.fixture
def reader():
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(
        ipc, services=SessionServices(scorer=Scorer(known=KnownWords.from_set(["本"])))
    )
    reader.track_commands.declare(SubtitleStartupConfigured(1, None, "jp", "ja,jpn,jp"))
    reader.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "私は本を読む。")])
    yield reader
    reader.close()
    gateway.close()


def _toggle(reader: SessionController) -> None:
    reader.command_runtime.handle(ANALYSIS_MSG)


def _invalidate(reader: SessionController, *, vocabulary_changed: bool = False) -> None:
    reader.analysis_commands.invalidate(vocabulary_changed=vocabulary_changed)


def _finish(reader: SessionController) -> None:
    await_ready(
        lambda: reader.analysis_controller.settled,
        "analysis result was not published",
        pump=reader._drain_events,
    )


def test_toggle_shows_analyzing_then_result_without_pause_or_seek(reader):
    _toggle(reader)
    assert reader.analysis_controller.status == "Analyzing…"
    _finish(reader)

    assert reader.analysis_controller.result is not None
    assert reader.analysis_controller.status == "Ready"
    assert OverlayId.ANALYSIS in reader.ov._live
    forbidden = {"sub-seek", "seek"}
    assert not any(command and command[0] in forbidden for command in reader.ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in reader.ipc.commands)

    _toggle(reader)
    assert OverlayId.ANALYSIS not in reader.ov._live


def test_external_srt_without_mpv_sid_is_still_analyzable(reader):
    reader.track_commands.declare(
        SubtitleTracksDiscovered(None, reader.track_commands.current().en_sid)
    )

    _toggle(reader)
    assert reader.analysis_controller.status == "Analyzing…"
    _finish(reader)
    assert reader.analysis_controller.status == "Ready"
    assert reader.analysis_controller.result is not None


def test_no_index_reports_unavailable(reader):
    reader.track_commands.navigation.current.sub_index = None

    _toggle(reader)

    assert reader.analysis_controller.status == "Japanese track unavailable"
    assert reader.analysis_controller.settled


def test_cache_hit_reopens_with_the_same_result_immediately(reader):
    _toggle(reader)
    _finish(reader)
    result = reader.analysis_controller.result

    _toggle(reader)
    _toggle(reader)

    assert reader.analysis_controller.status == "Ready"
    assert reader.analysis_controller.result is result


def test_track_analysis_completes_while_overlay_is_closed(reader):
    _invalidate(reader)
    _finish(reader)

    assert reader.analysis_controller.result is not None
    assert not reader.analysis_controller.open
    assert OverlayId.ANALYSIS not in reader.ov._live


def test_dependency_loading_defers_analysis_until_vocabulary_arrives(reader):
    reader.profile_session.begin_loading()

    _invalidate(reader)
    assert reader.analysis_controller.settled

    reader.profile_session.accept(DependencyBundle(reader.profile_session.identity))
    _invalidate(reader, vocabulary_changed=True)
    _finish(reader)
    assert reader.analysis_controller.result is not None


def test_vocabulary_and_track_changes_invalidate_and_restart(reader):
    _toggle(reader)
    _finish(reader)

    _invalidate(reader, vocabulary_changed=True)
    assert reader.analysis_controller.status == "Analyzing…"
    _finish(reader)

    reader.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "彼は映画を見る。")])
    _invalidate(reader)
    _finish(reader)
    assert cue_result(reader.analysis_controller.result, 0) is not None


def test_latest_analysis_waits_for_a_slot_then_publishes(reader, monkeypatch):
    old_started = [threading.Event(), threading.Event()]
    old_release = threading.Event()
    analyze_cues = analysis_controller.analyze_cues
    newest_calls = 0

    def analyze(cues, scorer, tokenizer):
        nonlocal newest_calls
        if cues[0].text in {"古い一", "古い二"}:
            old_started[int(cues[0].text[-1] == "二")].set()
            old_release.wait(1)
        else:
            newest_calls += 1
        return analyze_cues(cues, scorer, tokenizer)

    monkeypatch.setattr(analysis_controller, "analyze_cues", analyze)
    reader.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "古い一")])
    _toggle(reader)
    assert old_started[0].wait(1)

    reader.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "古い二")])
    _invalidate(reader)
    assert old_started[1].wait(1)

    reader.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "新しい")])
    _invalidate(reader)
    assert reader.analysis_controller.status == "Analyzing…"

    old_release.set()
    _finish(reader)
    current = reader.analysis_controller.result
    assert current is not None

    drain_for(reader._drain_events)
    assert reader.analysis_controller.result is current
    assert newest_calls == 1


def test_worker_completion_is_applied_only_when_the_session_thread_drains(reader, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    worker_thread: list[int] = []
    analyze_cues = analysis_controller.analyze_cues

    def analyze(cues, scorer, tokenizer):
        worker_thread.append(threading.get_ident())
        started.set()
        release.wait(1)
        result = analyze_cues(cues, scorer, tokenizer)
        finished.set()
        return result

    monkeypatch.setattr(analysis_controller, "analyze_cues", analyze)
    session_thread = threading.get_ident()
    _toggle(reader)
    assert started.wait(1)

    release.set()
    assert finished.wait(1)
    assert reader.analysis_controller.result is None

    _finish(reader)
    assert reader.analysis_controller.result is not None
    assert worker_thread[0] != session_thread


def test_close_preserves_completed_analysis_for_the_session_summary():
    from saitenka.app.session_stats import SessionRecorder, analysis_snapshot

    snapshots = []

    class Writer:
        def submit(self, snapshot) -> None:
            snapshots.append(snapshot)

        def close(self, _timeout=2.0) -> None:
            pass

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(
        ipc, services=SessionServices(scorer=Scorer(known=KnownWords.from_set(["本"])))
    )
    result = None
    try:
        reader.track_commands.declare(SubtitleStartupConfigured(1, None, "jp", "ja,jpn,jp"))
        reader.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "私は本を読む。")])
        reader.history.replace_recorder(
            SessionRecorder(
                "/anime/Show 01.mkv",
                clock=lambda: 0.0,
                wall_clock=lambda: 0.0,
                writer=Writer(),
            )
        )
        _toggle(reader)
        _finish(reader)
        result = reader.analysis_controller.result
    finally:
        reader.close()
        gateway.close()

    assert result is not None
    assert snapshots[-1].completed
    assert snapshots[-1].analysis_json == analysis_snapshot(result)


def test_malformed_success_has_a_terminal_unavailable_state(reader, monkeypatch):
    monkeypatch.setattr(analysis_controller, "analyze_cues", lambda *_args: object())
    _toggle(reader)
    _finish(reader)

    assert reader.analysis_controller.result is None
    assert reader.analysis_controller.status == "Analysis unavailable"


def test_analysis_failure_has_a_terminal_unavailable_state(reader, monkeypatch, caplog):
    def fail(_cues, _scorer, _tokenizer):
        raise RuntimeError("boom")

    monkeypatch.setattr(analysis_controller, "analyze_cues", fail)
    with caplog.at_level("WARNING"):
        _toggle(reader)
        _finish(reader)

    assert reader.analysis_controller.result is None
    assert reader.analysis_controller.status == "Analysis unavailable"
    assert "episode analysis failed" in caplog.text


def test_english_or_missing_japanese_track_is_unavailable(reader):
    reader.track_commands.declare(SubtitleLanguageChanged("en"))

    _toggle(reader)

    assert reader.analysis_controller.status == "Japanese track unavailable"
    assert reader.analysis_controller.result is None
    assert reader.analysis_controller.settled


def test_ui_scale_enlarges_episode_analysis_window():
    normal = render_analysis(None, "Analyzing…", osd=(1920, 1080), close_key="`")
    enlarged = render_analysis(None, "Analyzing…", osd=(1920, 1080), close_key="`", scale=1.5)

    assert enlarged.width > normal.width
    assert enlarged.height > normal.height
