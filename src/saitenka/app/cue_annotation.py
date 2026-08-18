"""Brokered cue annotation with identity-qualified publication."""

from __future__ import annotations

import heapq
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Protocol

from saitenka import otel_metrics
from saitenka.app.token_cache import TokenizedCue
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

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


@dataclass(frozen=True, slots=True)
class AnnotationExecution:
    key: AnnotationWorkKey
    inputs: AnnotationInputs
    priority: AnnotationPriority
    queued_at: float


@dataclass(frozen=True, slots=True)
class AnnotationExecutionResult:
    cue: TokenizedCue | None
    failed: bool
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


def _published_result(
    key: AnnotationWorkKey,
    job: _Job,
    completion: EffectFinished,
) -> tuple[TokenizedCue | None, AnnotationResult | None]:
    execution = (
        completion.result if isinstance(completion.result, AnnotationExecutionResult) else None
    )
    if (
        execution is not None
        and not execution.failed
        and completion.outcome is EffectOutcome.SUCCEEDED
    ):
        succeeded = True
        cue = execution.cue
    else:
        succeeded = False
        cue = None
    if job.waiter is None:
        return cue, None
    return cue, AnnotationResult(
        key,
        job.waiter,
        cue,
        None if succeeded else RuntimeError("cue annotation failed"),
        execution.queue_wait_ms if execution is not None else 0.0,
        execution.work_ms if execution is not None else 0.0,
    )


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


class AnnotationExecutor:
    """Executes broker-admitted annotation work."""

    def __init__(
        self,
        tokenizer_warm: Future[None] | None = None,
    ) -> None:
        self._tokenizer_warm = tokenizer_warm
        self._closed = threading.Event()

    def run(self, request: object, cancelled: threading.Event) -> object:
        if not isinstance(request, AnnotationExecution):
            raise TypeError("invalid cue-annotation request")
        if self._closed.is_set() or cancelled.is_set():
            return None
        return self._execute(request)

    def close(self) -> None:
        self._closed.set()

    def _execute(self, request: AnnotationExecution) -> AnnotationExecutionResult:
        started = time.monotonic()
        with otel_metrics.traced(
            "cue_annotation",
            phase="work",
            priority=request.priority.name.lower(),
            chars=str(len(request.inputs.normalized_text)),
        ) as span:
            try:
                queue_wait_ms = (started - request.queued_at) * 1_000
                span.set("queue_wait_ms", round(queue_wait_ms, 3))
                warm = self._tokenizer_warm
                self._tokenizer_warm = None
                if warm is not None:
                    warm.result()
                cue = annotate(request.inputs)
                finished = time.monotonic()
                span.set("token_count", len(cue.tokens))
                span.set("outcome", "computed")
                return AnnotationExecutionResult(
                    cue=cue,
                    failed=False,
                    queue_wait_ms=queue_wait_ms,
                    work_ms=(finished - started) * 1_000,
                )
            except Exception:  # noqa: BLE001 -- annotation failure degrades to plain subtitles
                finished = time.monotonic()
                span.set("failure", "annotation-error")
                span.set("outcome", "failed")
                return AnnotationExecutionResult(
                    cue=None,
                    failed=True,
                    queue_wait_ms=(started - request.queued_at) * 1_000,
                    work_ms=(finished - started) * 1_000,
                )


