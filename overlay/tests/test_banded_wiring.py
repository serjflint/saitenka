"""End-to-end wiring for the windowed (banded) tooltip renderer — the sole tooltip compositor.

Drives the real controller path: a hover composites each visible frame from the windowed engine and
hit-tests through its retained per-block geometry. Asserts the tooltip renders lazily (head first,
tail measured on scroll) and that a point over a scan cell resolves to that cell. Pixel parity of the
windowed viewport vs a one-shot render_panel crop lives in ``tests/test_windowed_panel.py``."""

from __future__ import annotations

from util import FakeIPC

from overlay.app.controller import SKIP_POS, Reader
from overlay.panel import Definition, Entry


class _FakeDS:
    def entry_for(self, tok, _inflected=None):
        # Far taller than head + one-screen overscan, so some blocks stay unmeasured after the first
        # paint — proving the lazy tail. CJK body → yields scan cells for the hit-test.
        para = "とても長い定義の本文で追いかける。" * 12
        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition(f"辞書{i}", [para]) for i in range(6)],
        )

    def has_term(self, *_forms):
        return True


def _reader() -> Reader:
    r = Reader(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5)
    r.osd = (1920, 1080)
    r.set_subtitle("本命を読む")
    return r


def _content_word(r: Reader) -> int:
    return next(i for i, t in enumerate(r.tokens) if t.is_content and t.pos not in SKIP_POS)


def test_tooltip_renders_lazily_and_hit_tests_end_to_end():
    r = _reader()
    r._show_tooltip(_content_word(r))
    st = r._tip_state
    assert st is not None and st.windowed is not None  # the windowed engine composites the tooltip
    assert r.hover_view().tip.rect is not None  # first frame composited + uploaded without error

    wp = st.windowed
    assert wp.measured < wp.count  # lazy: show measured only the head, not the whole tall panel
    assert st.full_height > r._tip_view_h, "entry should be tall enough to scroll"

    # Scrolling drives the windowed re-composite (and measures more blocks) without error.
    before = wp.measured
    r._scroll_tip(round(r.osd[1] * 0.12))
    assert r._tip_scroll > 0 and r.hover_view().tip.rect is not None
    assert wp.measured >= before

    # Hit-testing: a point over a real scan cell resolves to that cell through the windowed path.
    r._tip_scroll = 0
    r._render_tip_view()  # materialise the top blocks' geometry
    cells = [b for b in wp.scan_boxes() if b.y < r._tip_view_h]  # a cell in the top viewport
    assert cells, "expected scan cells in the top viewport"
    cell = cells[0]
    sx, sy = r._tip_xy
    hit = r._scan_hit(sx + cell.x + cell.w // 2, sy + cell.y + cell.h // 2)
    assert hit is not None and hit.text == cell.text
