"""Background episode analysis and playback-neutral overlay orchestration."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from overlay.app.episode_analysis import analysis_key, analyze_cues
from overlay.app.overlay_ids import OverlayId
from overlay.render.analysis import render_analysis

if TYPE_CHECKING:
    from overlay.app.controller import Reader
    from overlay.app.episode_analysis import AnalysisKey, EpisodeAnalysis

log = logging.getLogger(__name__)


@dataclass
class AnalysisState:
    """Runtime state for background episode analysis, grouped off the Reader."""

    open: bool = False
    status: str = "Analyzing…"
    current: EpisodeAnalysis | None = None  # the analysis result currently rendered
    cache: dict[AnalysisKey, EpisodeAnalysis] = field(default_factory=dict)
    results: queue.SimpleQueue = field(default_factory=queue.SimpleQueue)
    threads: list[threading.Thread] = field(default_factory=list)
    generation: int = 0  # bumped on cue/media change → cancels in-flight analysis
    active_key: AnalysisKey | None = None


def _show(reader: Reader) -> None:
    if not reader.analysis.open:
        return
    image = render_analysis(
        reader.analysis.current,
        reader.analysis.status,
        osd=reader.osd,
        close_key=reader.analysis_key,
        scale=reader.chrome_scale,
    )
    x = (reader.osd[0] - image.width) // 2
    y = (reader.osd[1] - image.height) // 2
    reader.ov.show(image, x, y, oid=OverlayId.ANALYSIS)


def _unavailable(reader: Reader) -> bool:
    # SSOT: analysis reads reader._sub_index.cues — the SAME parsed index the subtitle draw + hover
    # render from — so availability is exactly "a JP index is loaded", nothing more. jp_sid (an mpv
    # embedded-track id, used only for track SWITCHING) is None whenever the JP subs come from an
    # external / extracted / jimaku .srt loaded straight into _sub_index — so gating on it wrongly
    # reported "Japanese track unavailable" while we were visibly showing (and could analyse) those subs.
    return reader.subtitle_language != "jp" or reader._sub_index is None


def request(reader: Reader) -> None:
    if _unavailable(reader):
        reader.analysis.current = None
        reader.analysis.status = "Japanese track unavailable"
        reader.analysis.active_key = None
        _show(reader)
        return
    if reader._loading:
        reader.analysis.current = None
        reader.analysis.status = "Analyzing…"
        _show(reader)
        return
    index = reader._sub_index
    assert index is not None
    key = analysis_key(index, reader.scorer)
    cached = reader.analysis.cache.get(key)
    if cached is not None:
        reader.analysis.current = cached
        reader.analysis.status = "Ready"
        reader.analysis.active_key = None
        _show(reader)
        return
    if key == reader.analysis.active_key:
        _show(reader)
        return
    reader.analysis.generation += 1
    generation = reader.analysis.generation
    reader.analysis.active_key = key
    reader.analysis.current = None
    reader.analysis.status = "Analyzing…"
    _show(reader)
    cues = list(index.cues)
    scorer = reader.scorer

    def work() -> None:
        try:
            result = analyze_cues(cues, scorer)
            reader.analysis.results.put((generation, key, result, None))
        except Exception as exc:  # fail-soft background feature
            log.warning("episode analysis failed", exc_info=True)
            reader.analysis.results.put((generation, key, None, str(exc)))

    thread = threading.Thread(target=work, name="saitenka-episode-analysis", daemon=True)
    reader.analysis.threads.append(thread)
    thread.start()


def apply_results(reader: Reader) -> None:
    while True:
        try:
            generation, key, result, error = reader.analysis.results.get_nowait()
        except queue.Empty:
            return
        if result is not None:
            reader.analysis.cache[key] = result
        if generation != reader.analysis.generation or key != reader.analysis.active_key:
            continue
        reader.analysis.active_key = None
        reader.analysis.current = result
        reader.analysis.status = f"Analysis unavailable: {error}" if error else "Ready"
        _show(reader)


def toggle(reader: Reader) -> None:
    reader.analysis.open = not reader.analysis.open
    if not reader.analysis.open:
        reader.ov.hide(OverlayId.ANALYSIS)
        return
    request(reader)


def on_index_changed(reader: Reader) -> None:
    reader.analysis.generation += 1
    reader.analysis.active_key = None
    reader.analysis.current = None
    request(reader)


def on_vocabulary_changed(reader: Reader) -> None:
    reader.analysis.cache.clear()
    on_index_changed(reader)


def redraw(reader: Reader) -> None:
    _show(reader)


def cue_result(reader: Reader, cue_index: int):
    result = reader.analysis.current
    if result is None or not 0 <= cue_index < len(result.cues):
        return None
    return result.cues[cue_index]
