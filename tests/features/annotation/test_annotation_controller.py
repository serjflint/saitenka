from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import dicthelp
import pytest
from saitenka_subtitles import CueIndex, parse_srt
from saitenka_subtitles.model import Cue
from saitenka_tokenize.japanese import Token
from saitenka_tokenize.registry import get_tokenizer
from saitenka_wordstate import Scorer
from saitenka_wordstate.known import KnownWords
from util import FakeIPC

from saitenka.app.features.annotation.annotation_controller import (
    AnnotationInputs,
    AnnotationOutcome,
    CueAnnotationController,
)
from saitenka.app.scoring import Coloring
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


class _Tokenizer:
    def tokenize(self, text: str) -> list[Token]:
        return [Token(text, text, text, "名詞", 0, len(text))]

    def merge_dict_compounds(self, tokens, _terms_exist):
        return tokens


class _BlockingTokenizer(_Tokenizer):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def tokenize(self, text: str) -> list[Token]:
        self.started.set()
        assert self.release.wait(2)
        try:
            return super().tokenize(text)
        finally:
            self.finished.set()


class _CapturingIPC(FakeIPC):
    def __init__(self) -> None:
        super().__init__()
        self.handlers = {}
        self.jobs = []

    def register_runtime_job_lane(self, name, policy, handler) -> bool:  # noqa: ARG002
        self.handlers[name] = handler
        return True

    def submit_runtime_job(self, **kwargs) -> bool:
        self.jobs.append(kwargs)
        return True

    def finish_next(self) -> None:
        job = self.jobs.pop(0)
        result = self.handlers[job["lane"]](job["request"], threading.Event())
        job["on_finished"](
            EffectFinished(
                EffectId(1),
                Owner.SUBTITLE,
                job["identity"],
                EffectOutcome.SUCCEEDED,
                result=result,
            )
        )


def _inputs(
    *,
    start: float = 1.0,
    dependencies_ready: bool = True,
    terms_exist: bool = True,
) -> AnnotationInputs:
    return AnnotationInputs(
        source_epoch=1,
        track_identity=2,
        subtitle_role="primary",
        observed_start=start,
        observed_end=start + 2,
        source_order=None,
        tokenizer=_Tokenizer(),  # type: ignore[arg-type]
        terms_exist=(lambda _forms: set()) if terms_exist else None,
        scorer=None,
        selected_dictionaries=1,
        dependencies_ready=dependencies_ready,
        annotate=True,
    )


def test_sync_replace_returns_a_cached_publication_on_repeat() -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)

    first = owner.replace("猫", _inputs())
    second = owner.replace("猫", _inputs())

    assert first.outcome is AnnotationOutcome.TOKENIZED
    assert second.outcome is AnnotationOutcome.CACHED
    assert second.cue is first.cue


def test_dependency_readiness_is_distinct_from_compound_attestation() -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)

    transition = owner.replace("猫", _inputs(terms_exist=False))
    repeated = owner.replace("猫", _inputs(terms_exist=False))

    assert transition.cue is not None
    assert owner.view.pending_text is None
    assert repeated.outcome is AnnotationOutcome.TOKENIZED


def test_stale_completion_cannot_publish_into_a_replacement_cue() -> None:
    ipc = _CapturingIPC()
    owner = CueAnnotationController(ipc, mode="full", cache_max=4)
    owner.enable_async()
    owner.dependencies_changed("猫", _inputs())
    owner.replace("犬", _inputs(start=4.0))

    ipc.finish_next()

    assert owner.settle() == ()
    assert owner.view.identity is not None
    assert owner.view.identity.normalized_text == "犬"


def test_async_completion_is_published_only_when_the_owner_turn_settles() -> None:
    ipc = _CapturingIPC()
    owner = CueAnnotationController(ipc, mode="full", cache_max=4)
    owner.enable_async()
    owner.dependencies_changed("猫", _inputs())

    ipc.finish_next()
    publications = owner.settle()

    assert [publication.outcome for publication in publications] == [AnnotationOutcome.PUBLISHED]
    assert publications[0].cue is not None
    assert owner.view.pending_text is None


