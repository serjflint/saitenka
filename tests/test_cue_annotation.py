from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future

import pytest
from saitenka_tokenize.japanese import Token

from saitenka.app.features.annotation.jobs import (
    AnnotationExecutionResult,
    AnnotationExecutor,
    AnnotationInputs,
    AnnotationPriority,
    AnnotationWorkKey,
    CueAnnotationCoordinator,
    CueIdentity,
    annotate,
)
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome
from saitenka.runtime.jobs import JobLanePolicy, LocalJobLane


def _identity(text: str, *, start: float = 1.0) -> CueIdentity:
    return CueIdentity(1, 2, "ja", text, start, start + 1)


def _key(text: str) -> AnnotationWorkKey:
    return AnnotationWorkKey(text, 1, 2, "ja", 3, 4)


class _Tokenizer:
    name = "test"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def tokenize(self, line: str, *, strip_furigana: bool = True, merge: bool = True):
        del strip_furigana, merge
        self.calls.append(line)
        return [Token(line, line, line, "名詞", 0, len(line))]

    def merge_dict_compounds(self, tokens, exists):
        exists(tuple(token.surface for token in tokens))
        return tokens


class _Scorer:
    def score_line(self, tokens):
        return [f"style:{token.surface}" for token in tokens]


class _BlockingTokenizer(_Tokenizer):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def tokenize(self, line: str, *, strip_furigana: bool = True, merge: bool = True):
        self.started.set()
        assert self.release.wait(2)
        try:
            return super().tokenize(line, strip_furigana=strip_furigana, merge=merge)
        finally:
            self.finished.set()


class _FailingTokenizer(_Tokenizer):
    def tokenize(self, line: str, *, strip_furigana: bool = True, merge: bool = True):
        del strip_furigana, merge
        self.calls.append(line)
        raise RuntimeError("secret subtitle text")


def _exists(_forms):
    return set()


def _inputs(text: str, tokenizer=None, *, scorer=None, exists=_exists) -> AnnotationInputs:
    return AnnotationInputs(text, tokenizer or _Tokenizer(), exists, scorer)


class _ObservedWarm(Future[None]):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()

    def result(self, timeout: float | None = None) -> None:
        self.entered.set()
        return super().result(timeout)


def test_retiring_episode_work_drops_a_late_result_instead_of_caching_it() -> None:
    tokenizer = _Tokenizer()
    executor = AnnotationExecutor()
    admitted = deque()

    def submit(**kwargs) -> bool:
        admitted.append(kwargs)
        return True

    coordinator = CueAnnotationCoordinator(executor=executor, submitter=submit)
    key = _key("old episode")
    coordinator.submit(
        key,
        _inputs("old episode", tokenizer),
        priority=AnnotationPriority.EPISODE,
    )

    coordinator.cancel_priority(AnnotationPriority.EPISODE)
    old = admitted.popleft()
    old["on_finished"](
        EffectFinished(
            EffectId(1),
            old["owner"],
            old["identity"],
            EffectOutcome.SUCCEEDED,
            result=executor.run(old["request"], threading.Event()),
        )
    )

    assert coordinator.cached(key) is None
    assert coordinator.pending_count() == 0
    coordinator.close()


def test_current_request_reclaims_cancelled_same_key_episode_work() -> None:
    tokenizer = _Tokenizer()
    executor = AnnotationExecutor()
    admitted = deque()
    results = deque()

    def submit(**kwargs) -> bool:
        admitted.append(kwargs)
        return True

    coordinator = CueAnnotationCoordinator(
        executor=executor,
        submitter=submit,
        on_result=results.append,
    )
    key = _key("shared cue")
    inputs = _inputs("shared cue", tokenizer)
    coordinator.submit(key, inputs, priority=AnnotationPriority.EPISODE)
    coordinator.cancel_priority(AnnotationPriority.EPISODE)
    coordinator.submit(
        key,
        inputs,
        priority=AnnotationPriority.CURRENT,
        waiter=_identity("shared cue"),
    )

    shared = admitted.popleft()
    shared["on_finished"](
        EffectFinished(
            EffectId(1),
            shared["owner"],
            shared["identity"],
            EffectOutcome.SUCCEEDED,
            result=executor.run(shared["request"], threading.Event()),
        )
    )

    assert results.popleft().cue is coordinator.cached(key)
    coordinator.close()


