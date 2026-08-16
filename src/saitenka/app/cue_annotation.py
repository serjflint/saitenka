"""Single-worker cue annotation with identity-qualified publication.

The worker owns tokenizer, dictionary-attestation, and scoring work. It never touches Reader,
renderers, or mpv IPC; the Reader poll loop applies completed results.
"""

from __future__ import annotations

import heapq
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from saitenka import otel_metrics
from saitenka.app.token_cache import TokenizedCue

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Future

    from saitenka.app.tokenizer import Tokenizer


class AnnotationPriority(IntEnum):
    CURRENT = 0
    LOOKAHEAD = 1
    EPISODE = 2


@dataclass(frozen=True, slots=True)
class CueIdentity:
    source_epoch: int
    track_identity: object
    subtitle_role: str
    normalized_text: str
    observed_start: object
    observed_end: object
    source_order: int | None = None


@dataclass(frozen=True, slots=True)
class AnnotationWorkKey:
    normalized_text: str
    source_epoch: int
    track_identity: object
    subtitle_role: str
    token_cache_generation: int
    dependency_generation: int


@dataclass(frozen=True, slots=True)
class AnnotationInputs:
    normalized_text: str
    tokenizer: Tokenizer
    terms_exist: Callable[[Sequence[str]], set[str]] | None
    scorer: Any | None
    selected_dictionaries: int = 0


@dataclass(frozen=True, slots=True)
class AnnotationResult:
    key: AnnotationWorkKey
    identity: CueIdentity | None
    cue: TokenizedCue | None
    error: Exception | None
    queue_wait_ms: float
    work_ms: float


@dataclass(slots=True)
class _Job:
    key: AnnotationWorkKey
    inputs: AnnotationInputs
    priority: AnnotationPriority
    version: int
    queued_at: float
    waiter: CueIdentity | None


def annotate(inputs: AnnotationInputs) -> TokenizedCue:
    raw = (
        inputs.tokenizer.tokenize(line)
        for line in inputs.normalized_text.split("\n")
        if line.strip()
    )
    if inputs.terms_exist is None:
        lines = list(raw)
    else:
        lookup_terms = inputs.terms_exist

        def terms_exist(forms: Sequence[str]) -> set[str]:
            keys = tuple(dict.fromkeys(form for form in forms if form))
            with otel_metrics.traced(
                "dictionary_attestation",
                requested_forms=str(len(keys)),
                selected_dictionaries=str(inputs.selected_dictionaries),
            ) as span:
                found = lookup_terms(keys)
                span.set("hit_count", len(found))
                return found

        lines = [inputs.tokenizer.merge_dict_compounds(tokens, terms_exist) for tokens in raw]
    tokens = [token for line in lines for token in line]
    styles = inputs.scorer.score_line(tokens) if inputs.scorer is not None else None
    return TokenizedCue(lines, tokens, styles)


