"""Episode analysis runs off-thread and its overlay never mutates playback."""

from overlay.app import analysis_overlay
from overlay.app.bindings import ANALYSIS_MSG
from overlay.app.controller import Reader
from overlay.app.overlay_ids import OverlayId
from overlay.app.scoring import Scorer
from overlay.app.sub_index import SubCue, SubIndex
from overlay.app.wordlists import KnownWords


class FakeIPC:
    def __init__(self):
        self.commands: list[tuple] = []

    def command(self, *args):
        self.commands.append(args)
        return {"data": None}


def _reader() -> Reader:
    reader = Reader(FakeIPC(), scorer=Scorer(known=KnownWords.from_set(["本"])))
    reader.jp_sid = 1
    reader.subtitle_language = "jp"
    reader._sub_index = SubIndex([SubCue(0, 1, "私は本を読む。")])
    return reader


def _finish(reader: Reader) -> None:
    reader._analysis_threads[-1].join(timeout=2)
    assert not reader._analysis_threads[-1].is_alive()
    analysis_overlay.apply_results(reader)


def test_toggle_shows_analyzing_then_result_without_pause_or_seek():
    reader = _reader()

    reader._handle(ANALYSIS_MSG)
    assert reader._analysis_status == "Analyzing…"
    _finish(reader)

    assert reader._episode_analysis is not None
    assert reader._analysis_status == "Ready"
    assert OverlayId.ANALYSIS in reader.ov._live
    forbidden = {"sub-seek", "seek"}
    assert not any(command and command[0] in forbidden for command in reader.ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in reader.ipc.commands)

    reader._handle(ANALYSIS_MSG)
    assert OverlayId.ANALYSIS not in reader.ov._live


def test_cache_hit_does_not_start_another_worker():
    reader = _reader()
    analysis_overlay.toggle(reader)
    _finish(reader)
    workers = len(reader._analysis_threads)

    analysis_overlay.toggle(reader)
    analysis_overlay.toggle(reader)

    assert len(reader._analysis_threads) == workers
    assert reader._episode_analysis is not None


def test_track_analysis_completes_while_overlay_is_closed():
    reader = _reader()

    analysis_overlay.on_index_changed(reader)
    _finish(reader)

    assert reader._episode_analysis is not None
    assert not reader._analysis_open
    assert OverlayId.ANALYSIS not in reader.ov._live


def test_dependency_loading_defers_analysis_until_vocabulary_arrives():
    reader = _reader()
    reader._loading = True

    analysis_overlay.on_index_changed(reader)
    assert not reader._analysis_threads

    reader._loading = False
    analysis_overlay.on_vocabulary_changed(reader)
    _finish(reader)
    assert reader._episode_analysis is not None


def test_vocabulary_and_track_changes_invalidate_and_restart():
    reader = _reader()
    analysis_overlay.toggle(reader)
    _finish(reader)

    analysis_overlay.on_vocabulary_changed(reader)
    assert reader._analysis_status == "Analyzing…"
    assert len(reader._analysis_threads) == 2
    _finish(reader)

    reader._sub_index = SubIndex([SubCue(0, 1, "彼は映画を見る。")])
    analysis_overlay.on_index_changed(reader)
    assert len(reader._analysis_threads) == 3
    _finish(reader)
    assert analysis_overlay.cue_result(reader, 0) is not None


def test_english_or_missing_japanese_track_is_unavailable():
    reader = _reader()
    reader.subtitle_language = "en"

    analysis_overlay.toggle(reader)

    assert reader._analysis_status == "Japanese track unavailable"
    assert reader._episode_analysis is None
    assert not reader._analysis_threads