def test_async_incomplete_annotation_is_published_but_not_cached() -> None:
    ipc = _CapturingIPC()
    owner = CueAnnotationController(ipc, mode="full", cache_max=4)
    owner.enable_async()
    owner.dependencies_changed("猫", _inputs(terms_exist=False))

    ipc.finish_next()
    assert [item.outcome for item in owner.settle()] == [AnnotationOutcome.PUBLISHED]
    owner.replace("猫", _inputs(terms_exist=False))

    assert len(ipc.jobs) == 1


def test_profile_invalidation_drops_lookahead_captured_before_the_switch() -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)
    tokenizer = _BlockingTokenizer()
    inputs = _inputs()
    inputs = AnnotationInputs(
        inputs.source_epoch,
        inputs.track_identity,
        inputs.subtitle_role,
        inputs.observed_start,
        inputs.observed_end,
        inputs.source_order,
        tokenizer,  # type: ignore[arg-type]
        inputs.terms_exist,
        inputs.scorer,
        inputs.selected_dictionaries,
        inputs.dependencies_ready,
        inputs.annotate,
    )
    worker = threading.Thread(target=owner.lookahead_captured, args=("猫", lambda: inputs))
    worker.start()
    assert tokenizer.started.wait(1)

    owner.invalidate_tokenizer()
    tokenizer.release.set()
    worker.join(2)

    assert tokenizer.finished.is_set()
    assert owner.replace("猫", _inputs()).outcome is AnnotationOutcome.TOKENIZED


def test_profile_invalidation_during_blocking_prepare_drops_the_old_result() -> None:
    ipc = _CapturingIPC()
    owner = CueAnnotationController(ipc, mode="full", cache_max=4)

    def drive(_timeout: float | None) -> None:
        owner.invalidate_tokenizer()
        ipc.finish_next()

    owner.prepare_blocking("猫", _inputs(), drive=drive)
    transition = owner.replace("猫", _inputs())

    assert transition.outcome is AnnotationOutcome.PENDING
    assert len(ipc.jobs) == 1


def test_retired_episode_cannot_land_a_blocked_synchronous_warm() -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)
    tokenizer = _BlockingTokenizer()
    inputs = _inputs()
    inputs = AnnotationInputs(
        inputs.source_epoch,
        inputs.track_identity,
        inputs.subtitle_role,
        inputs.observed_start,
        inputs.observed_end,
        inputs.source_order,
        tokenizer,  # type: ignore[arg-type]
        inputs.terms_exist,
        inputs.scorer,
        inputs.selected_dictionaries,
        inputs.dependencies_ready,
        inputs.annotate,
    )
    index = CueIndex(parse_srt("1\n00:00:01,000 --> 00:00:03,000\n猫\n"))
    owner.start_episode_warm(index, inputs)
    assert tokenizer.started.wait(1)

    owner.retire_episode_warm()
    tokenizer.release.set()
    assert tokenizer.finished.wait(1)

    assert owner.replace("猫", _inputs()).outcome is AnnotationOutcome.TOKENIZED


def test_synchronous_episode_warm_does_not_cache_incomplete_annotation() -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)
    tokenizer = _BlockingTokenizer()
    tokenizer.release.set()
    inputs = _inputs(terms_exist=False)
    inputs = AnnotationInputs(
        inputs.source_epoch,
        inputs.track_identity,
        inputs.subtitle_role,
        inputs.observed_start,
        inputs.observed_end,
        inputs.source_order,
        tokenizer,  # type: ignore[arg-type]
        inputs.terms_exist,
        inputs.scorer,
        inputs.selected_dictionaries,
        inputs.dependencies_ready,
        inputs.annotate,
    )
    index = CueIndex(parse_srt("1\n00:00:01,000 --> 00:00:03,000\n猫\n"))

    owner.start_episode_warm(index, inputs)
    assert tokenizer.finished.wait(1)

    assert owner.replace("猫", _inputs(terms_exist=False)).outcome is AnnotationOutcome.TOKENIZED


