"""Provider-neutral legacy subtitle raster contract.

The reducer chooses plain versus styled intent and assembles an immutable request; a provider owns
only raster preparation and returns an immutable result. Pillow, a fake, and a null provider all
satisfy the same contract, so the choice of provider cannot change what gets decided.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL.Image import Image
    from saitenka_tokenize.japanese import Token


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
    preserve_raw: bool = False


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
    preserve_raw: bool = False


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
        preserve_raw=content.preserve_raw,
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
        elif request.preserve_raw:
            return _render_projected(request)
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


def raw_display_lines(text: str, lines) -> tuple[list[list[Token]], list[int | None]]:
    """Fill authored gaps for painting only; fillers never receive semantic indices."""
    from saitenka_tokenize.japanese import Token

    source = iter(
        line for line in text.replace("\\N", "\n").replace("\r", "").split("\n") if line.strip()
    )
    display: list[list[Token]] = []
    indices: list[int | None] = []
    semantic_index = 0
    for line_tokens in lines:
        raw = next(source)
        row: list[Token] = []
        end = 0
        for token in line_tokens:
            if raw[token.start : token.end] != token.surface or token.start < end:
                raise ValueError("study token does not match authored text")
            if token.start > end:
                row.append(Token(raw[end : token.start], "", "", "記号", end, token.start))
                indices.append(None)
            row.append(token)
            indices.append(semantic_index)
            semantic_index += 1
            end = token.end
        if end < len(raw):
            row.append(Token(raw[end:], "", "", "記号", end, len(raw)))
            indices.append(None)
        display.append(row)
    return display, indices


def _render_projected(request: SubtitleRasterRequest) -> SubtitleRasterResult:
    from saitenka.app.subtitles import render_subtitle

    lines, indices = raw_display_lines(request.text, request.lines)
    selected = [
        index
        for index, semantic in enumerate(indices)
        if semantic is not None
        and request.hover is not None
        and request.hover <= semantic < (request.hover_end or request.hover + 1)
    ]
    styles = (
        [request.styles[index] if index is not None else None for index in indices]
        if request.styles
        else None
    )
    rendered = render_subtitle(
        lines,
        request.width,
        size=request.size,
        background=request.background,
        hover=selected[0] if selected else None,
        hover_end=selected[-1] + 1 if selected else None,
        styles=styles,
    )
    boxes = []
    for box in rendered.boxes:
        index = indices[box.index]
        if index is not None:
            boxes.append(replace(box, index=index))
    return SubtitleRasterResult(rendered.image, tuple(boxes))
