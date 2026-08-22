"""Provider-neutral legacy subtitle raster contract.

The reducer chooses plain versus styled intent and assembles an immutable request; a provider owns
only raster preparation and returns an immutable result. Pillow, a fake, and a null provider all
satisfy the same contract, so the choice of provider cannot change what gets decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL.Image import Image

    from saitenka.app.tokenize import Token


class RasterStyle(StrEnum):
    #: Text only — the secondary track, or a cue whose annotation is pending or unavailable.
    PLAIN = "plain"
    #: Per-token colouring and the hover underline.
    STYLED = "styled"


@dataclass(frozen=True, slots=True)
class SubtitleRasterRequest:
    """Everything a provider needs to prepare the cue raster, and nothing it could decide from."""

    style: RasterStyle
    text: str
    lines: tuple[tuple[Token, ...], ...]
    width: int
    size: int
    background: tuple[int, int, int, int]
    hover: int | None = None
    hover_end: int | None = None
    styles: list | None = None


@dataclass(frozen=True, slots=True)
class SubtitleRasterResult:
    image: Image
    boxes: tuple


class SubtitleRasterPort(Protocol):
    def render(self, request: SubtitleRasterRequest) -> SubtitleRasterResult: ...

    def close(self) -> None:
        """Release whatever the provider holds. Part of the port because a close participant is a
        contract, not a capability to probe for: a provider with no state still has to answer."""


def raster_style(
    *, secondary_role: bool, upgrade_pending: bool, annotation_degraded: bool
) -> RasterStyle:
    """Plain pixels publish immediately for a cue that has no annotation to show — a pending
    upgrade or a failed one keeps the cue visible rather than blanking it."""
    if secondary_role or upgrade_pending or annotation_degraded:
        return RasterStyle.PLAIN
    return RasterStyle.STYLED


def annotation_visible(*, mode: str, hover_annotation: bool) -> bool:
    return mode == "full" or hover_annotation


@dataclass(frozen=True, slots=True)
class RasterContent:
    """The cue as it will be laid out, independent of any annotation on top of it."""

    text: str
    lines: Sequence[Sequence[Token]]
    width: int
    size: int
    background: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class AnnotationOverlay:
    """What the annotation layer wants drawn over the cue, if it is visible at all."""

    annotated: bool
    hover: int
    hover_span: tuple[int, int] | None
    styles: list | None


def build_request(
    style: RasterStyle, content: RasterContent, overlay: AnnotationOverlay
) -> SubtitleRasterRequest:
    """Assemble the request. A phrase span underlines [start, end) and can begin before the
    hovered token (a leading お in お休み), so it drives the underline rather than `hover`."""
    if style is RasterStyle.PLAIN:
        return SubtitleRasterRequest(
            style, content.text, (), content.width, content.size, content.background
        )
    span = overlay.hover_span if overlay.annotated else None
    hovered = overlay.hover if overlay.annotated and overlay.hover >= 0 else None
    return SubtitleRasterRequest(
        style,
        content.text,
        tuple(tuple(line) for line in content.lines),
        content.width,
        content.size,
        content.background,
        hover=span[0] if span else hovered,
        hover_end=span[1] if span else None,
        styles=overlay.styles if overlay.annotated else None,
    )


class PillowRasterProvider:
    """The shipping provider: Pillow rasterization, no policy."""

    def close(self) -> None:
        """Stateless: each render allocates and returns its own image."""

    def render(self, request: SubtitleRasterRequest) -> SubtitleRasterResult:
        from saitenka.app.subtitles import render_plain_subtitle, render_subtitle

        if request.style is RasterStyle.PLAIN:
            rendered = render_plain_subtitle(
                request.text, request.width, size=request.size, background=request.background
            )
        else:
            rendered = render_subtitle(
                [list(line) for line in request.lines],
                request.width,
                size=request.size,
                hover=request.hover,
                hover_end=request.hover_end,
                styles=request.styles,
                background=request.background,
            )
        return SubtitleRasterResult(rendered.image, tuple(rendered.boxes))
