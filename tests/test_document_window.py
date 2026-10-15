"""A windowed flow or document must equal the same band cropped from its full render.

The shared contract covers the primitive and its document composition. Document-only cases retain
ownership of block seams and window-space metadata."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from saitenka.model import Span, Style
from saitenka.render.document import render_document
from saitenka.render.flow import render_flow
from saitenka.render.layout import Block as FlowBlock
from saitenka.sc.model import Block as DocumentBlock

S = Style(size=23)
WIDTH = 340


def _document_blocks() -> list[DocumentBlock]:
    # A tall, mixed document: paragraphs + list items (markers, indents, gaps at block seams) so the
    # window has real block boundaries to span, not one uniform flow.
    para = [Span("これは長い定義の本文でありスクロールが必要になる説明です。", S)]
    out: list[DocumentBlock] = []
    for i in range(12):
        if i % 3 == 0:
            out.append(DocumentBlock(flow=para, kind="list-item", list_type="ol", ordinal=i + 1))
        elif i % 3 == 1:
            out.append(DocumentBlock(flow=para, kind="list-item", list_type="ul", indent=1))
        else:
            out.append(DocumentBlock(flow=para))
    return out


def _document_full():
    return render_document(_document_blocks(), WIDTH)


def _document_window(y_window):
    return render_document(_document_blocks(), WIDTH, y_window=y_window)


def _flow() -> list[Span]:
    return [
        Span("これは長い定義の本文でありスクロールが必要になる説明です。", S) for _ in range(60)
    ]


def _flow_block() -> FlowBlock:
    return FlowBlock(width=320, background=(0, 0, 0, 0))


def _flow_full():
    return render_flow(_flow(), _flow_block())


def _flow_window(y_window):
    return render_flow(_flow(), _flow_block(), y_window=y_window)


RENDERERS = [
    pytest.param(_flow_full, _flow_window, id="flow"),
    pytest.param(_document_full, _document_window, id="document"),
]


@pytest.mark.parametrize(("render_full", "render_window"), RENDERERS)
def test_full_window_equals_the_whole_render(render_full, render_window):
    full = render_full()
    win = render_window((0, full.height))
    assert np.array_equal(np.asarray(win), np.asarray(full))


@given(data=st.data())
@settings(max_examples=40, deadline=None)
@pytest.mark.parametrize(("render_full", "render_window"), RENDERERS)
def test_window_is_pixel_identical_to_the_full_crop(data, render_full, render_window):
    full = render_full()
    h = full.height
    y0 = data.draw(st.integers(0, max(0, h - 1)))
    y1 = data.draw(st.integers(y0 + 1, h))
    win = render_window((y0, y1))
    ref = full.crop((0, y0, full.width, y1))
    assert win.size == ref.size
    assert np.array_equal(np.asarray(win), np.asarray(ref))


@pytest.mark.parametrize(("render_full", "render_window"), RENDERERS)
@pytest.mark.parametrize("band", [(0, 1), (0, 5), (7, 8)])
def test_window_at_exact_boundaries_matches_the_crop(band, render_full, render_window):
    full = render_full()
    y0, y1 = band
    win = render_window((y0, y1))
    ref = full.crop((0, y0, full.width, y1))
    assert np.array_equal(np.asarray(win), np.asarray(ref))


def test_window_across_a_block_seam_matches_the_crop():
    # A window straddling two blocks (gap + marker of the next) is the case a single-flow window can't
    # exercise; assert identity spanning the seam explicitly.
    full = _document_full()
    mid = full.height // 2
    win = _document_window((mid - 30, mid + 30))
    ref = full.crop((0, mid - 30, full.width, mid + 30))
    assert np.array_equal(np.asarray(win), np.asarray(ref))


def test_window_carries_scan_and_link_boxes_in_window_space():
    # Scan boxes from a mid-document window must land at (full_y - y0), matching the full-render boxes
    # shifted into the window — so a hover over a windowed band resolves to the same cell.
    full_scan: list = []
    render_document(_document_blocks(), WIDTH, scan_out=full_scan)
    y0, y1 = 200, 360
    win_scan: list = []
    render_document(_document_blocks(), WIDTH, y_window=(y0, y1), scan_out=win_scan)
    full_shifted = {(b.text, b.x, b.y - y0, b.w, b.h) for b in full_scan}
    # every windowed box fully inside the band equals a full box shifted by -y0 (a partially-clipped
    # top/bottom line may differ in height, so restrict to boxes wholly within [0, y1-y0))
    inside = [b for b in win_scan if b.y >= 0 and b.y + b.h <= y1 - y0]
    assert inside  # the window actually produced (fully-inside) scan boxes
    for b in inside:
        assert (b.text, b.x, b.y, b.w, b.h) in full_shifted
