"""Style / span model for rich text.

A :class:`RichText` is a list of :class:`Span`, each with its own :class:`Style`. This is the inline
content unit the layout consumes; the structured-content walker produces it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

RGBA = tuple[int, int, int, int]

BLACK: RGBA = (0, 0, 0, 255)


@dataclass(frozen=True, slots=True)
class Style:
    size: int = 24
    weight: int = 400
    italic: bool = False
    underline: bool = False
    color: RGBA = BLACK

    def with_(self, **kw) -> Style:
        return replace(self, **kw)


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    style: Style = Style()
    href: str | None = None  # internal dict link target term; None = plain / external text


@dataclass(frozen=True, slots=True)
class ScanBox:
    """A hit-testable cell for one rendered CJK character (hover a word *inside* the tooltip).

    ``text`` is the character plus the rest of its CJK run (a Yomitan-style scan tail: hovering the
    first char of 追いかける gives ``追いかける``, the second gives ``いかける``), so the controller can
    longest-match a word starting exactly where the cursor is. ``(x, y, w, h)`` is the cell rect in the
    coordinate space of the image it was captured in (offset as the image is composited into the panel)."""

    text: str
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class LinkBox:
    """A hit-testable region for a rendered internal dictionary link (click a ``<a>`` cross-reference
    inside the tooltip → open its target term). ``query`` is the term to look up; ``(x, y, w, h)`` is
    the region in the coordinate space of the image it was captured in (offset as that image is
    composited into the panel). One box per link *per visual line* (a wrapped link yields several)."""

    query: str
    x: int
    y: int
    w: int
    h: int


RichText = list[Span]


def plain(text: str, style: Style | None = None) -> RichText:
    return [Span(text, style or Style())]
