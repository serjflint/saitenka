"""Episode analysis runs off-thread and its overlay never mutates playback."""

import threading
import time

import pytest
from util import FakeIPC, await_ready, runtime_gateway

from saitenka.app import analysis_overlay
from saitenka.app.bindings import ANALYSIS_MSG
from saitenka.app.controller import Reader
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.scoring import Scorer
from saitenka.app.wordlists import KnownWords
from saitenka.render.analysis import render_analysis
from saitenka.runtime.events import (
    SubtitleLanguageChanged,
    SubtitleStartupConfigured,
    SubtitleTracksDiscovered,
)
from saitenka.subtitles import Cue, CueIndex


def _toggle_analysis(reader) -> None:
    """Flip the panel the way the panel reducer would.

    `analysis_overlay.set_open` replaced `toggle` when the open/close decision moved to
    `panel_intents`; these tests are about what opening and closing *do*, so they keep flipping.
    """
    reader.set_analysis_open(open=not reader.analysis.open)


@pytest.fixture
def reader():
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc, scorer=Scorer(known=KnownWords.from_set(["本"])))
    reader.declare_subtitle(SubtitleStartupConfigured(1, None, "jp", "ja,jpn,jp"))
    reader._sub_index = CueIndex([Cue(0, 1, "私は本を読む。")])
    yield reader
    reader.close()
    gateway.close()


def _finish(reader: Reader) -> None:
    await_ready(
        lambda: reader.analysis.active_key is None,
        "analysis result was not published",
        pump=reader._drain_events,
    )


def test_toggle_shows_analyzing_then_result_without_pause_or_seek(reader):
    reader._handle(ANALYSIS_MSG)
    assert reader.analysis.status == "Analyzing…"
    _finish(reader)

    assert reader.analysis.current is not None
    assert reader.analysis.status == "Ready"
    assert OverlayId.ANALYSIS in reader.ov._live
    forbidden = {"sub-seek", "seek"}
    assert not any(command and command[0] in forbidden for command in reader.ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in reader.ipc.commands)

    reader._handle(ANALYSIS_MSG)
    assert OverlayId.ANALYSIS not in reader.ov._live


def test_external_srt_without_mpv_sid_is_still_analyzable(reader):
    # Regression: JP subs from an external / extracted / jimaku .srt carry no mpv jp_sid, but _sub_index
    # holds the cues we render AND analyse — so analysis must run, not report "Japanese track unavailable".
    # external index → no embedded-track sid
    reader.declare_subtitle(SubtitleTracksDiscovered(None, reader.en_sid))

    reader._handle(ANALYSIS_MSG)
    assert reader.analysis.status == "Analyzing…"
    _finish(reader)
    assert reader.analysis.status == "Ready"
    assert reader.analysis.current is not None


def test_no_index_reports_unavailable(reader):
    reader._sub_index = None  # the real SSOT for "no analysable JP cues"

    reader._handle(ANALYSIS_MSG)
    assert reader.analysis.status == "Japanese track unavailable"
    assert reader.analysis.active_key is None


def test_cache_hit_does_not_start_another_worker(reader):
    _toggle_analysis(reader)
    _finish(reader)
    generation = reader.analysis.generation

    _toggle_analysis(reader)
    _toggle_analysis(reader)

    assert reader.analysis.generation == generation
    assert reader.analysis.current is not None


def test_track_analysis_completes_while_overlay_is_closed(reader):
    reader.invalidate_analysis()
    _finish(reader)

    assert reader.analysis.current is not None
    assert not reader.analysis.open
    assert OverlayId.ANALYSIS not in reader.ov._live


def test_dependency_loading_defers_analysis_until_vocabulary_arrives(reader):
    reader._loading = True

    reader.invalidate_analysis()
    assert reader.analysis.active_key is None

    reader._loading = False
    reader.invalidate_analysis(vocabulary_changed=True)
    _finish(reader)
    assert reader.analysis.current is not None


def test_vocabulary_and_track_changes_invalidate_and_restart(reader):
    _toggle_analysis(reader)
    _finish(reader)

    reader.invalidate_analysis(vocabulary_changed=True)
    assert reader.analysis.status == "Analyzing…"
    assert reader.analysis.generation == 3
    _finish(reader)

    reader._sub_index = CueIndex([Cue(0, 1, "彼は映画を見る。")])
    reader.invalidate_analysis()
    assert reader.analysis.generation == 5
    _finish(reader)
    assert analysis_overlay.cue_result(reader.analysis.current, 0) is not None


def test_latest_analysis_waits_for_a_slot_then_publishes(reader, monkeypatch):
    old_started = [threading.Event(), threading.Event()]
    old_release = threading.Event()
    analyze_cues = analysis_overlay.analyze_cues
    newest_calls = 0

    def analyze(cues, scorer, tokenizer):
        nonlocal newest_calls
        if cues[0].text in {"古い一", "古い二"}:
            old_started[int(cues[0].text[-1] == "二")].set()
            old_release.wait(1)
        else:
            newest_calls += 1
        return analyze_cues(cues, scorer, tokenizer)

    monkeypatch.setattr(analysis_overlay, "analyze_cues", analyze)
    reader._sub_index = CueIndex([Cue(0, 1, "古い一")])
    _toggle_analysis(reader)
    assert old_started[0].wait(1)

    reader._sub_index = CueIndex([Cue(0, 1, "古い二")])
    reader.invalidate_analysis()
    assert old_started[1].wait(1)

    reader._sub_index = CueIndex([Cue(0, 1, "新しい")])
    reader.invalidate_analysis()
    assert reader.analysis.status == "Analyzing…"

    old_release.set()
    _finish(reader)
    current = reader.analysis.current
    assert current is not None

    # Not a wait — see the note in test_progressive: this proves nothing further arrives.
    for _ in range(200):
        reader._drain_events()
        time.sleep(0.001)
    assert reader.analysis.current is current
    assert newest_calls == 1


def test_malformed_success_has_a_terminal_unavailable_state(reader, monkeypatch):
    monkeypatch.setattr(analysis_overlay, "analyze_cues", lambda *_args: object())
    _toggle_analysis(reader)
    _finish(reader)

    assert reader.analysis.current is None
    assert reader.analysis.status == "Analysis unavailable"


def test_analysis_failure_has_a_terminal_unavailable_state(reader, monkeypatch, caplog):
    def fail(_cues, _scorer, _tokenizer):
        raise RuntimeError("boom")

    monkeypatch.setattr(analysis_overlay, "analyze_cues", fail)
    with caplog.at_level("WARNING"):
        _toggle_analysis(reader)
        _finish(reader)

    assert reader.analysis.current is None
    assert reader.analysis.status == "Analysis unavailable"
    assert "episode analysis failed" in caplog.text


def test_english_or_missing_japanese_track_is_unavailable(reader):
    reader.declare_subtitle(SubtitleLanguageChanged("en"))

    _toggle_analysis(reader)

    assert reader.analysis.status == "Japanese track unavailable"
    assert reader.analysis.current is None
    assert reader.analysis.active_key is None


def test_ui_scale_enlarges_episode_analysis_window():
    normal = render_analysis(None, "Analyzing…", osd=(1920, 1080), close_key="`")
    enlarged = render_analysis(None, "Analyzing…", osd=(1920, 1080), close_key="`", scale=1.5)

    assert enlarged.width > normal.width
    assert enlarged.height > normal.height
