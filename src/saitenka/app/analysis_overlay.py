"""Background episode analysis and playback-neutral overlay orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from saitenka.app.episode_analysis import AnalysisKey, EpisodeAnalysis, analysis_key, analyze_cues
from saitenka.app.languages import MAIN_LANG
from saitenka.app.overlay_ids import OverlayId
from saitenka.render.analysis import render_analysis
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.app.scoring import Scorer
    from saitenka.app.tokenizer import Tokenizer
    from saitenka.subtitles import Cue


@dataclass
class AnalysisState:
    """Runtime state for background episode analysis, grouped off the Reader."""

    open: bool = False
    status: str = "Analyzing…"
    current: EpisodeAnalysis | None = None  # the analysis result currently rendered
    cache: dict[AnalysisKey, EpisodeAnalysis] = field(default_factory=dict)
    generation: int = 0  # bumped on cue/media change → cancels in-flight analysis
    active_key: AnalysisKey | None = None
    inflight: set[tuple[int, AnalysisKey]] = field(default_factory=set)
    pending: tuple[int, AnalysisKey, AnalysisRequest] | None = None


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    cues: tuple[Cue, ...]
    scorer: Scorer | None
    tokenizer: Tokenizer


def run_analysis(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, AnalysisRequest):
        raise TypeError("invalid analysis request")
    if cancelled.is_set():
        return None
    try:
        return analyze_cues(list(request.cues), request.scorer, request.tokenizer)
    except Exception:
        log.warning("episode analysis failed", exc_info=True)
        raise


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool: ...


def configure_runtime_job(ipc) -> JobSubmitter | None:
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(
        "episode-analysis",
        JobLanePolicy(capacity=2, workers=2),
        run_analysis,
    ):
        return None
    return ipc.submit_runtime_job


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
    reader.lifecycle_surfaces.present(image, x, y, oid=OverlayId.ANALYSIS)


def _unavailable(reader: Reader) -> bool:
    # SSOT: analysis reads reader._sub_index.cues — the SAME parsed index the subtitle draw + hover
    # render from — so availability is exactly "a JP index is loaded", nothing more. jp_sid (an mpv
    # embedded-track id, used only for track SWITCHING) is None whenever the JP subs come from an
    # external / extracted / jimaku .srt loaded straight into _sub_index — so gating on it wrongly
    # reported "Japanese track unavailable" while we were visibly showing (and could analyse) those subs.
    return reader.subtitle_language != MAIN_LANG or reader._sub_index is None


def request(reader: Reader) -> None:
    if _unavailable(reader):
        reader.analysis.current = None
        reader.analysis.status = "Japanese track unavailable"
        reader.analysis.active_key = None
        reader.analysis.pending = None
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
        reader.analysis.pending = None
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
    analysis_request = AnalysisRequest(tuple(index.cues), reader.scorer, reader.tokenizer)
    reader.analysis.pending = (generation, key, analysis_request)
    _show(reader)
    if submit_pending(reader.analysis, reader._analysis_submit, reader._finish_analysis):
        _show(reader)


def submit_pending(
    state: AnalysisState,
    submit: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> bool:
    pending = state.pending
    if pending is None or len(state.inflight) >= 2:
        return False
    if submit is None:
        state.active_key = None
        state.pending = None
        state.status = "Analysis unavailable"
        return True
    generation, key, analysis_request = pending
    identity = (generation, key)
    if identity in state.inflight:
        return False
    state.inflight.add(identity)
    state.pending = None
    accepted = submit(
        owner=Owner.SESSION,
        identity=identity,
        lane="episode-analysis",
        request=analysis_request,
        on_finished=on_finished,
    )
    if not accepted:
        state.inflight.discard(identity)
    return False


def finish(state: AnalysisState, completion: EffectFinished) -> bool:
    identity = completion.identity
    if not (
        isinstance(identity, tuple)
        and len(identity) == 2
        and isinstance(identity[0], int)
        and isinstance(identity[1], AnalysisKey)
    ):
        return False
    generation, key = identity
    state.inflight.discard((generation, key))
    result = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
    if not isinstance(result, EpisodeAnalysis):
        result = None
    if result is not None:
        state.cache[key] = result
    if generation != state.generation or key != state.active_key:
        return False
    state.active_key = None
    state.pending = None
    state.current = result
    state.status = "Ready" if result is not None else "Analysis unavailable"
    return True


def toggle(reader: Reader) -> None:
    reader.analysis.open = not reader.analysis.open
    if not reader.analysis.open:
        reader.lifecycle_surfaces.remove(OverlayId.ANALYSIS)
        return
    request(reader)


def on_index_changed(reader: Reader) -> None:
    reader.analysis.generation += 1
    reader.analysis.active_key = None
    reader.analysis.pending = None
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
