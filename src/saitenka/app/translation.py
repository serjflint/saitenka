"""Translation reveal: an English secondary subtitle track shown as its own overlay, either manually
toggled (``t``) or auto-revealed while a tooltip is up (``auto_translate`` opt-in).

This module is the pure text and layout seam. The feature owner samples volatile inputs, decides
whether the reveal is wanted, and asks the subtitle and surface boundaries to apply that decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.model import Span, Style
from saitenka.render.flow import render_flow
from saitenka.render.layout import Block, inline_width

if TYPE_CHECKING:
    from PIL import Image

#: The box wraps only past this fraction of the screen, so a short line stays a short box.
_MAX_WIDTH = 0.8
_PADDING = 14


def clean_secondary(raw: object) -> str:
    """mpv's secondary-sub-text as one line: ASS breaks and newlines become spaces."""
    return (str(raw or "")).replace("\\N", " ").replace("\n", " ").strip()


def render_translation(text: str, osd: tuple[int, int]) -> tuple[Image.Image, int, int]:
    """Lay the translation box out for ``osd``, returning ``(image, x, y)``.

    Pure, and worth being so: every number here is a fraction of the screen, which is exactly the
    kind of thing that silently stops tracking a resize. It can be checked at any size without a
    session.
    """
    width, height = osd
    style = Style(size=max(20, round(height * 0.032)), color=(220, 224, 235, 255))
    # Trim the box to the text, then centre it.
    box_w = min(round(inline_width([Span(text, style)])) + 2 * _PADDING, int(width * _MAX_WIDTH))
    flow = render_flow(
        [Span(text, style)], Block(width=box_w, padding=_PADDING, background=(0, 0, 0, 170))
    )
    # Top of the screen (SubMiner-style) — clear of the JP subs at the bottom and of the tooltip,
    # which anchors above the hovered word.
    return flow, (width - flow.width) // 2, max(8, round(height * 0.035))
