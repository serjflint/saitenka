from __future__ import annotations

import threading
from types import SimpleNamespace

from saitenka.app import tooltip_engaged
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome


class Backend:
    def __init__(self):
        self.calls = []

    def hover(self, request, should_cancel):
        self.calls.append(("hover", request.key, should_cancel()))

    def navigate(self, request, should_cancel):
        self.calls.append(("navigate", request.query, should_cancel()))

    def open(self, request, should_cancel):
        self.calls.append(("open", request.query, should_cancel()))


class Submitter:
    def __init__(self):
        self.calls = []
        self.reject_next = False

    def __call__(self, **kwargs):
        if self.reject_next:
            self.reject_next = False
            return False
        self.calls.append(kwargs)
        return True

    def finish(self, backend, *, outcome=EffectOutcome.SUCCEEDED):
        call = self.calls.pop(0)
        result = tooltip_engaged.run_engaged(call["request"], threading.Event(), backend)
        completion = EffectFinished(
            EffectId(1), call["owner"], call["identity"], outcome, result=result
        )
        call["on_finished"](completion)


def _hover(key: str) -> tooltip_engaged.HoverRequest:
    return tooltip_engaged.HoverRequest(
        SimpleNamespace(surface=key), key, mined=False, key=(key,), cap=100
    )


def test_newest_intent_supersedes_running_and_pending_work():
    state = tooltip_engaged.EngagedState()
    submitter = Submitter()
    completions = []
    assert tooltip_engaged.submit(
        state, _hover("a"), generation=1, submitter=submitter, on_finished=completions.append
    )
    first = state.inflight
    assert first is not None

    assert tooltip_engaged.submit(
        state,
        tooltip_engaged.NavigateRequest("b", 1),
        generation=1,
        submitter=submitter,
        on_finished=completions.append,
    )
    second = state.pending
    assert second is not None and first[1].superseded.is_set()
    assert tooltip_engaged.submit(
        state,
        tooltip_engaged.OpenRequest("link", "c", (1, 2, 3), 1),
        generation=1,
        submitter=submitter,
        on_finished=completions.append,
    )

    assert second[1].superseded.is_set()
    assert isinstance(state.pending[1].request, tooltip_engaged.OpenRequest)


def test_stale_completion_dispatches_newest_once_and_cannot_publish():
    state = tooltip_engaged.EngagedState()
    submitter = Submitter()
    observed = []

    def finished(completion):
        observed.append(tooltip_engaged.finish(state, completion, submitter, finished))

    tooltip_engaged.submit(
        state, _hover("a"), generation=1, submitter=submitter, on_finished=finished
    )
    tooltip_engaged.submit(
        state,
        tooltip_engaged.NavigateRequest("b", 1),
        generation=1,
        submitter=submitter,
        on_finished=finished,
    )

    submitter.finish(Backend())

    assert len(observed) == 1
    assert observed[0] is not None
    assert observed[0][2:5] == (None, False, True)
    assert observed[0][5] is None
    assert len(submitter.calls) == 1
    assert isinstance(submitter.calls[0]["request"].request, tooltip_engaged.NavigateRequest)


def test_cancel_and_close_reject_stale_or_new_work():
    state = tooltip_engaged.EngagedState()
    submitter = Submitter()
    tooltip_engaged.submit(
        state, _hover("a"), generation=1, submitter=submitter, on_finished=lambda _: None
    )

    tooltip_engaged.cancel(state)
    assert state.inflight[1].superseded.is_set()
    tooltip_engaged.close(state)

    assert state.inflight is None
    assert not tooltip_engaged.submit(
        state, _hover("b"), generation=2, submitter=submitter, on_finished=lambda _: None
    )


def test_worker_checks_feature_supersession_before_touching_backend():
    backend = Backend()
    superseded = threading.Event()
    superseded.set()
    work = tooltip_engaged.EngagedWork(_hover("a"), superseded)

    assert tooltip_engaged.run_engaged(work, threading.Event(), backend) is None
    assert backend.calls == []


def test_initial_admission_rejection_is_observable_to_the_sync_fallback():
    state = tooltip_engaged.EngagedState()

    assert not tooltip_engaged.submit(
        state,
        _hover("a"),
        generation=1,
        submitter=lambda **_kwargs: False,
        on_finished=lambda _: None,
    )
    assert state.inflight is None and state.pending is None


def test_pending_admission_rejection_returns_the_newest_intent():
    state = tooltip_engaged.EngagedState()
    submitter = Submitter()
    observed = []

    def finished(completion):
        observed.append(tooltip_engaged.finish(state, completion, submitter, finished))

    tooltip_engaged.submit(
        state, _hover("a"), generation=1, submitter=submitter, on_finished=finished
    )
    newest = tooltip_engaged.OpenRequest("link", "b", (1, 2, 3), 1)
    tooltip_engaged.submit(state, newest, generation=1, submitter=submitter, on_finished=finished)
    submitter.reject_next = True

    submitter.finish(Backend())

    assert len(observed) == 1
    assert observed[0] is not None
    rejected = observed[0][5]
    assert rejected is not None and rejected[1] == newest
    assert state.inflight is None and state.pending is None


def test_stale_completion_reports_rejected_new_generation_with_its_own_identity():
    state = tooltip_engaged.EngagedState()
    submitter = Submitter()
    observed = []

    def finished(completion):
        observed.append(tooltip_engaged.finish(state, completion, submitter, finished))

    tooltip_engaged.submit(
        state, _hover("a"), generation=1, submitter=submitter, on_finished=finished
    )
    newest = tooltip_engaged.NavigateRequest("b", 1)
    tooltip_engaged.submit(state, newest, generation=2, submitter=submitter, on_finished=finished)
    submitter.reject_next = True

    submitter.finish(Backend())

    rejected = observed[0][5]
    assert rejected is not None
    assert rejected[0].generation == 2 and rejected[1] == newest
