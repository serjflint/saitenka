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
        "animated or karaoke overrides are not color-rewritten",
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