def _coordinator(**kwargs):
    results = deque()
    warm = kwargs.pop("tokenizer_warm", None)
    executor = AnnotationExecutor(warm)
    submitter = LocalJobLane("cue-annotation", JobLanePolicy(capacity=1), executor.run)
    coordinator = CueAnnotationCoordinator(
        **kwargs,
        executor=executor,
        submitter=submitter.submit,
        on_result=results.append,
    )
    return coordinator, submitter, results


def _wait_result(results):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if results:
            return results.popleft()
        time.sleep(0.001)
    raise AssertionError("annotation result did not arrive")


def _wait_calls(tokenizer, count: int) -> list[str]:
    """Block until the workers have recorded ``count`` tokenize calls, then return them.

    A result arriving says its own job finished, not that every job has. Reading `calls` off the
    back of one job's completion asserts an ordering against a list another thread is still
    appending to, which fails as `['current'] == ['current', 'episode']` whenever the second worker
    is a moment behind.
    """
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if len(tokenizer.calls) >= count:
            return tokenizer.calls[:count]
        time.sleep(0.001)
    raise AssertionError(f"expected {count} tokenize calls, saw {tokenizer.calls}")


def test_settled_no_dictionary_still_tokenizes_and_scores_without_attestation():
    tokenizer = _Tokenizer()

    cue = annotate(_inputs("猫", tokenizer, scorer=_Scorer(), exists=None))

    assert tokenizer.calls == ["猫"]
    assert [token.surface for token in cue.tokens] == ["猫"]
    assert cue.styles == ["style:猫"]


def test_rejected_broker_admission_finishes_current_waiter() -> None:
    results = deque()
    coordinator = CueAnnotationCoordinator(
        submitter=lambda **_kwargs: False,
        on_result=results.append,
    )

    coordinator.submit(
        _key("猫"),
        _inputs("猫"),
        priority=AnnotationPriority.CURRENT,
        waiter=_identity("猫"),
    )

    result = results.popleft()
    assert isinstance(result.error, RuntimeError)
    assert coordinator.pending_count() == 0


def test_blocking_adapter_resolves_through_the_runtime_broker() -> None:
    coordinator, submitter, _results = _coordinator()

    try:
        cue = coordinator.resolve(
            _key("猫"),
            _inputs("猫"),
            priority=AnnotationPriority.CURRENT,
        )

        assert [token.surface for token in cue.tokens] == ["猫"]
        assert coordinator.cached(_key("猫")) is cue
    finally:
        coordinator.close()
        submitter.close()


@pytest.mark.timeout(5)
def test_blocking_adapter_timeout_bounds_the_whole_operation() -> None:
    tokenizer = _BlockingTokenizer()
    coordinator, submitter, _results = _coordinator()
    try:
        with pytest.raises(TimeoutError, match="did not complete"):
            coordinator.resolve(
                _key("猫"),
                _inputs("猫", tokenizer),
                priority=AnnotationPriority.LOOKAHEAD,
                timeout=0.01,
            )
        # Bounded wait, not is_set(): the claim is that the timeout bounded work that had really
        # begun, not that the worker had been scheduled within the 10ms the timeout allows. Under a
        # loaded gate the submitter thread can dequeue a moment later, which is not the bug this
        # asserts against.
        assert tokenizer.started.wait(2)
    finally:
        tokenizer.release.set()
        coordinator.close()
        submitter.close()