class CueAnnotationCoordinator:
    """Priority/dedup scheduler with newest-current waiters and bounded close."""

    def __init__(
        self,
        *,
        cache_max: int = 512,
        tokenizer_warm: Future[None] | None = None,
    ) -> None:
        self._condition = threading.Condition()
        self._jobs: dict[AnnotationWorkKey, _Job] = {}
        self._heap: list[tuple[int, int, AnnotationWorkKey, int]] = []
        self._results: deque[AnnotationResult] = deque()
        self._cache: dict[AnnotationWorkKey, TokenizedCue] = {}
        self._cache_order: deque[AnnotationWorkKey] = deque()
        self._cache_max = max(1, cache_max)
        self._sequence = 0
        self._closed = False
        self._tokenizer_warm = tokenizer_warm
        self._thread = threading.Thread(
            target=self._run,
            name="saitenka-cue-annotation",
            daemon=True,
        )
        self._thread.start()

    def cached(self, key: AnnotationWorkKey) -> TokenizedCue | None:
        with self._condition:
            return self._cache.get(key)

    def submit(
        self,
        key: AnnotationWorkKey,
        inputs: AnnotationInputs,
        *,
        priority: AnnotationPriority,
        waiter: CueIdentity | None = None,
    ) -> TokenizedCue | None:
        with self._condition:
            cached = self._cache.get(key)
            if cached is not None:
                with otel_metrics.traced(
                    "cue_annotation",
                    phase="submit",
                    outcome="cache-hit",
                    priority=priority.name.lower(),
                    chars=str(len(inputs.normalized_text)),
                ):
                    pass
                return cached
            if self._closed:
                return None
            job = self._jobs.get(key)
            if job is not None:
                if waiter is not None:
                    job.waiter = waiter
                if priority < job.priority:
                    job.priority = priority
                    job.version += 1
                    self._sequence += 1
                    heapq.heappush(
                        self._heap,
                        (int(priority), self._sequence, key, job.version),
                    )
                    self._condition.notify()
                return None
            self._sequence += 1
            job = _Job(key, inputs, priority, 0, time.monotonic(), waiter)
            self._jobs[key] = job
            heapq.heappush(self._heap, (int(priority), self._sequence, key, 0))
            self._condition.notify()
            return None

    def drain(self) -> list[AnnotationResult]:
        with self._condition:
            results = list(self._results)
            self._results.clear()
            return results

    def pending_count(self) -> int:
        with self._condition:
            return len(self._jobs)

    def resolve(
        self,
        key: AnnotationWorkKey,
        inputs: AnnotationInputs,
        *,
        priority: AnnotationPriority,
        timeout: float | None = None,
    ) -> TokenizedCue:
        """Resolve annotation off the caller's critical thread through this worker."""
        cached = self.submit(key, inputs, priority=priority)
        if cached is not None:
            return cached
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while key in self._jobs and not self._closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("cue annotation did not complete")
                self._condition.wait(remaining)
            cached = self._cache.get(key)
            if cached is None:
                raise RuntimeError("cue annotation failed")
            return cached

    def close(self, timeout: float = 1.0) -> None:
        with self._condition:
            self._closed = True
            self._heap.clear()
            self._jobs.clear()
            self._results.clear()
            self._condition.notify_all()
        self._thread.join(timeout)

    def _next_job(self) -> _Job | None:
        while self._heap:
            _priority, _sequence, key, version = heapq.heappop(self._heap)
            job = self._jobs.get(key)
            if job is not None and job.version == version:
                return job
        return None

    def _run(self) -> None:
        while job := self._wait_for_job():
            started = time.monotonic()
            cue, error = self._execute(job, started)
            finished = time.monotonic()
            self._finish(job, cue, error, started, finished)

    def _wait_for_job(self) -> _Job | None:
        with self._condition:
            job = self._next_job()
            while job is None and not self._closed:
                self._condition.wait()
                job = self._next_job()
            return None if self._closed else job

    def _finish(
        self,
        job: _Job,
        cue: TokenizedCue | None,
        error: Exception | None,
        started: float,
        finished: float,
    ) -> None:
        with self._condition:
            current = self._jobs.pop(job.key, None)
            if self._closed or current is None:
                return
            if cue is not None and cue.tokens:
                self._cache[job.key] = cue
                self._cache_order.append(job.key)
                while len(self._cache) > self._cache_max:
                    oldest = self._cache_order.popleft()
                    self._cache.pop(oldest, None)
            if current.waiter is not None:
                self._results.append(
                    AnnotationResult(
                        job.key,
                        current.waiter,
                        cue,
                        error,
                        (started - job.queued_at) * 1_000,
                        (finished - started) * 1_000,
                    )
                )
            self._condition.notify_all()

    def _execute(self, job: _Job, started: float) -> tuple[TokenizedCue | None, Exception | None]:
        with otel_metrics.traced(
            "cue_annotation",
            phase="work",
            priority=job.priority.name.lower(),
            chars=str(len(job.inputs.normalized_text)),
        ) as span:
            try:
                span.set("queue_wait_ms", round((started - job.queued_at) * 1_000, 3))
                warm = self._tokenizer_warm
                if warm is not None:
                    self._tokenizer_warm = None
                    warm.result()
                cue = annotate(job.inputs)
                span.set("token_count", len(cue.tokens))
                span.set("outcome", "computed")
                return cue, None
            except Exception as error:  # noqa: BLE001  # worker failures degrade to plain subtitles
                span.set("failure", "annotation-error")
                span.set("outcome", "failed")
                return None, error
