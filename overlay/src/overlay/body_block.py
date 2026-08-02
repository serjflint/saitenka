"""One def-body block's render: the SC-walk + document layout, as plain picklable inputs.

A TOP-LEVEL module (sibling to ``panel.py``/``model.py``), not nested under ``render/`` — it needs
``sc.walk`` (structured-content walking), and ``sc.walk`` itself already imports from
``render.flow``, so a copy living inside the ``render`` package would create a
``render -> sc -> render`` cycle. Same reason ``Theme``/``Style`` live in ``overlay.model`` rather
than ``panel.py``. Exists so ``render/banded.py`` can call :func:`render_body_block` (its GIL-build
process-pool path) without ``panel.py``'s ``panel_rows()`` closures in the way. ``panel.py`` imports
and re-exports from here so ``from overlay.panel import BodyRenderArgs`` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay.render.document import DocLayout, layout_document, render_document
from overlay.sc.walk import walk

if TYPE_CHECKING:
    from PIL import Image

    from overlay.model import LinkBox, ScanBox, Style

# A structured-content node (Yomitan SC): plain text, a tag dict, or a list of nodes.
type SCNode = str | dict | list


@dataclass(frozen=True, slots=True)
class BodyRenderArgs:
    """Picklable description of one def-body block's render inputs — everything
    :func:`render_body_block` needs, with no closures. Extracted so a process-pool worker
    (``render/banded.py``'s GIL-build parallel path — threads give zero speedup for this CPU-bound
    work without a free-threaded build, per ``examples/bench_parallelism.py``) can render a block
    without any of ``panel.py``'s ``panel_rows()`` closures — or the shared ``WindowedPanel`` state
    they'd otherwise drag along — crossing the process boundary. Only ``content`` and ``body_style``
    carry real data; the rest are theme pixel sizes, all plain values."""

    content: SCNode
    body_style: Style
    body_w: int
    gap_px: int
    indent_px: int
    gutter_px: int


@dataclass(frozen=True, slots=True)
class LaidOutBody:
    """One def-body block walked + laid out (wrap → line boxes → full height) but NOT drawn — the
    reusable measure half of :func:`render_body_block`. Cache one per row and raster any y-window of it
    (:func:`raster_body_window`) without re-paying the ``walk`` (~200ms on pathological entries) or the
    re-wrap: only the ``getmask2`` of the requested band. :attr:`full_height` is the row's exact height
    (drives the scroll offset table), known without any raster."""

    doc: DocLayout

    @property
    def full_height(self) -> int:
        return self.doc.full_height

    def geometry(self) -> tuple[list[ScanBox], list[LinkBox]]:
        """Whole-body scan/link hitboxes (body-local) from the layout, no raster — so a measured-but-not-
        yet-rastered row is already hoverable (the banded engine seeds ``_geom`` from this at measure)."""
        from overlay.render.document import document_geometry

        return document_geometry(self.doc)


def layout_body_block(args: BodyRenderArgs) -> LaidOutBody:
    """Walk + lay out one def-body block WITHOUT drawing — cheap enough to run ahead of the viewport so
    a row's first band never pays the walk synchronously (see the PR3 crux)."""
    doc = layout_document(
        walk(args.content, args.body_style),
        width=args.body_w,
        base=args.body_style,
        padding=0,
        gap=args.gap_px,
        indent_px=args.indent_px,
        gutter_px=args.gutter_px,
        background=(0, 0, 0, 0),
    )
    return LaidOutBody(doc)


def raster_body_window(
    laid: LaidOutBody, y0: int, y1: int
) -> tuple[Image.Image, list[ScanBox], list[LinkBox]]:
    """Rasterise the ``[y0, y1)`` band of an already-laid-out body — pixel-identical to
    :func:`render_body_block`'s full image cropped to that band, but drawing O(band) not O(block)."""
    from overlay.render.document import render_layout_window

    scan: list[ScanBox] = []
    links: list[LinkBox] = []
    img = render_layout_window(laid.doc, y0, y1, scan, links)
    return img, scan, links


def render_body_band(
    args: BodyRenderArgs, y0: int, y1: int
) -> tuple[Image.Image, list[ScanBox], list[LinkBox]]:
    """Picklable one-shot band render from plain args — ``layout_body_block`` then
    ``raster_body_window``. The process-pool (GIL-build) render-ahead path uses this: it re-walks per
    call (no shared handle across processes), but bands are the target unit so the free-threaded and GIL
    paths warm the SAME bands. On free-threading the in-process ``Row.render_window`` (memoised layout)
    is used instead — no re-walk."""
    return raster_body_window(layout_body_block(args), y0, y1)


def render_body_block(
    args: BodyRenderArgs, max_h: int | None = None
) -> tuple[Image.Image, list[ScanBox], list[LinkBox], bool]:
    """Render one def-body block: the SC-walk + document layout shared by ``panel.py``'s deferred
    ``Row.render``/``render_capped`` thunks (which just call this with their captured args) and the
    process-pool path in ``render/banded.py``. ``max_h=None`` is the uncapped, always-complete form
    (``Row.render``'s contract); a real value mirrors ``render_capped``'s mid-block clip — the
    covering strip for a pathologically tall single block, not the whole thing (see panel.py's
    ``Row.render_capped`` docstring for why this bounds cold first paint to O(viewport))."""
    scan: list[ScanBox] = []
    links: list[LinkBox] = []
    clipped: list = []
    img = render_document(
        walk(args.content, args.body_style),
        width=args.body_w,
        base=args.body_style,
        padding=0,
        gap=args.gap_px,
        indent_px=args.indent_px,
        gutter_px=args.gutter_px,
        background=(0, 0, 0, 0),
        scan_out=scan,
        link_out=links,
        max_height=max_h,
        clipped_out=clipped,
    )
    return img, scan, links, not clipped
