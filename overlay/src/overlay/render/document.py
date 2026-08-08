"""Stack layout blocks vertically into one image (paragraphs + simple list markers).

Used to golden-render a walked structured-content tree; the panel composer layers chrome
(chips, borders, hanging indents) on top of the same block stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image

from overlay.model import RGBA, LinkBox, ScanBox, Span, Style
from overlay.render.flow import (
    FlowLayout,
    first_baseline,
    layout_flow,
    render_flow,
    render_flow_window,
)
from overlay.render.layout import Block as FlowBlock

if TYPE_CHECKING:
    from overlay.sc.model import Block

INDENT_PX = 18
GUTTER_PX = 22


def _marker(block: Block) -> str:
    if block.kind != "list-item":
        return ""
    if block.list_type == "ol" and block.ordinal is not None:
        return f"{block.ordinal}."
    return "・"


def _render_block(
    b: Block,
    width: int,
    padding: int,
    indent_px: int,
    gutter_px: int,
    remaining: int | None,
    scan_out: list[ScanBox] | None,
    link_out: list[LinkBox] | None,
    clipped_out: list | None,
) -> tuple[tuple[int, Image.Image, str, int, list[ScanBox], list[LinkBox]], int]:
    """Render one block's flow (capped at ``remaining``). Returns ``(row, image_height)``."""
    indent = padding + b.indent * indent_px
    gutter = gutter_px if b.kind == "list-item" else 0
    content_w = max(10, width - indent - gutter - padding)
    fb = FlowBlock(width=content_w, padding=0, background=(0, 0, 0, 0))
    local: list[ScanBox] | None = [] if scan_out is not None else None
    llocal: list[LinkBox] | None = [] if link_out is not None else None
    img = render_flow(
        b.flow, fb, scan_out=local, link_out=llocal, max_height=remaining, clipped_out=clipped_out
    )
    baseline = first_baseline(b.flow, fb)  # align marker to real first-line baseline
    row = (indent + gutter, img, _marker(b), baseline, local or [], llocal or [])
    return row, img.height


def _render_blocks(
    blocks: list[Block],
    width: int,
    style: DocStyle,
    max_height: int | None,
    clipped_out: list | None,
    scan_out: list[ScanBox] | None,
    link_out: list[LinkBox] | None,
) -> list[tuple[int, Image.Image, str, int, list[ScanBox], list[LinkBox]]]:
    """Render each block's flow (capped at the remaining height budget), stopping early when the
    budget runs out — blocks past it are skipped entirely and ``clipped_out`` records the drop."""
    rendered: list[tuple[int, Image.Image, str, int, list[ScanBox], list[LinkBox]]] = []
    remaining = None if max_height is None else max(1, max_height - 2 * style.padding)
    for b in blocks:
        if remaining is not None and remaining <= 0 and rendered:
            if clipped_out is not None:
                clipped_out.append(True)
            break
        row, h = _render_block(
            b,
            width,
            style.padding,
            style.indent_px,
            style.gutter_px,
            remaining,
            scan_out,
            link_out,
            clipped_out,
        )
        if remaining is not None:
            remaining -= h + style.gap
        rendered.append(row)
    return rendered


def _composite(
    rendered: list[tuple[int, Image.Image, str, int, list[ScanBox], list[LinkBox]]],
    width: int,
    padding: int,
    gap: int,
    gutter_px: int,
    base: Style,
    background: RGBA,
    scan_out: list[ScanBox] | None,
    link_out: list[LinkBox] | None,
) -> Image.Image:
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


@dataclass
class LaidBlock:
    """One block, wrapped but not drawn: its x-offset, list marker, first-line baseline, stacked top
    (document-space y), and the cached flow layout the windowed raster draws any band from."""

    x: int
    marker: str
    baseline: int
    top: int
    layout: FlowLayout

    @property
    def height(self) -> int:
        return self.layout.height


