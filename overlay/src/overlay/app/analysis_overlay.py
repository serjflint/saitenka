"""Background episode analysis and playback-neutral overlay orchestration."""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING

from overlay.app.episode_analysis import analysis_key, analyze_cues
from overlay.app.overlay_ids import OverlayId
from overlay.render.analysis import render_analysis

if TYPE_CHECKING:
    from overlay.app.controller import Reader

log = logging.getLogger(__name__)


def _show(reader: Reader) -> None:
    if not reader._analysis_open:
        return
    image = render_analysis(
        reader._episode_analysis,
        reader._analysis_status,
        osd=reader.osd,
        close_key=reader.analysis_key,
    )
    x = (reader.osd[0] - image.width) // 2
    y = (reader.osd[1] - image.height) // 2
    reader.ov.show(image, x, y, oid=OverlayId.ANALYSIS)


def _unavailable(reader: Reader) -> bool:
    return reader.subtitle_language != "jp" or reader.jp_sid is None or reader._sub_index is None


def request(reader: Reader) -> None:
    if _unavailable(reader):
        reader._episode_analysis = None
        reader._analysis_status = "Japanese track unavailable"
        reader._analysis_active_key = None
        _show(reader)
        return
    if reader._loading:
        reader._episode_analysis = None
        reader._analysis_status = "Analyzing…"
        _show(reader)
        return
    index = reader._sub_index
    assert index is not None
    key = analysis_key(index, reader.scorer)
    cached = reader._analysis_cache.get(key)
    if cached is not None:
        reader._episode_analysis = cached
        reader._analysis_status = "Ready"
        reader._analysis_active_key = None
        _show(reader)
        return
    if key == reader._analysis_active_key:
        _show(reader)
        return
    reader._analysis_generation += 1
    generation = reader._analysis_generation
    reader._analysis_active_key = key
    reader._episode_analysis = None
    reader._analysis_status = "Analyzing…"
    _show(reader)
    cues = list(index.cues)
    scorer = reader.scorer

    def work() -> None:
        try:
            result = analyze_cues(cues, scorer)
            reader._analysis_results.put((generation, key, result, None))
        except Exception as exc:  # fail-soft background feature
            log.warning("episode analysis failed", exc_info=True)
            reader._analysis_results.put((generation, key, None, str(exc)))

    thread = threading.Thread(target=work, name="saitenka-episode-analysis", daemon=True)
    reader._analysis_threads.append(thread)
    thread.start()


def apply_results(reader: Reader) -> None:
    while True:
        try:
            generation, key, result, error = reader._analysis_results.get_nowait()
        except queue.Empty:
            return
        if result is not None:
            reader._analysis_cache[key] = result
        if generation != reader._analysis_generation or key != reader._analysis_active_key:
            continue
        reader._analysis_active_key = None
        reader._episode_analysis = result
        reader._analysis_status = f"Analysis unavailable: {error}" if error else "Ready"
        _show(reader)


def toggle(reader: Reader) -> None:
    reader._analysis_open = not reader._analysis_open
    if not reader._analysis_open:
        reader.ov.hide(OverlayId.ANALYSIS)
        return
    request(reader)


def on_index_changed(reader: Reader) -> None:
    reader._analysis_generation += 1
    reader._analysis_active_key = None
    reader._episode_analysis = None
    request(reader)


def on_vocabulary_changed(reader: Reader) -> None:
    reader._analysis_cache.clear()
    on_index_changed(reader)


def redraw(reader: Reader) -> None:
    _show(reader)


def cue_result(reader: Reader, cue_index: int):
    result = reader._episode_analysis
    if result is None or not 0 <= cue_index < len(result.cues):
        return None
    return result.cues[cue_index]
