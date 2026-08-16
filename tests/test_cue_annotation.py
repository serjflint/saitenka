from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import pytest

from saitenka.app.cue_annotation import (
    AnnotationInputs,
    AnnotationPriority,
    AnnotationWorkKey,
    CueAnnotationCoordinator,
    CueIdentity,
    annotate,
)
from saitenka.app.tokenize import Token


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


def _inputs(text: str, tokenizer=None, *, scorer=None, exists=None) -> AnnotationInputs:
    return AnnotationInputs(text, tokenizer or _Tokenizer(), exists, scorer)


def _wait_result(coordinator: CueAnnotationCoordinator):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if results := coordinator.drain():
            return results[0]
        time.sleep(0.001)
    raise AssertionError("annotation result did not arrive")


def test_settled_no_dictionary_still_tokenizes_and_scores_without_attestation():
    tokenizer = _Tokenizer()

    cue = annotate(_inputs("猫", tokenizer, scorer=_Scorer()))

    assert tokenizer.calls == ["猫"]
    assert [token.surface for token in cue.tokens] == ["猫"]
    assert cue.styles == ["style:猫"]


@pytest.mark.parametrize("warm_error", [None, RuntimeError("warm failed")])
@pytest.mark.timeout(5)
def test_first_annotation_waits_for_retained_tokenizer_warm(warm_error):
    warm: Future[None] = Future()
    tokenizer = _Tokenizer()
    coordinator = CueAnnotationCoordinator(tokenizer_warm=warm)
    try:
        coordinator.submit(
            _key("猫"),
            _inputs("猫", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("猫"),
        )
        assert tokenizer.calls == []

        if warm_error is None:
            warm.set_result(None)
        else:
            warm.set_exception(warm_error)

        result = _wait_result(coordinator)
        assert (result.error is None) is (warm_error is None)
        assert tokenizer.calls == (["猫"] if warm_error is None else [])
    finally:
        if not warm.done():
            warm.set_result(None)
        coordinator.close()


@pytest.mark.timeout(5)
def test_failed_tokenizer_warm_is_consumed_before_the_next_cue():
    warm: Future[None] = Future()
    warm.set_exception(RuntimeError("warm failed"))
    tokenizer = _Tokenizer()
    coordinator = CueAnnotationCoordinator(tokenizer_warm=warm)
    try:
        coordinator.submit(
            _key("first"),
            _inputs("first", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("first"),
        )
        assert isinstance(_wait_result(coordinator).error, RuntimeError)

        coordinator.submit(
            _key("second"),
            _inputs("second", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("second"),
        )
        recovered = _wait_result(coordinator)

        assert recovered.error is None
        assert [token.surface for token in recovered.cue.tokens] == ["second"]
        assert tokenizer.calls == ["second"]
    finally:
        coordinator.close()


@pytest.mark.timeout(5)
def test_running_duplicate_attaches_newest_current_waiter_and_executes_once():
    tokenizer = _BlockingTokenizer()
    coordinator = CueAnnotationCoordinator()
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
        tokenizer.release.set()

        result = _wait_result(coordinator)
        assert result.identity == current
        assert [token.surface for token in result.cue.tokens] == ["猫"]
        assert tokenizer.calls == ["猫"]
    finally:
        tokenizer.release.set()
        coordinator.close()


@pytest.mark.timeout(5)
def test_current_work_precedes_an_already_queued_episode_job():
    blocker = _BlockingTokenizer()
    tokenizer = _Tokenizer()
    coordinator = CueAnnotationCoordinator()
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
        result = _wait_result(coordinator)
        assert result.identity == _identity("current")
        assert tokenizer.calls[:2] == ["current", "episode"]
    finally:
        blocker.release.set()
        coordinator.close()


@pytest.mark.timeout(5)
def test_failed_identity_completes_once_without_a_retry_loop():
    tokenizer = _FailingTokenizer()
    coordinator = CueAnnotationCoordinator()
    try:
        coordinator.submit(
            _key("猫"),
            _inputs("猫", tokenizer),
            priority=AnnotationPriority.CURRENT,
            waiter=_identity("猫"),
        )

        result = _wait_result(coordinator)
        assert result.cue is None and isinstance(result.error, RuntimeError)
        assert tokenizer.calls == ["猫"]
        assert coordinator.drain() == []
    finally:
        coordinator.close()


@pytest.mark.timeout(5)
def test_blocked_worker_cannot_publish_after_bounded_close():
    tokenizer = _BlockingTokenizer()
    coordinator = CueAnnotationCoordinator()
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

    assert coordinator.drain() == []