def test_mode_change_retires_the_hover_projection() -> None:
    owner = CueAnnotationController(FakeIPC(), mode="hover", cache_max=4)
    owner.set_hover_revealed(revealed=True)

    owner.set_mode("full")

    assert owner.view.hover_revealed is False


def test_close_refuses_a_late_completion() -> None:
    ipc = _CapturingIPC()
    owner = CueAnnotationController(ipc, mode="full", cache_max=4)
    owner.enable_async()
    owner.dependencies_changed("猫", _inputs())

    owner.close()
    ipc.finish_next()

    assert owner.settle() == ()


def test_public_view_is_an_immutable_snapshot() -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)
    view = owner.view

    with pytest.raises(FrozenInstanceError):
        view.retired = False  # type: ignore[misc]


def test_unknown_mode_is_rejected_by_the_feature_owner() -> None:
    with pytest.raises(ValueError, match="unknown annotation mode"):
        CueAnnotationController(FakeIPC(), mode="invalid", cache_max=4)  # type: ignore[arg-type]


# One unknown content word each (読む / 書く), no end punctuation: joined, they are one N+2 sentence.
SIGN = "私は本を読む"
LINE = "私は本を書く"
FRAME = f"{SIGN}\n{LINE}"
CO_TIMED = (Cue(0.0, 5.0, SIGN), Cue(0.0, 5.0, LINE))


@pytest.fixture
def scorer() -> Coloring:
    return Coloring(
        Scorer(
            known=KnownWords.from_set(["私", "本"]), jlpt=dicthelp.load_jlpt(), enable_freq=False
        )
    )


def _scored_inputs(scorer: Coloring, *cues: Cue, attested: bool = False) -> AnnotationInputs:
    return AnnotationInputs(
        source_epoch=1,
        track_identity=2,
        subtitle_role="primary",
        observed_start=0.0,
        observed_end=5.0,
        source_order=None,
        tokenizer=get_tokenizer(),
        # A dictionary makes the result complete, which is what lets the token cache keep it.
        terms_exist=(lambda _forms: set()) if attested else None,
        scorer=scorer,
        selected_dictionaries=0,
        dependencies_ready=True,
        annotate=True,
        sub_index=CueIndex(list(cues)),
    )


def _n_plus_one(cue) -> set[str]:
    assert cue is not None and cue.styles is not None
    return {
        token.surface
        for token, style in zip(cue.tokens, cue.styles, strict=True)
        if style.tag.startswith("n+1")
    }


def test_co_timed_events_count_n_plus_one_separately(scorer) -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)

    transition = owner.replace(FRAME, _scored_inputs(scorer, *CO_TIMED))

    assert _n_plus_one(transition.cue) == {"読む", "書く"}


def test_a_wrapped_event_stays_one_sentence(scorer) -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)

    transition = owner.replace(FRAME, _scored_inputs(scorer, Cue(0.0, 5.0, FRAME)))

    assert _n_plus_one(transition.cue) == set()


def test_the_token_cache_keeps_event_splits_apart(scorer) -> None:
    owner = CueAnnotationController(FakeIPC(), mode="full", cache_max=4)
    wrapped = owner.replace(FRAME, _scored_inputs(scorer, Cue(0.0, 5.0, FRAME), attested=True))
    assert (
        owner.replace(FRAME, _scored_inputs(scorer, Cue(0.0, 5.0, FRAME), attested=True)).cue
        is wrapped.cue
    )

    transition = owner.replace(FRAME, _scored_inputs(scorer, *CO_TIMED, attested=True))

    assert _n_plus_one(transition.cue) == {"読む", "書く"}


def test_async_lane_counts_n_plus_one_per_event(scorer) -> None:
    ipc = _CapturingIPC()
    owner = CueAnnotationController(ipc, mode="full", cache_max=4)
    owner.enable_async()
    owner.dependencies_changed(FRAME, _scored_inputs(scorer, Cue(0.0, 5.0, FRAME), attested=True))
    ipc.finish_next()
    owner.settle()

    owner.replace(FRAME, _scored_inputs(scorer, *CO_TIMED, attested=True))
    ipc.finish_next()
    (publication,) = owner.settle()

    assert _n_plus_one(publication.cue) == {"読む", "書く"}
