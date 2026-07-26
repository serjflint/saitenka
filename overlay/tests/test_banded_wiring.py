"""Smoke test for the banded tooltip wiring (``SAITENKA_BANDED`` / ``Reader._banded``).

When the flag is on, the base tooltip renders each visible frame + hit-tests from the windowed
(banded) engine instead of slicing a whole-panel BGRA blob. This drives the real controller path and
asserts the windowed frames are pixel-identical to the blob slice at every offset (the swap safety net)
and that hit-testing still resolves — plus that the flag OFF leaves the blob path untouched."""

from __future__ import annotations

import numpy as np
from util import FakeIPC

from overlay.app.controller import SKIP_POS, Reader
from overlay.mpvio.osd import to_bgra_array
from overlay.panel import Definition, Entry


class _FakeDS:
    def entry_for(self, tok, inflected=None):
        para = "とても長い定義の本文で追いかける。" * 8  # tall + CJK → scrollable, yields scan cells
        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition(f"辞書{i}", [para]) for i in range(3)],
        )

    def has_term(self, *forms):
        return True


def _reader(*, banded: bool) -> Reader:
    # `banded=` flows through with_overrides → [tooltip].banded → Reader._banded (the real config path).
    r = Reader(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5, show_dict_tabs=False, banded=banded)
    r.osd = (1920, 1080)
    # No prefetch worker → _show_tooltip finishes the panel synchronously, so the blob is the WHOLE
    # panel (the parity reference), not a head strip a worker would still be filling.
    r._finish_available = lambda: False
    r.set_subtitle("本命を読む")
    return r


def test_banded_config_enables_it_and_logs(caplog):
    with caplog.at_level("INFO"):
        r = Reader(FakeIPC(), dict_set=_FakeDS(), banded=True)
    assert r._banded  # [tooltip].banded = true → windowed path on
    assert any("banded tooltip renderer ENABLED" in rec.message for rec in caplog.records)


def test_env_var_enables_banded(monkeypatch):
    monkeypatch.setenv("SAITENKA_BANDED", "1")
    assert Reader(FakeIPC(), dict_set=_FakeDS())._banded  # env wins over the config default


def _content_word(r: Reader) -> int:
    return next(i for i, t in enumerate(r.tokens) if t.is_content and t.pos not in SKIP_POS)


def test_flag_off_leaves_the_blob_path_untouched():
    r = _reader(banded=False)
    r._show_tooltip(_content_word(r))
    assert r._tip_state is not None
    assert r._tip_state.windowed is None  # no windowed panel built when the flag is off
    assert r._tip_rect is not None  # tooltip still renders via the blob slice


def test_banded_tooltip_renders_and_hit_tests_end_to_end():
    r = _reader(banded=True)
    i = _content_word(r)
    r._show_tooltip(i)
    st = r._tip_state
    assert st is not None and st.windowed is not None  # the windowed engine is wired in
    assert r._tip_rect is not None  # first frame composited + uploaded without error

    # Parity: the windowed viewport == the blob slice at every offset the tooltip can scroll to.
    blob = st.bgra()
    full_h, vh = blob.shape[0], r._tip_view_h
    assert full_h > vh, "entry should be tall enough to scroll"
    for scroll in range(0, full_h - vh + 1, max(1, (full_h - vh) // 6)):
        windowed = to_bgra_array(st.windowed.viewport(scroll, vh))
        assert np.array_equal(windowed, blob[scroll : scroll + vh]), f"mismatch at scroll={scroll}"

    # Scrolling drives the windowed re-composite without error.
    r._tip_scroll = 0
    r._scroll_tip(round(r.osd[1] * 0.12))
    assert r._tip_scroll > 0 and r._tip_rect is not None

    # Hit-testing: a point over a real scan cell resolves to that cell through the windowed path.
    r._tip_scroll = 0
    r._render_tip_view()  # render the top so its blocks' geometry is materialised
    cells = [b for b in st.lazy.scan_boxes if b.y < vh]  # a cell visible in the top viewport
    assert cells, "expected scan cells in the top viewport"
    cell = cells[0]
    sx, sy = r._tip_xy
    hit = r._scan_hit(sx + cell.x + cell.w // 2, sy + cell.y + cell.h // 2)
    assert hit is not None and hit.text == cell.text  # windowed hit == the blob-path cell
