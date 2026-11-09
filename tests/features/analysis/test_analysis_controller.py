"""Episode analysis runs off-thread and its overlay never mutates playback."""

import threading

import pytest
from saitenka_subtitles import Cue, CueIndex
from saitenka_wordstate import Scorer
from saitenka_wordstate.known import KnownWords
from session_builder import TestSession, build_session
from util import FakeIPC, await_ready, drain_for, overlay_frame, session_gateway

from saitenka.app.bindings import ANALYSIS_MSG
from saitenka.app.features.analysis import analysis_controller
from saitenka.app.features.analysis.analysis_rows import analysis_rows
from saitenka.app.features.analysis.episode_analysis import cue_result
from saitenka.app.features.profiles.dependencies import DependencyBundle
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.scoring import Coloring
from saitenka.app.session.factory import SessionServices
from saitenka.render.analysis import render_analysis
from saitenka.runtime.events import (
    SubtitleLanguageChanged,
    SubtitleStartupConfigured,
    SubtitleTracksDiscovered,
)


@pytest.fixture
def reader():
    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    reader = build_session(
        ipc, services=SessionServices(scorer=Coloring(Scorer(known=KnownWords.from_set(["本"]))))
    )
    reader.graph.track_commands.declare(SubtitleStartupConfigured(1, None, "jp", "ja,jpn,jp"))
    reader.graph.track_commands.navigation.current.sub_index = CueIndex(
        [Cue(0, 1, "私は本を読む。")]
    )
    yield reader
    reader.close()
    gateway.close()


@pytest.fixture
def analysis_reader(make_session):
    reader = make_session(
        FakeIPC(),
        services=SessionServices(scorer=Coloring(Scorer(known=KnownWords.from_set(["本"])))),
    )
    reader.graph.track_commands.declare(SubtitleStartupConfigured(1, None, "jp", "ja,jpn,jp"))
    return reader


def _load_episode(reader, tmp_path, text="私は本を読む。"):
    path = tmp_path / "episode.srt"
    path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n")
    reader.graph.subtitle_navigation.load_index(path)


def _toggle(reader: TestSession) -> None:
    reader.command(ANALYSIS_MSG)


def _invalidate(reader: TestSession, *, vocabulary_changed: bool = False) -> None:
    reader.graph.analysis_commands.invalidate(vocabulary_changed=vocabulary_changed)


def _finish(reader: TestSession) -> None:
    await_ready(
        lambda: reader.graph.analysis.settled,
        "analysis result was not published",
        pump=reader.pump,
    )


def test_toggle_shows_analyzing_then_result_without_pause_or_seek(
    analysis_reader, tmp_path, monkeypatch
):
    reader = analysis_reader
    started = threading.Event()
    release = threading.Event()
    analyze_cues = analysis_controller.analyze_cues

    def analyze(cues, scorer, tokenizer):
        started.set()
        release.wait(1)
        return analyze_cues(cues, scorer, tokenizer)

    monkeypatch.setattr(analysis_controller, "analyze_cues", analyze)
    _load_episode(reader, tmp_path)
    _toggle(reader)
    assert started.wait(1)
    assert reader.graph.analysis.status == "Analyzing…"
    release.set()
    _finish(reader)

    assert reader.graph.analysis.result is not None
    assert reader.graph.analysis.status == "Ready"
    assert overlay_frame(reader.graph.ipc, OverlayId.ANALYSIS) is not None
    forbidden = {"sub-seek", "seek"}
    assert not any(command and command[0] in forbidden for command in reader.graph.ipc.commands)
    assert not any(
        command[:2] == ("set_property", "pause") for command in reader.graph.ipc.commands
    )

    _toggle(reader)
    await_ready(
        lambda: overlay_frame(reader.graph.ipc, OverlayId.ANALYSIS) is None,
        "analysis overlay did not retire",
        pump=reader.pump,
    )


def test_external_srt_without_mpv_sid_is_still_analyzable(analysis_reader, tmp_path):
    reader = analysis_reader
    reader.graph.track_commands.declare(
        SubtitleTracksDiscovered(None, reader.graph.track_commands.current().en_sid)
    )

    _load_episode(reader, tmp_path)
    _toggle(reader)
    _finish(reader)
    assert reader.graph.analysis.status == "Ready"
    assert reader.graph.analysis.result is not None


def test_no_index_reports_unavailable(analysis_reader):
    reader = analysis_reader

    _toggle(reader)

    assert reader.graph.analysis.status == "Japanese track unavailable"
    assert reader.graph.analysis.settled
    assert reader.graph.analysis.result is None


def test_cache_hit_reopens_with_the_same_result_immediately(analysis_reader, tmp_path):
    reader = analysis_reader
    _load_episode(reader, tmp_path)
    _toggle(reader)
    _finish(reader)
    result = reader.graph.analysis.result
    assert result is not None

    _toggle(reader)
    _toggle(reader)

    assert reader.graph.analysis.status == "Ready"
    assert reader.graph.analysis.result is result
    assert reader.graph.analysis.settled


