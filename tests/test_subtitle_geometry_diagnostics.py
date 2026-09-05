from __future__ import annotations

import pytest

from saitenka.app.subtitle_geometry_diagnostics import (
    GeometryErrorCode,
    geometry_failure_reason,
)


@pytest.mark.parametrize(
    ("detail", "code"),
    [
        ("active ASS event limit exceeded", GeometryErrorCode.ACTIVE_EVENT_BUDGET),
        ("active ASS row byte limit exceeded", GeometryErrorCode.ACTIVE_ROW_BUDGET),
        ("active ASS event does not match", GeometryErrorCode.ACTIVE_EVENT_MISMATCH),
        ("semantic projection differs", GeometryErrorCode.SEMANTIC_MISMATCH),
        ("bitmap budget exceeded", GeometryErrorCode.BITMAP_BUDGET),
        ("frame pixel limit exceeded", GeometryErrorCode.FRAME_BUDGET),
        (
            "geometry frame and storage sizes must be positive",
            GeometryErrorCode.INVALID_RENDER_SPACE,
        ),
        ("palette entry limit exceeded", GeometryErrorCode.PALETTE_BUDGET),
        ("token annotation extends beyond text", GeometryErrorCode.ANNOTATION_MAPPING),
    ],
)
def test_contract_and_resource_errors_are_bounded_unsupported_decisions(
    detail: str, code: GeometryErrorCode
) -> None:
    assert geometry_failure_reason(ValueError(detail)) == ("subtitle-frame-unsupported", code)


@pytest.mark.parametrize(
    "detail",
    [
        "animated overrides are outside the static interactive envelope",
        "a blurred token's extent is not the word's extent",
        "drawing events are not color-rewritten",
        "ASS effects are outside the static interactive envelope",
        "bidirectional text is outside the interactive envelope",
        "a token boundary may split a Latin ligature",
        "unparsed primary-color command is not color-rewritten",
    ],
)
def test_the_envelope_refusals_are_named_rather_than_reported_as_provider_errors(
    detail: str,
) -> None:
    """Each of these is a property of the track: it refuses the same cue every time, and no retry,
    reinstall or report will change it. Reporting them as `provider-error` said the opposite —
    that something broke — and gave a user nothing to act on. Two of them also contain the word
    "token", so the ordering against `token-mapping-invalid` is part of the claim."""
    assert geometry_failure_reason(ValueError(detail)) == (
        "subtitle-frame-unsupported",
        GeometryErrorCode.TYPESETTING,
    )


@pytest.mark.parametrize(
    ("raw", "span"),
    [
        ("{\\t(\\fscx120)}動く", (0, 2)),
        ("{\\blur4}猫", (0, 1)),
        ("{\\blur-}猫", (0, 1)),
        ("{\\p1}m 0 0{\\p0}字", (0, 1)),
        ("色{\\cZZ}変更", (0, 3)),
    ],
)
def test_the_classifier_reads_the_message_the_rewrite_actually_raises(
    raw: str, span: tuple[int, int]
) -> None:
    """The parametrisation above is hand-copied text. Copies drift: rename one refusal and every
    track carrying that typesetting silently reverts to reporting `provider-error` — "something
    broke, retry" — with both files still green. This closes the loop by classifying the exception
    the raise site produces rather than a transcription of it.
    """
    from saitenka_subtitles.ass import UnsupportedAssEvent, rewrite_ass_event
    from test_ass_document import CATALOG, annotated  # the suite puts tests/ on the path

    with pytest.raises(UnsupportedAssEvent) as refusal:
        rewrite_ass_event(annotated(raw, span), {0: 0x010203}, CATALOG)

    assert geometry_failure_reason(refusal.value) == (
        "subtitle-frame-unsupported",
        GeometryErrorCode.TYPESETTING,
    )


@pytest.mark.parametrize(
    ("detail", "code"),
    [
        ("missing libass token colors", GeometryErrorCode.MISSING_PALETTE_PIXELS),
        ("ambiguous libass token overlap", GeometryErrorCode.OVERLAPPING_PALETTE_PIXELS),
        ("private subtitle text and /a/path", GeometryErrorCode.PROVIDER),
    ],
)
def test_provider_failures_export_only_registered_error_codes(
    detail: str, code: GeometryErrorCode
) -> None:
    assert geometry_failure_reason(RuntimeError(detail)) == ("geometry-provider-failed", code)
