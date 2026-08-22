"""WP4.2: identity, not arrival order, decides what an annotation completion may do."""

from __future__ import annotations

from saitenka.app.cue_annotation import (
    AnnotationDisposition,
    AnnotationResult,
    AnnotationWorkKey,
    CueIdentity,
    disposition,
)
from saitenka.app.token_cache import TokenizedCue

IDENTITY = CueIdentity(
    source_epoch=1,
    track_identity=2,
    subtitle_role="jp",
    normalized_text="猫を見る",
    observed_start=1.0,
    observed_end=3.0,
)
KEY = AnnotationWorkKey(
    normalized_text="猫を見る",
    source_epoch=1,
    track_identity=2,
    subtitle_role="jp",
    token_cache_generation=0,
    dependency_generation=0,
)
CUE = TokenizedCue(lines=[], tokens=[], styles=None)


def result(
    *,
    key: AnnotationWorkKey = KEY,
    identity: CueIdentity | None = IDENTITY,
    cue: TokenizedCue | None = CUE,
    error: Exception | None = None,
) -> AnnotationResult:
    return AnnotationResult(key, identity, cue, error, 0.0, 0.0)


def decide(
    completion: AnnotationResult,
    *,
    current_identity: CueIdentity | None = IDENTITY,
    current_key: AnnotationWorkKey | None = KEY,
    cue_retired: bool = False,
    pending_text: str | None = "猫を見る",
) -> AnnotationDisposition:
    return disposition(
        completion,
        current_identity=current_identity,
        current_key=current_key,
        cue_retired=cue_retired,
        pending_text=pending_text,
    )


def test_a_matching_completion_publishes() -> None:
    assert decide(result()) is AnnotationDisposition.PUBLISH


def test_a_retired_cue_quarantines_a_late_completion() -> None:
    assert decide(result(), cue_retired=True) is AnnotationDisposition.STALE_CUE


def test_a_replaced_cue_identity_quarantines_a_late_completion() -> None:
    replaced = CueIdentity(2, 2, "jp", "猫を見る", 1.0, 3.0)

    assert decide(result(), current_identity=replaced) is AnnotationDisposition.STALE_CUE


def test_a_completion_for_an_abandoned_upgrade_does_not_publish() -> None:
    assert decide(result(), pending_text=None) is AnnotationDisposition.STALE_CUE
    assert decide(result(), pending_text="犬も見る") is AnnotationDisposition.STALE_CUE


def test_a_moved_work_generation_quarantines_a_completion() -> None:
    moved = AnnotationWorkKey("猫を見る", 1, 2, "jp", 0, 1)

    assert decide(result(), current_key=moved) is AnnotationDisposition.STALE_GENERATION


def test_a_failure_for_the_live_cue_degrades_interaction_only() -> None:
    outcome = decide(result(cue=None, error=RuntimeError("tokenizer down")))

    assert outcome is AnnotationDisposition.DEGRADE
    assert outcome.failed


def test_a_failure_for_a_gone_cue_has_nothing_to_degrade() -> None:
    outcome = decide(result(cue=None, error=RuntimeError("boom")), current_identity=None)

    assert outcome is AnnotationDisposition.FAILED_STALE
    assert outcome.failed


def test_a_failure_under_a_moved_generation_has_nothing_to_degrade() -> None:
    moved = AnnotationWorkKey("猫を見る", 1, 2, "jp", 0, 1)
    outcome = decide(result(cue=None, error=RuntimeError("boom")), current_key=moved)

    assert outcome is AnnotationDisposition.FAILED_STALE


def test_a_result_missing_its_identity_is_never_published() -> None:
    assert decide(result(identity=None)).failed


def test_a_result_missing_its_cue_is_never_published() -> None:
    assert decide(result(cue=None)).failed


def test_only_failures_report_as_failed() -> None:
    non_failures = {
        AnnotationDisposition.PUBLISH,
        AnnotationDisposition.STALE_CUE,
        AnnotationDisposition.STALE_GENERATION,
    }

    assert not any(outcome.failed for outcome in non_failures)