def test_track_analysis_completes_while_overlay_is_closed(analysis_reader, tmp_path):
    reader = analysis_reader
    _load_episode(reader, tmp_path)
    _finish(reader)

    assert reader.graph.analysis.result is not None
    assert reader.graph.analysis.status == "Ready"
    assert not reader.graph.analysis.open
    assert overlay_frame(reader.graph.ipc, OverlayId.ANALYSIS) is None


def test_dependency_loading_defers_analysis_until_vocabulary_arrives(analysis_reader, tmp_path):
    reader = analysis_reader
    reader.graph.profile.begin_loading()

    _load_episode(reader, tmp_path)
    assert reader.graph.analysis.settled
    assert reader.graph.analysis.result is None

    reader.graph.profile.accept(DependencyBundle(reader.graph.profile.identity))
    _invalidate(reader, vocabulary_changed=True)
    _finish(reader)
    assert reader.graph.analysis.result is not None


def test_vocabulary_and_track_changes_invalidate_and_restart(analysis_reader, tmp_path):
    reader = analysis_reader
    _load_episode(reader, tmp_path)
    _toggle(reader)
    _finish(reader)

    _invalidate(reader, vocabulary_changed=True)
    assert reader.graph.analysis.status == "Analyzing…"
    _finish(reader)

    _load_episode(reader, tmp_path, "彼は映画を見る。")
    _finish(reader)
    assert cue_result(reader.graph.analysis.result, 0) is not None
    assert reader.graph.analysis.status == "Ready"
    assert len(reader.graph.analysis.result.cues) == 1


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
    reader.graph.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "古い一")])
    _toggle(reader)
    assert old_started[0].wait(1)

    reader.graph.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "古い二")])
    _invalidate(reader)
    assert old_started[1].wait(1)

    reader.graph.track_commands.navigation.current.sub_index = CueIndex([Cue(0, 1, "新しい")])
    _invalidate(reader)
    assert reader.graph.analysis.status == "Analyzing…"

    old_release.set()
    _finish(reader)
    current = reader.graph.analysis.result
    assert current is not None

    drain_for(reader.pump)
    assert reader.graph.analysis.result is current
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
    assert reader.graph.analysis.result is None

    _finish(reader)
    assert reader.graph.analysis.result is not None
    assert worker_thread[0] != session_thread


def test_close_preserves_completed_analysis_for_the_session_summary(make_session):
    from saitenka.app.session_stats import SessionRecorder, analysis_snapshot

    snapshots = []

    class Writer:
        def submit(self, snapshot) -> None:
            snapshots.append(snapshot)

        def close(self, _timeout=2.0) -> None:
            pass

    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    reader = make_session(
        ipc, services=SessionServices(scorer=Coloring(Scorer(known=KnownWords.from_set(["本"]))))
    )
    result = None
    try:
        reader.graph.track_commands.declare(SubtitleStartupConfigured(1, None, "jp", "ja,jpn,jp"))
        reader.graph.track_commands.navigation.current.sub_index = CueIndex(
            [Cue(0, 1, "私は本を読む。")]
        )
        reader.graph.history.replace_recorder(
            SessionRecorder(
                "/anime/Show 01.mkv",
                clock=lambda: 0.0,
                wall_clock=lambda: 0.0,
                writer=Writer(),
            )
        )
        _toggle(reader)
        _finish(reader)
        result = reader.graph.analysis.result
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

    assert reader.graph.analysis.result is None
    assert reader.graph.analysis.status == "Analysis unavailable"


def test_analysis_failure_has_a_terminal_unavailable_state(reader, monkeypatch, caplog):
    def fail(_cues, _scorer, _tokenizer):
        raise RuntimeError("boom")

    monkeypatch.setattr(analysis_controller, "analyze_cues", fail)
    with caplog.at_level("WARNING"):
        _toggle(reader)
        _finish(reader)

    assert reader.graph.analysis.result is None
    assert reader.graph.analysis.status == "Analysis unavailable"
    assert "episode analysis failed" in caplog.text


def test_ui_scale_enlarges_episode_analysis_window():
    rows = analysis_rows(None, "Analyzing…")
    normal = render_analysis(rows, osd=(1920, 1080), close_key="`")
    enlarged = render_analysis(rows, osd=(1920, 1080), close_key="`", scale=1.5)

    assert enlarged.width > normal.width
    assert enlarged.height > normal.height


def test_english_or_missing_japanese_track_is_unavailable(analysis_reader, tmp_path):
    reader = analysis_reader
    reader.graph.track_commands.declare(SubtitleLanguageChanged("en"))

    _load_episode(reader, tmp_path)
    _toggle(reader)

    assert reader.graph.analysis.status == "Japanese track unavailable"
    assert reader.graph.analysis.result is None
    assert reader.graph.analysis.settled