@dataclass
class DocLayout:
    """A whole document's blocks laid out (walk + wrap, no ``getmask2``) — the measure half of
    :func:`render_document`, reused by the banded engine to raster viewport-sized bands of a tall body
    without re-walking or re-wrapping. :attr:`full_height` is the exact height a full render produces."""

    blocks: list[LaidBlock]
    width: int
    padding: int
    gap: int
    base: Style
    background: RGBA
    gutter_px: int

    @property
    def full_height(self) -> int:
        n = len(self.blocks)
        if n == 0:
            return 2 * self.padding
        return 2 * self.padding + sum(b.height for b in self.blocks) + self.gap * (n - 1)


def layout_document(
    blocks: list[Block],
    width: int,
    base: Style | None = None,
    padding: int = 14,
    gap: int = 4,
    background: RGBA = (0, 0, 0, 0),
    indent_px: int = INDENT_PX,
    gutter_px: int = GUTTER_PX,
) -> DocLayout:
    """Wrap every block and stack it (same geometry as :func:`_render_block`/:func:`_composite`) WITHOUT
    drawing — cheap. The result rasters any y-window in O(band) via :func:`_composite_window`."""
    base = base or Style()
    laid: list[LaidBlock] = []
    y = padding
    for b in blocks:
        indent = padding + b.indent * indent_px
        gutter = gutter_px if b.kind == "list-item" else 0
        content_w = max(10, width - indent - gutter - padding)
        fb = FlowBlock(width=content_w, padding=0, background=(0, 0, 0, 0))
        lay = layout_flow(b.flow, fb)
        laid.append(LaidBlock(indent + gutter, _marker(b), lay.first_baseline, y, lay))
        y += lay.height + gap
    return DocLayout(laid, width, padding, gap, base, background, gutter_px)


def document_geometry(doc: DocLayout) -> tuple[list[ScanBox], list[LinkBox]]:
    """Whole-document scan/link hitboxes (document-space) from the layout WITHOUT drawing — each block's
    flow geometry offset by its ``(x, top)``, matching :func:`_composite`'s coords. Lets the banded
    engine retain a measured body's hitboxes before any band rasters."""
    from overlay.render.flow import flow_geometry

    scan: list[ScanBox] = []
    links: list[LinkBox] = []
    for lb in doc.blocks:
        s, k = flow_geometry(lb.layout)
        scan.extend(ScanBox(b.text, b.x + lb.x, b.y + lb.top, b.w, b.h) for b in s)
        links.extend(LinkBox(b.query, b.x + lb.x, b.y + lb.top, b.w, b.h) for b in k)
    return scan, links


def _draw_window_block(
    canvas: Image.Image,
    draw,
    doc: DocLayout,
    lb: LaidBlock,
    y0: int,
    y1: int,
    scan_out: list[ScanBox] | None,
    link_out: list[LinkBox] | None,
    *,
    scale: float = 1.0,
) -> None:
    """Draw one block's overlapping band into the window canvas at its stacked top minus ``y0``, and
    append its scan/link boxes in window space (see :func:`_composite_window`). ``scale`` > 1 draws the
    band natively and places it at ``round(pos×scale)``; scan/link boxes stay 1× (offset math unchanged)."""
    from overlay.render.layout import draw_inline

    local_y0 = max(0, y0 - lb.top)  # first block-local row inside the window
    local_y1 = min(lb.height, y1 - lb.top)
    local_scan: list[ScanBox] | None = [] if scan_out is not None else None
    local_link: list[LinkBox] | None = [] if link_out is not None else None
    img = render_flow_window(lb.layout, local_y0, local_y1, local_scan, local_link, scale=scale)
    dst_y = lb.top + local_y0 - y0  # where the drawn band lands in the window (reference px)
    canvas.alpha_composite(img, (round(lb.x * scale), round(dst_y * scale)))
    if lb.marker:
        draw_inline(
            canvas,
            draw,
            lb.x - doc.gutter_px,
            lb.top + lb.baseline - y0,
            [Span(lb.marker, doc.base)],
            scale=scale,
        )
    if scan_out is not None and local_scan is not None:
        scan_out.extend(ScanBox(s.text, s.x + lb.x, s.y + dst_y, s.w, s.h) for s in local_scan)
    if link_out is not None and local_link is not None:
        link_out.extend(LinkBox(k.query, k.x + lb.x, k.y + dst_y, k.w, k.h) for k in local_link)


