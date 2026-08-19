"""Style / span model for rich text.

A :class:`RichText` is a list of :class:`Span`, each with its own :class:`Style`. This is the inline
content unit the layout consumes; the structured-content walker produces it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar, NamedTuple

RGBA = tuple[int, int, int, int]

BLACK: RGBA = (0, 0, 0, 255)


class PitchAccent(NamedTuple):
    """One pitch-accent pattern: the ``position`` downstep (0 = heiban, 1 = atamadaka, n = drop after
    mora n) plus optional per-mora annotations from NHK/Kanjium-style data — ``devoice`` (voiceless
    morae, ○) and ``nasal`` (nasalised morae, ゜), both 1-based mora indices. Position-only consumers
    (freq pill, mined ``{pitch-accent-positions}``) read ``.position``; the graph renderer draws the
    marks. An ``int`` still coerces via ``PitchAccent(n)`` so a plain accent dict is unchanged."""

    position: int
    devoice: tuple[int, ...] = ()
    nasal: tuple[int, ...] = ()


@dataclass(frozen=True)
class Theme:
    """Tooltip colours + reference-size paddings. A value type (no PIL/render deps) so both ``panel``
    and the ``render`` layer can share it without a package cycle — it lives here, not in ``panel``."""

    bg: RGBA = (252, 252, 250, 255)
    text: RGBA = (33, 33, 33, 255)
    muted: RGBA = (110, 118, 110, 255)
    accent: RGBA = (60, 110, 210, 255)  # ▶ triangle / links
    purple: RGBA = (126, 96, 168, 255)  # dictionary-name pills
    tag: RGBA = (96, 125, 175, 255)  # defTag pills (★ / priority form)
    # Every size is defined at the REFERENCE window (scale 1.0) and multiplied by this, so the whole
    # tooltip renders at window_height / REF_H — mpv's OSD model, same content at any window size (just
    # scaled). A plain ``Theme()`` (scale 1.0) is byte-identical to before.
    scale: float = 1.0
    # Reference-size structural paddings (at scale 1.0); the scaled values are the properties below.
    _MARGIN: ClassVar[int] = 16
    _GAP: ClassVar[int] = 7
    _BODY_INDENT: ClassVar[int] = 20

    def px(self, v: float) -> int:
        """Scale a reference-canvas pixel size to the current window (floor 1px so nothing vanishes)."""
        return max(1, round(v * self.scale))

    @property
    def margin(self) -> int:
        return self.px(self._MARGIN)

    @property
    def gap(self) -> int:
        return self.px(self._GAP)

    @property
    def body_indent(self) -> int:
        return self.px(self._BODY_INDENT)


_DEFAULT_THEME = Theme()  # frozen — safe module singleton (B008: no per-call Theme())


@dataclass(frozen=True, slots=True)
class Style:
    size: int = 24
    weight: int = 400
    italic: bool = False
    underline: bool = False
    color: RGBA = BLACK
    strike: bool = False  # textDecorationLine: line-through
    valign: int = 0  # 0 = baseline, +1 = superscript (raised), −1 = subscript (lowered)
    font: str | None = (
        None  # force this vendored font file for the run (kanji stroke-order headword);
    )
    # None = the normal coverage-based fallback chain. Only honoured when the file covers the glyph.

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


def in_rect(rect: tuple[float, float, float, float], x: float, y: float) -> bool:
    """Whether (x, y) falls in ``rect`` — half-open on the right and bottom, so adjacent rects tile
    without a shared edge that hits twice. Lives here, beside the box types, because three modules
    were reaching into the Reader for it.
    """
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def is_ideograph(ch: str) -> bool:
    """True for a CJK ideograph (kanji) — astral-SAFE, unlike the scattered BMP-only range checks that
    miss CJK Extension B+ (#99, e.g. 𠮟 U+20B9F). Covers Unified + Ext A, the Compatibility Ideographs,
    and the whole supplementary ideographic planes (Ext B–H + compat supplement, U+20000–U+3FFFF)."""
    o = ord(ch)
    return (
        0x3400 <= o <= 0x9FFF  # CJK Unified + Ext A
        or 0xF900 <= o <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x20000 <= o <= 0x3FFFF  # supplementary ideographic planes (Ext B–H + compat supplement)
    )


RichText = list[Span]


def plain(text: str, style: Style | None = None) -> RichText:
    return [Span(text, style or Style())]
