"""Bounded owner of episode-analysis state, work, presentation, and lane lifetime."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from saitenka_tokenize.languages import MAIN_LANG

from saitenka.app.features.analysis.analysis_rows import analysis_rows
from saitenka.app.features.analysis.episode_analysis import (
    AnalysisKey,
    EpisodeAnalysis,
    analysis_key,
    analyze_cues,
)
from saitenka.app.overlay_ids import OverlayId
from saitenka.render.analysis import render_analysis
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, configure_lane

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka_subtitles import Cue, CueIndex
    from saitenka_tokenize.registry import Tokenizer

    from saitenka.app.config import KeyOptions
    from saitenka.app.features.help.help_controller import ScreenState
    from saitenka.app.features.profiles.profile_session import ProfileSession
    from saitenka.app.features.subtitle.navigation_state import NavigationStore
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.scoring import Coloring
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore

log = logging.getLogger(__name__)
LANE = "episode-analysis"


@dataclass(frozen=True, slots=True)
class AnalysisInputs:
    """Volatile facts captured together when one analysis operation is admitted."""

    language: str
    index: CueIndex | None
    loading: bool
    scorer: Coloring | None
    tokenizer: Tokenizer


@dataclass(frozen=True, slots=True)
class AnalysisObservation:
    """Capture the profile, track, and episode facts for one analysis admission."""

    tracks: SubtitleTrackStore
    navigation: NavigationStore
    profile: ProfileSession

    def current(self) -> AnalysisInputs:
        return AnalysisInputs(
            language=self.tracks.current.language,
            index=self.navigation.current.sub_index,
            loading=self.profile.loading,
            scorer=self.profile.scorer,
            tokenizer=self.profile.profile.tokenizer,
        )


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    cues: tuple[Cue, ...]
    scorer: Coloring | None
    tokenizer: Tokenizer


@dataclass(slots=True)
class _AnalysisState:
    open: bool = False
    status: str = "Analyzing…"
    current: EpisodeAnalysis | None = None
    cache: dict[AnalysisKey, EpisodeAnalysis] = field(default_factory=dict)
    generation: int = 0
    active_key: AnalysisKey | None = None
    inflight: set[tuple[int, AnalysisKey]] = field(default_factory=set)
    pending: tuple[int, AnalysisKey, AnalysisRequest] | None = None


@dataclass(frozen=True, slots=True)
class AnalysisCommandEndpoint:
    _owner: AnalysisController
    _inputs: Callable[[], AnalysisInputs]

    @property
    def open(self) -> bool:
        return self._owner.open

    def set_open(self, *, open: bool) -> None:  # noqa: A002
        self._owner.set_open(self._inputs(), open=open)

    def invalidate(self, *, vocabulary_changed: bool = False) -> None:
        self._owner.invalidate(self._inputs(), vocabulary_changed=vocabulary_changed)


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


class AnalysisController:
    """One writer for analysis admission, result state, overlay, and completion."""

    def __init__(
        self,
        ipc: MpvIPC,
        surfaces: LifecycleSurfaces,
        screen: ScreenState,
        keys: KeyOptions,
        *,
        ui_scale: float,
    ) -> None:
        self._ipc = ipc
        self._surfaces = surfaces
        self._screen = screen
        self._keys = keys
        self._ui_scale = ui_scale
        self._submit = configure_lane(
            ipc,
            LANE,
            JobLanePolicy(capacity=2, workers=2),
            run_analysis,
        )
        self._state = _AnalysisState()

    @property
    def open(self) -> bool:
        return self._state.open

    @property
    def status(self) -> str:
        return self._state.status

    @property
    def result(self) -> EpisodeAnalysis | None:
        return self._state.current

    @property
    def settled(self) -> bool:
        return self._state.active_key is None and self._state.pending is None

    def endpoint(self, inputs: Callable[[], AnalysisInputs]) -> AnalysisCommandEndpoint:
        return AnalysisCommandEndpoint(self, inputs)

    def set_open(self, inputs: AnalysisInputs, *, open: bool) -> None:  # noqa: A002
        self._state.open = open
        if not open:
            self._surfaces.remove(OverlayId.ANALYSIS)
            return
        self.refresh(inputs)

    def invalidate(
        self,
        inputs: AnalysisInputs,
        *,
        vocabulary_changed: bool = False,
    ) -> None:
        state = self._state
        if vocabulary_changed:
            state.cache.clear()
        state.generation += 1
        state.active_key = None
        state.pending = None
        state.current = None
        self.refresh(inputs)

    def refresh(self, inputs: AnalysisInputs) -> None:
        self._request(inputs)
        self.redraw()
        if self._submit_pending():
            self.redraw()

    def redraw(self) -> None:
        state = self._state
        if not state.open:
            return
        osd = self._screen.osd
        image = render_analysis(
            analysis_rows(state.current, state.status),
            osd=osd,
            close_key=self._keys.analysis_key,
            scale=self._ui_scale * max(1.0, osd[1] / 1080),
        )
        self._surfaces.present(
            image,
            (osd[0] - image.width) // 2,
            (osd[1] - image.height) // 2,
            oid=OverlayId.ANALYSIS,
        )

    def close_lane(self, timeout: float) -> bool:
        """Stop work without discarding the result consumed by the later summary phase."""
        return self._ipc.close_runtime_job_lane(LANE, timeout)

    def _request(self, inputs: AnalysisInputs) -> None:
        state = self._state
        if inputs.language != MAIN_LANG or inputs.index is None:
            state.current = None
            state.status = "Japanese track unavailable"
            state.active_key = None
            state.pending = None
            return
        if inputs.loading:
            state.current = None
            state.status = "Analyzing…"
            return
        key = analysis_key(inputs.index, inputs.scorer)
        cached = state.cache.get(key)
        if cached is not None:
            state.current = cached
            state.status = "Ready"
            state.active_key = None
            state.pending = None
            return
        if key == state.active_key:
            return
        state.generation += 1
        state.active_key = key
        state.current = None
        state.status = "Analyzing…"
        state.pending = (
            state.generation,
            key,
            AnalysisRequest(tuple(inputs.index.cues), inputs.scorer, inputs.tokenizer),
        )

    def _submit_pending(self) -> bool:
        state = self._state
        pending = state.pending
        if pending is None or len(state.inflight) >= 2:
            return False
        if self._submit is None:
            state.active_key = None
            state.pending = None
            state.status = "Analysis unavailable"
            return True
        generation, key, request = pending
        identity = (generation, key)
        if identity in state.inflight:
            return False
        state.inflight.add(identity)
        state.pending = None
        accepted = self._submit(
            owner=Owner.SESSION,
            identity=identity,
            lane=LANE,
            request=request,
            on_finished=self._finish,
        )
        if not accepted:
            state.inflight.discard(identity)
        return False

    def _finish(self, completion: EffectFinished) -> None:
        state = self._state
        identity = completion.identity
        if not (
            isinstance(identity, tuple)
            and len(identity) == 2
            and isinstance(identity[0], int)
            and isinstance(identity[1], AnalysisKey)
        ):
            return
        generation, key = identity
        state.inflight.discard((generation, key))
        result = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
        if not isinstance(result, EpisodeAnalysis):
            result = None
        if result is not None:
            state.cache[key] = result
        changed = False
        if generation == state.generation and key == state.active_key:
            state.active_key = None
            state.pending = None
            state.current = result
            state.status = "Ready" if result is not None else "Analysis unavailable"
            changed = True
        if completion.outcome is not EffectOutcome.REJECTED:
            changed |= self._submit_pending()
        if changed:
            self.redraw()