@pytest.mark.timeout(5)
def test_blocking_lookahead_cannot_overtake_an_admitted_current_job() -> None:
    tokenizer = _Tokenizer()
    executor = AnnotationExecutor()
    admitted = deque()

    def submit(**kwargs) -> bool:
        admitted.append(kwargs)
        return True

    coordinator = CueAnnotationCoordinator(executor=executor, submitter=submit)
    coordinator.submit(
        _key("current"),
        _inputs("current", tokenizer),
        priority=AnnotationPriority.CURRENT,
        waiter=_identity("current"),
    )
    resolved = []
    thread = threading.Thread(
        target=lambda: resolved.append(
            coordinator.resolve(
                _key("lookahead"),
                _inputs("lookahead", tokenizer),
                priority=AnnotationPriority.LOOKAHEAD,
                timeout=2,
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 1
    while coordinator.pending_count() < 2 and time.monotonic() < deadline:
        time.sleep(0.001)

    assert len(admitted) == 1
    current = admitted.popleft()
    current["on_finished"](
        EffectFinished(
            EffectId(1),
            current["owner"],
            current["identity"],
            EffectOutcome.SUCCEEDED,
            result=executor.run(current["request"], threading.Event()),
        )
    )
    lookahead = admitted.popleft()
    lookahead["on_finished"](
        EffectFinished(
            EffectId(2),
            lookahead["owner"],
            lookahead["identity"],
            EffectOutcome.SUCCEEDED,
            result=executor.run(lookahead["request"], threading.Event()),
        )
    )
    thread.join(2)

    assert tokenizer.calls == ["current", "lookahead"]
    assert [token.surface for token in resolved[0].tokens] == ["lookahead"]
    coordinator.close()


def test_non_success_terminal_result_does_not_poison_the_cache() -> None:
    tokenizer = _Tokenizer()
    executor = AnnotationExecutor()
    results = deque()
    attempt = 0

    def submit(**kwargs) -> bool:
        nonlocal attempt
        attempt += 1
        execution = executor.run(kwargs["request"], threading.Event())
        assert isinstance(execution, AnnotationExecutionResult)
        kwargs["on_finished"](
            EffectFinished(
                EffectId(attempt),
                kwargs["owner"],
                kwargs["identity"],
                EffectOutcome.CANCELLED if attempt == 1 else EffectOutcome.SUCCEEDED,
                result=execution,
            )
        )
        return True

    coordinator = CueAnnotationCoordinator(
        executor=executor,
        submitter=submit,
        on_result=results.append,
    )
    key = _key("猫")
    coordinator.submit(
        key,
        _inputs("猫", tokenizer),
        priority=AnnotationPriority.CURRENT,
        waiter=_identity("猫"),
    )
    assert isinstance(results.popleft().error, RuntimeError)
    assert coordinator.cached(key) is None

    coordinator.submit(
        key,
        _inputs("猫", tokenizer),
        priority=AnnotationPriority.CURRENT,
        waiter=_identity("猫", start=2),
    )
    assert results.popleft().error is None
    assert coordinator.cached(key) is not None
    assert tokenizer.calls == ["猫", "猫"]
    coordinator.close()


@pytest.mark.timeout(5)
def test_rejected_job_does_not_consume_tokenizer_warm() -> None:
    warm = _ObservedWarm()
    tokenizer = _Tokenizer()
    executor = AnnotationExecutor(warm)
    serial = LocalJobLane("cue-annotation", JobLanePolicy(capacity=1), executor.run)
    results = deque()
    reject = True

    def submit(**kwargs) -> bool:
        nonlocal reject
        if reject:
            reject = False
            return False
        return serial.submit(**kwargs)

    coordinator = CueAnnotationCoordinator(
        executor=executor,
        submitter=submit,
        on_result=results.append,
    )
    try:
        coordinator.submit(
            _key("first"),
            _inputs("first", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("first"),
        )
        assert isinstance(results.popleft().error, RuntimeError)

        coordinator.submit(
            _key("second"),
            _inputs("second", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("second"),
        )
        assert warm.entered.wait(1)
        assert tokenizer.calls == []
        warm.set_result(None)

        assert _wait_result(results).error is None
        assert tokenizer.calls == ["second"]
    finally:
        if not warm.done():
            warm.set_result(None)
        coordinator.close()
        serial.close()


@pytest.mark.timeout(5)
def test_failed_tokenizer_warm_is_consumed_before_the_next_cue():
    warm = _ObservedWarm()
    tokenizer = _Tokenizer()
    coordinator, submitter, results = _coordinator(tokenizer_warm=warm)
    try:
        coordinator.submit(
            _key("first"),
            _inputs("first", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("first"),
        )
        assert warm.entered.wait(1)
        assert tokenizer.calls == []
        warm.set_exception(RuntimeError("warm failed"))
        assert isinstance(_wait_result(results).error, RuntimeError)

        coordinator.submit(
            _key("second"),
            _inputs("second", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("second"),
        )
        recovered = _wait_result(results)

        assert recovered.error is None
        assert [token.surface for token in recovered.cue.tokens] == ["second"]
        assert tokenizer.calls == ["second"]
    finally:
        if not warm.done():
            warm.set_result(None)
        coordinator.close()
        submitter.close()


@pytest.mark.timeout(5)
def test_running_duplicate_attaches_newest_current_waiter_and_executes_once():
    tokenizer = _BlockingTokenizer()
    coordinator, submitter, results = _coordinator()
    key = _key("猫")
    old = _identity("猫", start=1)
    current = _identity("猫", start=3)
    try:
        coordinator.submit(
            key,
            _inputs("猫", tokenizer),
            priority=AnnotationPriority.EPISODE,
        )
        assert tokenizer.started.wait(1)

        coordinator.submit(
            key,
            _inputs("猫", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=old,
        )
        coordinator.submit(
            key,
            _inputs("猫", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=current,
        )
        coordinator.submit(key, _inputs("猫", tokenizer), priority=AnnotationPriority.LOOKAHEAD)
        tokenizer.release.set()

        result = _wait_result(results)
        assert result.identity == current
        assert [token.surface for token in result.cue.tokens] == ["猫"]
        assert tokenizer.calls == ["猫"]
    finally:
        tokenizer.release.set()
        coordinator.close()
        submitter.close()


@pytest.mark.timeout(5)
def test_current_work_precedes_an_already_queued_episode_job():
    blocker = _BlockingTokenizer()
    tokenizer = _Tokenizer()
    coordinator, submitter, results = _coordinator()
    try:
        coordinator.submit(
            _key("block"),
            _inputs("block", blocker),
            priority=AnnotationPriority.EPISODE,
        )
        assert blocker.started.wait(1)
        coordinator.submit(
            _key("episode"),
            _inputs("episode", tokenizer),
            priority=AnnotationPriority.EPISODE,
        )
        coordinator.submit(
            _key("current"),
            _inputs("current", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("current"),
        )

        blocker.release.set()
        result = _wait_result(results)
        assert result.identity == _identity("current")
        assert _wait_calls(tokenizer, 2) == ["current", "episode"]
    finally:
        blocker.release.set()
        coordinator.close()
        submitter.close()


@pytest.mark.timeout(5)
def test_failed_identity_completes_once_without_a_retry_loop():
    tokenizer = _FailingTokenizer()
    coordinator, submitter, results = _coordinator()
    try:
        coordinator.submit(
            _key("猫"),
            _inputs("猫", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("猫"),
        )

        result = _wait_result(results)
        assert result.cue is None and isinstance(result.error, RuntimeError)
        assert tokenizer.calls == ["猫"]
        assert results == deque()
    finally:
        coordinator.close()
        submitter.close()


@pytest.mark.timeout(5)
def test_blocked_worker_cannot_publish_after_bounded_close():
    tokenizer = _BlockingTokenizer()
    coordinator, submitter, results = _coordinator()
    coordinator.submit(
        _key("猫"),
        _inputs("猫", tokenizer),
        priority=AnnotationPriority.CURRENT,
        waiter=_identity("猫"),
    )
    assert tokenizer.started.wait(1)

    coordinator.close(timeout=0.01)
    tokenizer.release.set()
    assert tokenizer.finished.wait(1)
    submitter.close()

    assert results == deque()