def _composite_window(
    doc: DocLayout,
    y0: int,
    y1: int,
    scan_out: list[ScanBox] | None,
    link_out: list[LinkBox] | None,
    *,
    scale: float = 1.0,
) -> Image.Image:
    """Assemble the document band ``[y0, y1)`` into a ``(y1 - y0)``-tall image — pixel-identical to the
    full render cropped to that band, but drawing only the blocks (and the lines within them) that fall
    in the window. Each block is drawn at its stacked top minus ``y0``; scan/link boxes come back in the
    window's space (offset by ``-y0``), matching :func:`_composite`. ``scale`` > 1 makes the image
    native (``round(w×scale) × round((y1-y0)×scale)``); at 1.0 it's byte-identical."""
    from PIL import ImageDraw

    canvas = Image.new(
        "RGBA", (round(doc.width * scale), max(1, round((y1 - y0) * scale))), doc.background
    )
    draw = ImageDraw.Draw(canvas)
    for lb in doc.blocks:
        if lb.top < y1 and lb.top + lb.height > y0:  # block's band overlaps the window
            _draw_window_block(canvas, draw, doc, lb, y0, y1, scan_out, link_out, scale=scale)
    return canvas


def render_layout_window(
    doc: DocLayout,
    y0: int,
    y1: int,
    scan_out: list[ScanBox] | None = None,
    link_out: list[LinkBox] | None = None,
    *,
    scale: float = 1.0,
) -> Image.Image:
    """Raster the band ``[y0, y1)`` of an already-laid-out :class:`DocLayout` — the reusable draw the
    banded engine calls per row/band without re-walking or re-wrapping (see :func:`layout_document`).
    ``scale`` > 1 rasters natively (crisp glyph masks at ``size×scale``); geometry stays 1×."""
    return _composite_window(doc, y0, y1, scan_out, link_out, scale=scale)


@dataclass(frozen=True)
class DocStyle:
    """Layout geometry + base text style + backdrop for :func:`render_document` — the spacing constants
    that flow together through layout/composite, bundled so a document's rendering config is one value."""

    base: Style | None = None
    padding: int = 14
    gap: int = 4
    background: RGBA = (0, 0, 0, 0)
    indent_px: int = INDENT_PX
    gutter_px: int = GUTTER_PX


_DEFAULT_STYLE = DocStyle()  # frozen → safe shared default


def render_document(
    blocks: list[Block],
    width: int,
    style: DocStyle | None = None,
    *,
    scan_out: list[ScanBox] | None = None,
    link_out: list[LinkBox] | None = None,
    max_height: int | None = None,
    clipped_out: list | None = None,
    y_window: tuple[int, int] | None = None,
) -> Image.Image:
    """Render blocks stacked top-to-bottom at a fixed panel width. ``style`` carries the spacing/base/
    background geometry (defaults to :data:`_DEFAULT_STYLE`).

    When ``scan_out`` is given, append per-character :class:`ScanBox`es (document-image coords) for
    nested scanning. When ``link_out`` is given, append per-link :class:`LinkBox`es in the same
    coords.

    ``max_height``: bound the rasterised height — each block's flow is capped at the remaining
    budget and blocks past the budget are skipped entirely; ``True`` is appended to
    ``clipped_out`` when anything was dropped. ``None`` = full render (byte-identical).

    ``y_window=(y0, y1)``: rasterise ONLY the band ``[y0, y1)`` into a ``(y1 - y0)``-tall image —
    pixel-identical to the full render cropped to that band, but drawing only the overlapping blocks
    (via :func:`render_flow_window` for the partially-visible ones). Overrides ``max_height``."""
    style = style or _DEFAULT_STYLE
    if y_window is not None:
        doc = layout_document(
            blocks,
            width,
            style.base,
            style.padding,
            style.gap,
            style.background,
            style.indent_px,
            style.gutter_px,
        )
        return _composite_window(doc, y_window[0], y_window[1], scan_out, link_out)
    base = style.base or Style()
    rendered = _render_blocks(blocks, width, style, max_height, clipped_out, scan_out, link_out)
    return _composite(
        rendered,
        width,
        style.padding,
        style.gap,
        style.gutter_px,
        base,
        style.background,
        scan_out,
        link_out,
    )
