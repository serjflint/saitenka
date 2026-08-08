"""Stage 2: the windowed viewport compositor equals a one-shot stack crop, byte-for-byte.

Synthetic column only (numbered AA text + a pasted colour block per row) — the real panel arrives in
Stage 3. Blocks carry anti-aliased CJK+Latin glyphs precisely because the seam invariant
(``test_lazy_panel.py::test_integer_y_band_split_is_pixel_exact``) is what makes the disjoint copy
exact; any off-by-one vertical placement fringes a glyph and the pixel diff catches it."""

from __future__ import annotations

import numpy as np
from hypothesis import example, given, settings
from hypothesis import strategies as st
from overlay.mpvio.osd import to_bgra_array
from overlay.panel import Theme, compose_panel
from overlay.render.banded import composite_window
from overlay.render.window import build_offsets
from PIL import Image, ImageDraw

from overlay import fonts

WIDTH = 384
BG = (252, 252, 250, 255)  # opaque panel background (Theme().bg)
_FONT = fonts.load(fonts.FontSpec("NotoSansJP.ttf", 22))


def _block(index: int, height: int) -> Image.Image:
    """A distinctive transparent RGBA block: a tinted fill, AA text lines, and a solid colour swatch —
    so any vertical misplacement (even a 1px shift) produces a pixel diff against the one-shot stack."""
    im = Image.new("RGBA", (WIDTH - 32, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    tint = (index * 37 % 256, index * 53 % 256, index * 29 % 256, 40)
    d.rectangle((0, 0, im.width - 1, height - 1), fill=tint)
    d.rectangle((4, 4, 40, min(height - 4, 24)), fill=(index * 61 % 256, 90, 160, 255))  # swatch
    for row, y in enumerate(range(2, height - 10, 20)):
        d.text((48, y), f"blk{index} 行{row} fi—明鏡", font=_FONT, fill=(20, 20, 20, 255))
    return im


@st.composite
def column(draw):
    """A synthetic column: N blocks with random realistic heights + gaps, at margin x-offset, plus a
    scroll offset and viewport clamped to real usage (0 <= scroll <= total - viewport)."""
    n = draw(st.integers(1, 20))
    heights = [draw(st.integers(12, 220)) for _ in range(n)]
    gaps = [draw(st.integers(0, 10)) for _ in range(n)]
    m = Theme().margin
    table = build_offsets(heights, gaps, m, m)
    blocks = [(m, _block(i, h)) for i, h in enumerate(heights)]
    viewport = draw(st.integers(20, min(600, table.total)))
    scroll = draw(st.integers(0, max(0, table.total - viewport)))
    overscan = draw(st.integers(0, 150))
    return blocks, heights, gaps, table, scroll, viewport, overscan


def _one_shot_crop(blocks, gaps, scroll, viewport) -> Image.Image:
    """The reference: compose the WHOLE stack once (compose_panel geometry) and crop the viewport."""
    full = compose_panel([(x, im) for x, im in blocks], WIDTH, Theme(), gaps=gaps)
    return full.crop((0, scroll, WIDTH, scroll + viewport))


@given(column())
@settings(max_examples=200, deadline=None)
@example(  # a mid-glyph cut: scroll lands inside a text line, exercising the seam directly
    (
        [(16, _block(0, 100)), (16, _block(1, 100))],
        [100, 100],
        [7, 7],
        build_offsets([100, 100], [7, 7], 16, 16),
        63,
        80,
        0,
    )
)
def test_windowed_composite_is_pixel_identical_to_one_shot_crop(col):
    blocks, _heights, gaps, table, scroll, viewport, overscan = col
    win = composite_window(
        blocks, table, scroll, viewport, width=WIDTH, background=BG, overscan=overscan
    )
    ref = _one_shot_crop(blocks, gaps, scroll, viewport)
    assert win.size == ref.size
    diff = np.abs(np.asarray(win, np.int16) - np.asarray(ref, np.int16))
    assert diff.max() == 0  # windowed assembly == one-shot crop, seam included


@given(column())
@settings(max_examples=100, deadline=None)
def test_bgra_upload_path_is_seam_exact(col):
    # The real upload converts to premultiplied BGRA then slices; windowed must match that slice.
    blocks, _heights, gaps, table, scroll, viewport, overscan = col
    win = composite_window(
        blocks, table, scroll, viewport, width=WIDTH, background=BG, overscan=overscan
    )
    win_bgra = to_bgra_array(win)
    full_bgra = to_bgra_array(
        compose_panel([(x, im) for x, im in blocks], WIDTH, Theme(), gaps=gaps)
    )
    ref_bgra = full_bgra[scroll : scroll + viewport]
    assert np.array_equal(win_bgra, ref_bgra)  # no double-darkening at a join


@given(column())
@settings(max_examples=100, deadline=None)
def test_premultiplied_disjoint_numpy_copy_matches_reference(col):
    # Stage-4 fast path: pre-convert each block (composited over bg) to premultiplied BGRA once, then
    # assemble a viewport by disjoint numpy row-copies. Must equal the one-shot BGRA crop.
    blocks, _heights, gaps, table, scroll, viewport, overscan = col
    bg_bgra = to_bgra_array(Image.new("RGBA", (1, 1), BG))[0, 0]
    block_bgra = []
    for x, im in blocks:
        canvas = Image.new("RGBA", (WIDTH, im.height), BG)
        canvas.alpha_composite(
            im, (x, 0)
        )  # composite over bg → opaque → plain-overwrite copy is exact
        block_bgra.append(to_bgra_array(canvas))
    out = np.empty((viewport, WIDTH, 4), np.uint8)
    out[:] = bg_bgra
    for i in range(*table.visible_range(scroll, viewport, overscan)):
        top = table.starts[i] - scroll
        src_y0 = max(0, -top)
        dst_y = max(0, top)
        h = min(block_bgra[i].shape[0] - src_y0, viewport - dst_y)
        if h <= 0:
            continue
        out[dst_y : dst_y + h] = block_bgra[i][src_y0 : src_y0 + h]
    ref = to_bgra_array(compose_panel([(x, im) for x, im in blocks], WIDTH, Theme(), gaps=gaps))[
        scroll : scroll + viewport
    ]
    assert np.array_equal(out, ref)
