"""Bounded owner of cue annotation identity, work, cache, and degradation."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.features.annotation import jobs as cue_annotation
from saitenka.app.token_cache import TokenCache, TokenizedCue, cue_key

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Future

    from saitenka_subtitles import CueIndex
    from saitenka_tokenize.registry import Tokenizer

    from saitenka.app import subtitle_intents
    from saitenka.app.scoring import Coloring, TokenStyle
    from saitenka.mpvio.ipc import MpvIPC

log = logging.getLogger(__name__)


class AnnotationOutcome(StrEnum):
    EMPTY = "empty"
    SECONDARY = "secondary"
    CACHED = "cached"
    TOKENIZED = "tokenized"
    PENDING = "pending"
    PUBLISHED = "published"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class AnnotationInputs:
    """Volatile cue and dependency facts captured together at admission."""

    source_epoch: int
    track_identity: object
    subtitle_role: str
    observed_start: object
    observed_end: object
    source_order: int | None
    tokenizer: Tokenizer
    terms_exist: Callable[[Sequence[str]], set[str]] | None
    scorer: Coloring | None
    selected_dictionaries: int
    dependencies_ready: bool
    annotate: bool


@dataclass(frozen=True, slots=True)
class AnnotationTransition:
    outcome: AnnotationOutcome
    identity: cue_annotation.CueIdentity | None = None
    cue: TokenizedCue | None = None
    publish: bool = False
    schedule_geometry: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationView:
    """Immutable facts other features may observe."""

    mode: subtitle_intents.AnnotationMode
    hover_revealed: bool
    pending_text: str | None
    degraded: bool
    identity: cue_annotation.CueIdentity | None
    retired: bool
    async_enabled: bool
    dependencies_settled: bool
    dependency_generation: int


class CueAnnotationController:
    """One writer for annotation admission, work identity, cache, and publication policy."""

    def __init__(
        self,
        ipc: MpvIPC,
        *,
        mode: subtitle_intents.AnnotationMode,
        cache_max: int,
        tokenizer_warm: Future[None] | None = None,
    ) -> None:
        if mode not in {"full", "hover"}:
            raise ValueError(f"unknown annotation mode: {mode!r}")
        self._mode: subtitle_intents.AnnotationMode = mode
        self._hover_revealed = False
        self._token_cache = TokenCache(cache_max)
        self._state_lock = threading.RLock()
        self._executor = cue_annotation.AnnotationExecutor(tokenizer_warm)
        self._submitter = cue_annotation.configure_runtime_job(ipc, self._executor)
        self._coordinator: cue_annotation.CueAnnotationCoordinator | None = None
        self._cache_max = cache_max
        self._async_enabled = False
        self._dependencies_settled = True
        self._dependency_generation = 0
        self._identity: cue_annotation.CueIdentity | None = None
        self._current_inputs: AnnotationInputs | None = None
        self._prepared: tuple[str, TokenizedCue, int] | None = None
        self._retired = True
        self._pending_text: str | None = None
        self._degraded = False
        self._publications: deque[AnnotationTransition] = deque()
        self._publications_lock = threading.Lock()
        self._episode_index: CueIndex | None = None
        self._episode_cursor = 0
        self._episode_inputs: AnnotationInputs | None = None
        self._episode_lease = 0
        self._warm_cancel = threading.Event()
        self._closed = threading.Event()

    @property
    def view(self) -> AnnotationView:
        return AnnotationView(
            mode=self._mode,
            hover_revealed=self._hover_revealed,
            pending_text=self._pending_text,
            degraded=self._degraded,
            identity=self._identity,
            retired=self._retired,
            async_enabled=self._async_enabled,
            dependencies_settled=self._dependencies_settled,
            dependency_generation=self._dependency_generation,
        )

    def set_mode(self, mode: subtitle_intents.AnnotationMode) -> None:
        if mode != self._mode:
            self._hover_revealed = False
        self._mode = mode

    def set_hover_revealed(self, *, revealed: bool) -> None:
        self._hover_revealed = revealed

    def enable_async(self) -> None:
        self._async_enabled = True
        self._dependencies_settled = False
        self._ensure_coordinator()

    def replace(self, text: str, inputs: AnnotationInputs) -> AnnotationTransition:
        """Admit the current cue after the shell has retired cross-feature presentation."""
        self._retire_local()
        norm = cue_key(text)
        if not norm.strip():
            return AnnotationTransition(AnnotationOutcome.EMPTY)
        identity = self._identity_for(norm, inputs)
        self._identity = identity
        self._current_inputs = inputs
        self._retired = False
        if not inputs.annotate:
            return AnnotationTransition(AnnotationOutcome.SECONDARY, identity)
        prepared = self._prepared
        self._prepared = None
        if (
            prepared is not None
            and prepared[0] == norm
            and prepared[2] == self._dependency_generation
        ):
            self._pending_text = None if inputs.dependencies_ready else norm
            return AnnotationTransition(
                AnnotationOutcome.TOKENIZED,
                identity,
                prepared[1],
                publish=True,
                schedule_geometry=True,
            )
        cached = self._token_cache.get(norm)
        if cached is not None:
            return AnnotationTransition(
                AnnotationOutcome.CACHED,
                identity,
                cached,
                publish=True,
                schedule_geometry=True,
            )
        if self._async_enabled:
            self._pending_text = norm
            if self._dependencies_settled:
                published = self._submit_current(norm, inputs)
                if published is not None:
                    return published
            return AnnotationTransition(AnnotationOutcome.PENDING, identity)
        cue = self._tokenize(norm, inputs)
        self._pending_text = None if inputs.dependencies_ready else norm
        return AnnotationTransition(
            AnnotationOutcome.TOKENIZED,
            identity,
            cue,
            publish=True,
            schedule_geometry=True,
        )

    def prepare_blocking(
        self,
        text: str,
        inputs: AnnotationInputs,
        *,
        drive: Callable[[float | None], None],
    ) -> None:
        """Warm exactly the annotation the later cue replacement will consume."""
        self._async_enabled = True
        self._dependencies_settled = True
        coordinator = self._ensure_coordinator()
        norm = cue_key(text)
        with self._state_lock:
            cache_generation = self._token_cache.generation
            dependency_generation = self._dependency_generation
        if inputs.terms_exist is None:
            self._prepared = (
                norm,
                self._compute_cue(norm, inputs),
                dependency_generation,
            )
            return
        cue = coordinator.resolve(
            self._key_at(norm, inputs, cache_generation, dependency_generation),
            self._work_inputs(norm, inputs),
            priority=cue_annotation.AnnotationPriority.CURRENT,
            drive=drive,
        )
        self._token_cache.put(norm, cue, generation=cache_generation)

    def lookahead_captured(
        self,
        text: str,
        capture: Callable[[], AnnotationInputs],
    ) -> TokenizedCue:
        """Capture dependencies and their owner generations as one admission fact."""
        with self._state_lock:
            inputs = capture()
            cache_generation = self._token_cache.generation
            dependency_generation = self._dependency_generation
        return self._lookahead(
            text,
            inputs,
            cache_generation=cache_generation,
            dependency_generation=dependency_generation,
        )

    def captured_lookahead(
        self,
        capture: Callable[[], AnnotationInputs],
    ) -> Callable[[str], TokenizedCue]:
        """Bind the volatile-input capture seam without exposing owner internals."""
        return partial(self.lookahead_captured, capture=capture)

    def _lookahead(
        self,
        text: str,
        inputs: AnnotationInputs,
        *,
        cache_generation: int | None = None,
        dependency_generation: int | None = None,
    ) -> TokenizedCue:
        norm = cue_key(text)
        if cache_generation is None:
            cache_generation = self._token_cache.generation
        if dependency_generation is None:
            dependency_generation = self._dependency_generation
        if not self._async_enabled or inputs.terms_exist is None:
            return self._tokenize(norm, inputs, generation=cache_generation)
        return self._ensure_coordinator().resolve(
            self._key_at(norm, inputs, cache_generation, dependency_generation),
            self._work_inputs(norm, inputs),
            priority=cue_annotation.AnnotationPriority.LOOKAHEAD,
        )

    def dependencies_changed(
        self,
        text: str,
        inputs: AnnotationInputs,
    ) -> AnnotationTransition | None:
        with self._state_lock:
            self._dependency_generation += 1
            self._dependencies_settled = True
            self._degraded = False
            self._prepared = None
            self._token_cache.clear()
            self._cancel_episode_warm()
        norm = cue_key(text)
        if not norm.strip() or not inputs.annotate:
            return None
        self._pending_text = norm
        self._current_inputs = inputs
        return self._submit_current(norm, inputs) or AnnotationTransition(
            AnnotationOutcome.PENDING,
            self._identity,
        )

    def retokenize(self, text: str, inputs: AnnotationInputs) -> AnnotationTransition | None:
        norm = cue_key(text)
        if not norm.strip() or not inputs.annotate:
            return None
        if self._async_enabled:
            self.retire_cue()
            identity = self._identity_for(norm, inputs)
            self._identity = identity
            self._current_inputs = inputs
            self._retired = False
            self._pending_text = norm
            self._degraded = False
            return self._submit_current(norm, inputs) or AnnotationTransition(
                AnnotationOutcome.PENDING,
                identity,
            )
        identity = self._identity_for(norm, inputs)
        self._identity = identity
        self._current_inputs = inputs
        self._retired = False
        self._pending_text = None
        self._degraded = False
        cue = self._tokenize(norm, inputs)
        return AnnotationTransition(
            AnnotationOutcome.TOKENIZED,
            identity,
            cue,
            publish=True,
            schedule_geometry=True,
        )

    def retire_cue(self) -> bool:
        was_active = not self._retired
        self._retire_local()
        return was_active

    def invalidate_tokenizer(self) -> None:
        with self._state_lock:
            self._dependency_generation += 1
            self._prepared = None
            self._token_cache.clear()
            self._cancel_episode_warm()

    def retire_episode_warm(self) -> None:
        self._cancel_episode_warm()

    def start_episode_warm(self, index: CueIndex, inputs: AnnotationInputs) -> bool:
        if self._episode_index is index or self._closed.is_set():
            return False
        self._cancel_episode_warm()
        self._episode_index = index
        self._episode_inputs = inputs
        self._episode_cursor = 0
        if self._async_enabled:
            self._feed_episode()
            return True
        cancelled = self._warm_cancel
        generation = self._token_cache.generation
        threading.Thread(
            target=self._warm_episode,
            args=(index, inputs, generation, cancelled, self._episode_lease),
            name="saitenka-episode-warm",
            daemon=True,
        ).start()
        return True

    def settle(self) -> tuple[AnnotationTransition, ...]:
        self._feed_episode()
        with self._publications_lock:
            publications = tuple(self._publications)
            self._publications.clear()
        return publications

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._cancel_episode_warm()
        coordinator = self._coordinator
        if coordinator is None:
            self._executor.close()
        else:
            coordinator.close()

    def _ensure_coordinator(self) -> cue_annotation.CueAnnotationCoordinator:
        coordinator = self._coordinator
        if coordinator is None:
            coordinator = self._coordinator = cue_annotation.CueAnnotationCoordinator(
                cache_max=self._cache_max,
                executor=self._executor,
                submitter=self._submitter,
                on_result=self._finish,
            )
        return coordinator

    def _identity_for(
        self,
        norm: str,
        inputs: AnnotationInputs,
    ) -> cue_annotation.CueIdentity:
        return cue_annotation.CueIdentity(
            inputs.source_epoch,
            inputs.track_identity,
            inputs.subtitle_role,
            norm,
            inputs.observed_start,
            inputs.observed_end,
            inputs.source_order,
        )

    def _key(self, norm: str, inputs: AnnotationInputs) -> cue_annotation.AnnotationWorkKey:
        return self._key_at(
            norm,
            inputs,
            self._token_cache.generation,
            self._dependency_generation,
        )

    def _key_at(
        self,
        norm: str,
        inputs: AnnotationInputs,
        cache_generation: int,
        dependency_generation: int,
    ) -> cue_annotation.AnnotationWorkKey:
        identity = self._identity_for(norm, inputs)
        return cue_annotation.AnnotationWorkKey(
            norm,
            identity.source_epoch,
            identity.track_identity,
            identity.subtitle_role,
            cache_generation,
            dependency_generation,
        )

    @staticmethod
    def _work_inputs(norm: str, inputs: AnnotationInputs) -> cue_annotation.AnnotationInputs:
        return cue_annotation.AnnotationInputs(
            norm,
            inputs.tokenizer,
            inputs.terms_exist,
            inputs.scorer,
            inputs.selected_dictionaries,
        )

    def _submit_current(
        self,
        norm: str,
        inputs: AnnotationInputs,
    ) -> AnnotationTransition | None:
        coordinator = self._coordinator
        if coordinator is None or not inputs.annotate:
            return None
        identity = self._identity_for(norm, inputs)
        self._identity = identity
        self._current_inputs = inputs
        self._retired = False
        cached = coordinator.submit(
            self._key(norm, inputs),
            self._work_inputs(norm, inputs),
            priority=cue_annotation.AnnotationPriority.CURRENT,
            waiter=identity,
        )
        if cached is None:
            return None
        self._pending_text = None
        return AnnotationTransition(
            AnnotationOutcome.CACHED,
            identity,
            cached,
            publish=True,
            schedule_geometry=True,
        )

    def _finish(self, result: cue_annotation.AnnotationResult) -> None:
        with otel_metrics.traced("cue_annotation", phase="publish") as span:
            span.set("queue_wait_ms", round(result.queue_wait_ms, 3))
            span.set("work_ms", round(result.work_ms, 3))
            outcome = cue_annotation.disposition(
                result,
                current_identity=self._identity,
                current_key=(
                    self._key(result.identity.normalized_text, self._current_inputs)
                    if result.identity is not None and self._current_inputs is not None
                    else None
                ),
                cue_retired=self._retired,
                pending_text=self._pending_text,
            )
            publication: AnnotationTransition | None = None
            if outcome is cue_annotation.AnnotationDisposition.DEGRADE:
                self._pending_text = None
                self._degraded = True
                publication = AnnotationTransition(AnnotationOutcome.DEGRADED, self._identity)
                log.warning("cue annotation unavailable; keeping plain subtitles")
            elif outcome is cue_annotation.AnnotationDisposition.PUBLISH:
                assert result.cue is not None and result.identity is not None
                self._token_cache.put(
                    result.identity.normalized_text,
                    result.cue,
                    complete=result.complete,
                    generation=result.key.token_cache_generation,
                )
                self._pending_text = None
                self._degraded = False
                publication = AnnotationTransition(
                    AnnotationOutcome.PUBLISHED,
                    result.identity,
                    result.cue,
                    publish=True,
                    schedule_geometry=True,
                )
            if outcome.failed:
                span.set("outcome", "failed")
                span.set("failure", "annotation-error")
            else:
                span.set("outcome", outcome.value)
            if publication is not None:
                with self._publications_lock:
                    self._publications.append(publication)

    def _tokenize(
        self,
        norm: str,
        inputs: AnnotationInputs,
        *,
        generation: int | None = None,
    ) -> TokenizedCue:
        cue = self._compute_cue(norm, inputs)
        self._token_cache.put(
            norm,
            cue,
            complete=inputs.terms_exist is not None,
            generation=generation,
        )
        return cue

    @staticmethod
    def _compute_cue(norm: str, inputs: AnnotationInputs) -> TokenizedCue:
        with otel_metrics.traced("tokenize_line", chars=str(len(norm))):
            raw = (inputs.tokenizer.tokenize(line) for line in norm.split("\n") if line.strip())
            lines = [
                inputs.tokenizer.merge_dict_compounds(tokens, inputs.terms_exist)
                if inputs.terms_exist
                else tokens
                for tokens in raw
            ]
        tokens = [token for line in lines for token in line]
        with otel_metrics.traced("score_line"):
            styles: list[TokenStyle] | None = (
                inputs.scorer.score_line(tokens) if inputs.scorer else None
            )
        return TokenizedCue(lines, tokens, styles)

    def _feed_episode(self) -> None:
        coordinator = self._coordinator
        index = self._episode_index
        inputs = self._episode_inputs
        if coordinator is None or index is None or inputs is None:
            return
        while coordinator.pending_count() < 4 and self._episode_cursor < len(index.cues):
            cue = index.cues[self._episode_cursor]
            self._episode_cursor += 1
            norm = cue_key(cue.text)
            coordinator.submit(
                self._key(norm, inputs),
                self._work_inputs(norm, inputs),
                priority=cue_annotation.AnnotationPriority.EPISODE,
            )

    def _warm_episode(
        self,
        index: CueIndex,
        inputs: AnnotationInputs,
        generation: int,
        cancelled: threading.Event,
        lease: int,
    ) -> None:
        warmed = 0
        for cue in list(index.cues):
            if (
                cancelled.is_set()
                or self._closed.is_set()
                or self._token_cache.generation != generation
            ):
                return
            try:
                norm = cue_key(cue.text)
                tokenized = self._compute_cue(norm, inputs)
                with self._state_lock:
                    current = self._episode_lease == lease and self._episode_index is index
                if cancelled.is_set() or not current:
                    return
                self._token_cache.put(
                    norm,
                    tokenized,
                    complete=inputs.terms_exist is not None,
                    generation=generation,
                )
                warmed += 1
            except Exception:
                log.debug("episode token warm failed for a cue", exc_info=True)
        log.info("episode token warm: %d/%d cues into the token cache", warmed, len(index.cues))

    def _cancel_episode_warm(self) -> None:
        self._episode_lease += 1
        self._warm_cancel.set()
        self._warm_cancel = threading.Event()
        self._episode_index = None
        self._episode_inputs = None
        self._episode_cursor = 0
        coordinator = self._coordinator
        if coordinator is not None:
            coordinator.cancel_priority(cue_annotation.AnnotationPriority.EPISODE)

    def _retire_local(self) -> None:
        self._retired = True
        self._identity = None
        self._current_inputs = None
        self._pending_text = None
        self._degraded = False
        self._hover_revealed = False
