"""Bounded speculative-prefetch scheduling at the runtime-job seam."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from saitenka_tokenize.japanese import Token

from saitenka.app.features.tooltip import prefetch
from saitenka.app.features.tooltip.preparation import (
    PersistentHeadCache,
    TooltipPreparationConfig,
    TooltipPreparationInputs,
)
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


def _item(generation: int, surface: str, *, full: bool = False) -> prefetch.PrefetchItem:
    token = Token(surface, surface, surface, "名詞", 0, len(surface))
    return prefetch.PrefetchItem(generation, token, surface, mined=False, full=full)


class _Submitter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


def _complete(state: prefetch.PrefetchState, call: dict, outcome=EffectOutcome.SUCCEEDED) -> None:
    prefetch.finish(
        state,
        EffectFinished(
            EffectId(len(call)),
            Owner.INTERACTION,
            call["identity"],
            outcome,
            result=True,
        ),
        call["on_finished"],
    )


def test_speculative_scheduler_is_bounded_by_its_snapshot_contract() -> None:
    state = prefetch.PrefetchState(head_queue_max=1)
    state.gen = 1
    state.workers = 1
    state.submitter = _Submitter()
    jobs = [(3, _item(1, f"語{i}")) for i in range(100)]

    assert prefetch.schedule(state, jobs, lambda _completion: None)

    snapshot = state.snapshot
    assert snapshot.inflight == 1
    assert snapshot.pending == snapshot.pending_limit - 1


@pytest.mark.parametrize("limit", [-1, 0, 65])
def test_speculative_scheduler_rejects_an_invalid_head_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 64"):
        prefetch.PrefetchState(head_queue_max=limit)


def test_speculative_scheduler_runs_current_then_head_before_warm() -> None:
    state = prefetch.PrefetchState(head_queue_max=4)
    state.gen = 1
    state.workers = 1
    submitter = _Submitter()
    state.submitter = submitter
    token = Token("先", "先", "さき", "名詞", 0, 1)
    jobs = [
        (3, _item(1, "温")),
        (1, prefetch.HeadPrefetchItem(1, token, "先", mined=False)),
        (0, _item(1, "今", full=True)),
    ]

    prefetch.schedule(state, jobs, lambda _completion: None)
    assert submitter.calls[0]["request"].item.token.surface == "今"
    _complete(state, submitter.calls[0])
    assert submitter.calls[1]["request"].item.token.surface == "先"
    _complete(state, submitter.calls[1])
    assert submitter.calls[2]["request"].item.token.surface == "温"


def test_stale_completion_releases_a_slot_for_the_new_generation() -> None:
    state = prefetch.PrefetchState(head_queue_max=4)
    state.workers = 1
    submitter = _Submitter()
    state.submitter = submitter
    generation = state.cancel()
    prefetch.schedule(state, [(0, _item(generation, "古"))], lambda _completion: None)
    stale_call = submitter.calls[0]

    generation = state.cancel()
    prefetch.schedule(state, [(0, _item(generation, "新"))], lambda _completion: None)
    assert len(submitter.calls) == 1
    _complete(state, stale_call, EffectOutcome.CANCELLED)

    assert submitter.calls[1]["request"].item.token.surface == "新"


def test_close_cancels_work_and_rejects_new_admission() -> None:
    state = prefetch.PrefetchState(head_queue_max=4)
    state.workers = 1
    state.submitter = _Submitter()
    generation = state.cancel()
    prefetch.schedule(state, [(0, _item(generation, "古"))], lambda _completion: None)

    prefetch.close(state)

    assert state.snapshot.closed and state.snapshot.pending == 0 and state.snapshot.inflight == 0
    assert not prefetch.schedule(state, [(0, _item(state.gen, "新"))], lambda _completion: None)


def test_superseded_work_never_enters_the_backend() -> None:
    called = False

    class _Backend:
        def run(self, _item, _context, _should_cancel):
            nonlocal called
            called = True
            return True

    work = prefetch.PrefetchWork(_item(1, "古"), None, threading.Event())
    work.superseded.set()

    assert prefetch.run_prefetch(work, threading.Event(), _Backend()) is False
    assert called is False


def test_persistent_signature_tracks_the_captured_dictionary_identity() -> None:
    config = TooltipPreparationConfig(
        enabled=False,
        workers=0,
        cue_lookahead=0,
        head_lookahead=0,
        head_queue_max=1,
        cache_enabled=False,
        cache_max_bytes=0,
        cache_min_height=0,
        mask_atlas_enabled=False,
    )
    cache = PersistentHeadCache(config)
    panels = SimpleNamespace(style=SimpleNamespace(width=384), cap=260)

    def inputs(label: str) -> TooltipPreparationInputs:
        dictionary = SimpleNamespace(
            dicts=(),
            freqs=(SimpleNamespace(title=label),),
            pitches=(),
        )
        return TooltipPreparationInputs(panels, dictionary)  # type: ignore[arg-type]

    old, new = inputs("old-profile"), inputs("new-profile")

    old_signature = cache.signature(old)
    cache.invalidate_signature()
    new_signature = cache.signature(new)
    cache.signature(old)  # an old worker finishes after the replacement

    assert old_signature != new_signature
    assert cache.signature(new) == new_signature
