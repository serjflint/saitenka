"""Stack layout blocks vertically into one image (paragraphs + simple list markers).

Used to golden-render a walked structured-content tree; the panel composer layers chrome
(chips, borders, hanging indents) on top of the same block stream.
"""

from __future__ import annotations

from PIL import Image

from overlay.model import RGBA, LinkBox, ScanBox, Span, Style
from overlay.render.flow import first_baseline, render_flow
from overlay.render.layout import Block as FlowBlock
from overlay.sc.model import Block

INDENT_PX = 18
GUTTER_PX = 22


def _marker(block: Block) -> str:
    if block.kind != "list-item":
        return ""
    if block.list_type == "ol" and block.ordinal is not None:
        return f"{block.ordinal}."
    return "・"


def render_document(
    blocks: list[Block],
    width: int,
    base: Style | None = None,
    padding: int = 14,
    gap: int = 4,
    background: RGBA = (0, 0, 0, 0),
    scan_out: list[ScanBox] | None = None,
    link_out: list[LinkBox] | None = None,
    max_height: int | None = None,
    clipped_out: list | None = None,
    indent_px: int = INDENT_PX,
    gutter_px: int = GUTTER_PX,
) -> Image.Image:
    """Render blocks stacked top-to-bottom at a fixed panel width.

    When ``scan_out`` is given, append per-character :class:`ScanBox`es (document-image coords) for
    nested scanning. When ``link_out`` is given, append per-link :class:`LinkBox`es in the same
    coords.

    ``max_height``: bound the rasterised height — each block's flow is capped at the remaining
    budget and blocks past the budget are skipped entirely; ``True`` is appended to
    ``clipped_out`` when anything was dropped. ``None`` = full render (byte-identical)."""
    base = base or Style()
    rendered: list[tuple[int, Image.Image, str, int, list[ScanBox], list[LinkBox]]] = []
    remaining = None if max_height is None else max(1, max_height - 2 * padding)
    for b in blocks:
        if remaining is not None and remaining <= 0 and rendered:
            if clipped_out is not None:
                clipped_out.append(True)
            break
        indent = padding + b.indent * indent_px
        gutter = gutter_px if b.kind == "list-item" else 0
        content_w = max(10, width - indent - gutter - padding)
        fb = FlowBlock(width=content_w, padding=0, background=(0, 0, 0, 0))
        local: list[ScanBox] | None = [] if scan_out is not None else None
        llocal: list[LinkBox] | None = [] if link_out is not None else None
        img = render_flow(
            b.flow,
            fb,
            scan_out=local,
            link_out=llocal,
            max_height=remaining,
            clipped_out=clipped_out,
        )
        if remaining is not None:
            remaining -= img.height + gap
        baseline = first_baseline(b.flow, fb)  # align marker to real first-line baseline
        rendered.append((indent + gutter, img, _marker(b), baseline, local or [], llocal or []))

    total_h = (
        padding * 2 + sum(im.height for _, im, *_ in rendered) + gap * max(0, len(rendered) - 1)
    )
    canvas = Image.new("RGBA", (width, max(total_h, 1)), background)
    from PIL import ImageDraw

    from overlay.render.layout import draw_inline

    draw = ImageDraw.Draw(canvas)

    y = padding
    for x, img, marker, baseline, local, llocal in rendered:
        canvas.alpha_composite(img, (x, y))
        if marker:
            draw_inline(canvas, draw, x - gutter_px, y + baseline, [Span(marker, base)])
        if scan_out is not None:
            for sb in local:
                scan_out.append(ScanBox(sb.text, sb.x + x, sb.y + y, sb.w, sb.h))
        if link_out is not None:
            for lb in llocal:
                link_out.append(LinkBox(lb.query, lb.x + x, lb.y + y, lb.w, lb.h))
        y += img.height + gap
    return canvas