def configure_runtime_job(ipc, executor: AnnotationExecutor) -> JobSubmitter | None:
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(
        "cue-annotation",
        JobLanePolicy(capacity=1),
        executor.run,
    ):
        return None
    return ipc.submit_runtime_job


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
    """Event-thread priority/cache state; JobBroker owns execution and lifetime."""

    def __init__(
        self,
        *,
        cache_max: int = 512,
        tokenizer_warm: Future[None] | None = None,
        executor: AnnotationExecutor | None = None,
        submitter: JobSubmitter | None = None,
        on_result: Callable[[AnnotationResult], None] | None = None,
    ) -> None:
        self._condition = threading.Condition()
        self._jobs: dict[AnnotationWorkKey, _Job] = {}
        self._heap: list[tuple[int, int, AnnotationWorkKey, int]] = []
        self._cache: dict[AnnotationWorkKey, TokenizedCue] = {}
        self._cache_order: deque[AnnotationWorkKey] = deque()
        self._cache_max = max(1, cache_max)
        self._sequence = 0
        self._closed = False
        self._executor = executor or AnnotationExecutor(tokenizer_warm)
        self._submitter = submitter
        self._on_result = on_result
        self._inflight: AnnotationWorkKey | None = None

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
                return None
            self._sequence += 1
            job = _Job(key, inputs, priority, 0, time.monotonic(), waiter)
            self._jobs[key] = job
            heapq.heappush(self._heap, (int(priority), self._sequence, key, 0))
            dispatch = self._inflight is None
        if dispatch:
            self._dispatch_next()
        return None

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
        drive: Callable[[float | None], None] | None = None,
    ) -> TokenizedCue:
        """Blocking adapter for demo/screenshot and geometry workers."""
        cached = self.submit(key, inputs, priority=priority)
        if cached is not None:
            return cached
        deadline = None if timeout is None else time.monotonic() + timeout
        return self._wait_for_result(key, deadline, drive)

    def _wait_for_result(
        self,
        key: AnnotationWorkKey,
        deadline: float | None,
        drive: Callable[[float | None], None] | None,
    ) -> TokenizedCue:
        while True:
            with self._condition:
                if key not in self._jobs or self._closed:
                    cached = self._cache.get(key)
                    if cached is None:
                        raise RuntimeError("cue annotation failed")
                    return cached
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("cue annotation did not complete")
                if drive is None:
                    self._condition.wait(remaining)
                    continue
            drive(remaining)

    def close(self, timeout: float = 1.0) -> None:
        del timeout
        with self._condition:
            self._closed = True
            self._heap.clear()
            self._jobs.clear()
            self._inflight = None
            self._condition.notify_all()
        self._executor.close()

    def _next_job(self) -> _Job | None:
        while self._heap:
            _priority, _sequence, key, version = heapq.heappop(self._heap)
            job = self._jobs.get(key)
            if job is not None and job.version == version:
                return job
        return None

    def _dispatch_next(self) -> None:
        with self._condition:
            if self._closed or self._inflight is not None:
                return
            job = self._next_job()
            if job is None:
                return
            self._inflight = job.key
            request = AnnotationExecution(job.key, job.inputs, job.priority, job.queued_at)
        submitter = self._submitter
        if submitter is None:
            self._finish(
                EffectFinished(
                    EffectId(0),
                    Owner.SUBTITLE,
                    job.key,
                    EffectOutcome.REJECTED,
                )
            )
            return
        accepted = submitter(
            owner=Owner.SUBTITLE,
            identity=job.key,
            lane="cue-annotation",
            request=request,
            on_finished=self._finish,
        )
        with self._condition:
            still_inflight = self._inflight == job.key
        if not accepted and still_inflight:
            self._finish(
                EffectFinished(
                    EffectId(0),
                    Owner.SUBTITLE,
                    job.key,
                    EffectOutcome.REJECTED,
                )
            )

    def _finish(self, completion: EffectFinished) -> None:
        annotation_result: AnnotationResult | None = None
        with self._condition:
            key = completion.identity
            if not isinstance(key, AnnotationWorkKey) or self._inflight != key:
                return
            self._inflight = None
            current = self._jobs.pop(key, None)
            if self._closed or current is None:
                return
            cue, annotation_result = _published_result(key, current, completion)
            if cue is not None and cue.tokens:
                self._cache_put(key, cue)
            self._condition.notify_all()
        if annotation_result is not None and self._on_result is not None:
            self._on_result(annotation_result)
        self._dispatch_next()

    def _cache_put(self, key: AnnotationWorkKey, cue: TokenizedCue) -> None:
        self._cache[key] = cue
        self._cache_order.append(key)
        while len(self._cache) > self._cache_max:
            oldest = self._cache_order.popleft()
            self._cache.pop(oldest, None)
