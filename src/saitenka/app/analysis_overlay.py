"""Background episode analysis and playback-neutral overlay orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from saitenka.app.episode_analysis import AnalysisKey, EpisodeAnalysis, analysis_key, analyze_cues
from saitenka.app.languages import MAIN_LANG
from saitenka.render.analysis import render_analysis
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.scoring import Scorer
    from saitenka.app.tokenizer import Tokenizer
    from saitenka.subtitles import Cue, CueIndex


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


def panel_image(state: AnalysisState, *, osd: tuple[int, int], close_key: str, scale: float):
    """Render the panel for a screen. Pure: same state and size, same pixels."""
    return render_analysis(state.current, state.status, osd=osd, close_key=close_key, scale=scale)


def unavailable(language: str, index: CueIndex | None) -> bool:
    """Whether there is anything to analyse.

    SSOT: analysis reads the SAME parsed index the subtitle draw and hover render from, so
    availability is exactly "a JP index is loaded", nothing more. Deliberately not `jp_sid`, an mpv
    embedded-track id used only for track SWITCHING — it is None whenever the JP subs came from an
    external / extracted / jimaku .srt loaded straight into the index, and gating on it reported
    "Japanese track unavailable" while those very subs were on screen and analysable.
    """
    return language != MAIN_LANG or index is None


def request(
    state: AnalysisState,
    *,
    language: str,
    index: CueIndex | None,
    loading: bool,
    scorer: Scorer | None,
    tokenizer: Tokenizer,
) -> None:
    """Bring ``state`` up to date for the current episode, queueing work if it is not cached.

    Takes the feature's own state and the facts it decides from — not the session. The caller
    presents afterwards, which is why no branch here draws.
    """
    if unavailable(language, index):
        state.current = None
        state.status = "Japanese track unavailable"
        state.active_key = None
        state.pending = None
        return
    if loading:
        state.current = None
        state.status = "Analyzing…"
        return
    assert index is not None
    key = analysis_key(index, scorer)
    cached = state.cache.get(key)
    if cached is not None:
        state.current = cached
        state.status = "Ready"
        state.active_key = None
        state.pending = None
        return
    if key == state.active_key:
        return  # already running for exactly this episode and vocabulary
    state.generation += 1
    state.active_key = key
    state.current = None
    state.status = "Analyzing…"
    state.pending = (state.generation, key, AnalysisRequest(tuple(index.cues), scorer, tokenizer))


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


def invalidate(state: AnalysisState, *, vocabulary_changed: bool = False) -> None:
    """Retire whatever is running or shown, because its inputs moved.

    A vocabulary change also drops the cache: the same episode scores differently once the known
    set does, so a cached result for that key would be answering the old question.
    """
    if vocabulary_changed:
        state.cache.clear()
    state.generation += 1
    state.active_key = None
    state.pending = None
    state.current = None


def cue_result(result: EpisodeAnalysis | None, cue_index: int):
    """The per-cue row for ``cue_index``, or None when the analysis does not cover it."""
    if result is None or not 0 <= cue_index < len(result.cues):
        return None
    return result.cues[cue_index]
