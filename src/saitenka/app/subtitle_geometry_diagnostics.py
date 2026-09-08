"""Bounded diagnostic vocabulary for optional subtitle geometry."""

from __future__ import annotations

import hashlib
from enum import StrEnum


def cue_digest(text: str) -> str:
    """A short stable handle for one cue's text — the join key across cue and geometry spans.

    Every span in this chain used to carry a different identity, or none: the draw a content digest,
    the decision a `generation`, the reconcile a `cue_revision`, the libass render a `timestamp_ms`,
    and the cache lookup nothing at all. With no shared key the only way to relate them was a
    timestamp window, which is how three separate mechanisms were confidently mis-diagnosed while
    the deciding pair of attributes sat on a span nobody could join to.

    A digest rather than the text: a span attribute has no cardinality limit, but a subtitle line is
    the user's content and does not belong in a bundle that gets shared. It identifies the cue's
    *content*, so a repeated line returns under the handle it had before -- see the readout, which
    splits appearances by intervening draws rather than trusting the handle alone.

    Lives here rather than beside the renderer so the geometry side can reach it: this module is the
    leaf both halves of the chain already import, and routing it through the renderer would make the
    geometry owner depend on the thing it feeds.
    """
    return hashlib.blake2s(text.encode(), digest_size=4).hexdigest()


class GeometryOutcome(StrEnum):
    EMPTY = "empty"
    READY = "ready"
    PENDING = "pending"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class GeometryCacheReason(StrEnum):
    """Why a geometry cache lookup missed.

    A cache key is derived from subtitle text and a source path, so the reason a lookup missed is
    the one diagnostic on this path with a route to that content. Closing the vocabulary at the
    type is what keeps it out — a `str` here would let any caller widen it back open.
    """

    EVICTED = "evicted"
    FIRST_SEEN = "first-seen"
    PREFETCH_PENDING = "prefetch-pending"
    PREFETCH_SUPERSEDED = "prefetch-superseded"
    PROVENANCE_UNKNOWN = "provenance-unknown"
    RENDER_INPUT_CHANGED = "render-input-changed"
    SOURCE_CHANGED = "source-changed"


class GeometryErrorCode(StrEnum):
    ACTIVE_EVENT_BUDGET = "active-event-budget-exceeded"
    ACTIVE_EVENT_MISMATCH = "active-event-mismatch"
    ACTIVE_ROW_BUDGET = "active-row-budget-exceeded"
    BITMAP_BUDGET = "bitmap-budget-exceeded"
    FRAME_BUDGET = "frame-budget-exceeded"
    INVALID_RENDER_SPACE = "invalid-render-space"
    MISSING_PALETTE_PIXELS = "missing-token-colors"
    OVERLAPPING_PALETTE_PIXELS = "overlapping-token-colors"
    SEMANTIC_MISMATCH = "semantic-projection-mismatch"
    PALETTE_BUDGET = "token-budget-exceeded"
    ANNOTATION_MAPPING = "token-mapping-invalid"
    #: The cue is typeset with something the interactive envelope refuses — animation, a vector
    #: drawing, a blur, an ASS effect, bidirectional text. A property of the track, not a
    #: failure: it will refuse the same cue every time, and `provider-error` said the opposite.
    TYPESETTING = "typesetting-unsupported"
    PROVIDER = "provider-error"


_UNSUPPORTED_CODES = frozenset(
    {
        GeometryErrorCode.ACTIVE_EVENT_MISMATCH,
        GeometryErrorCode.ACTIVE_EVENT_BUDGET,
        GeometryErrorCode.ACTIVE_ROW_BUDGET,
        GeometryErrorCode.BITMAP_BUDGET,
        GeometryErrorCode.FRAME_BUDGET,
        GeometryErrorCode.INVALID_RENDER_SPACE,
        GeometryErrorCode.SEMANTIC_MISMATCH,
        GeometryErrorCode.PALETTE_BUDGET,
        GeometryErrorCode.ANNOTATION_MAPPING,
        GeometryErrorCode.TYPESETTING,
    }
)


def geometry_error_code(error: BaseException | str) -> GeometryErrorCode:
    """Classify provider details without exporting their potentially sensitive text."""
    detail = str(error).casefold()
    checks = (
        ("active ass event limit", GeometryErrorCode.ACTIVE_EVENT_BUDGET),
        ("active ass row byte limit", GeometryErrorCode.ACTIVE_ROW_BUDGET),
        ("active ass event", GeometryErrorCode.ACTIVE_EVENT_MISMATCH),
        ("semantic", GeometryErrorCode.SEMANTIC_MISMATCH),
        ("bitmap budget", GeometryErrorCode.BITMAP_BUDGET),
        ("frame pixel", GeometryErrorCode.FRAME_BUDGET),
        ("frame and storage sizes", GeometryErrorCode.INVALID_RENDER_SPACE),
        ("palette", GeometryErrorCode.PALETTE_BUDGET),
        ("missing libass token", GeometryErrorCode.MISSING_PALETTE_PIXELS),
        ("token overlap", GeometryErrorCode.OVERLAPPING_PALETTE_PIXELS),
        # Ahead of the bare "token" fragment below, which two of these also contain — the envelope's
        # refusals are a property of the typesetting, not of how the tokens were mapped onto it.
        ("animated overrides", GeometryErrorCode.TYPESETTING),
        ("blurred", GeometryErrorCode.TYPESETTING),
        ("drawing events", GeometryErrorCode.TYPESETTING),
        ("ass effects", GeometryErrorCode.TYPESETTING),
        ("bidirectional", GeometryErrorCode.TYPESETTING),
        ("ligature", GeometryErrorCode.TYPESETTING),
        ("unparsed primary-color", GeometryErrorCode.TYPESETTING),
        ("unparsed secondary-color", GeometryErrorCode.TYPESETTING),
        ("token", GeometryErrorCode.ANNOTATION_MAPPING),
    )
    return next(
        (code for fragment, code in checks if fragment in detail), GeometryErrorCode.PROVIDER
    )


def geometry_failure_reason(error: BaseException | str) -> tuple[str, GeometryErrorCode]:
    code = geometry_error_code(error)
    reason = (
        "subtitle-frame-unsupported" if code in _UNSUPPORTED_CODES else "geometry-provider-failed"
    )
    return reason, code
