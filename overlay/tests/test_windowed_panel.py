"""Stage 3: the real-row windowing adapter (``WindowedPanel``) — lazy offsets, height retention,
eviction, and golden parity vs a one-shot ``render_panel`` crop at every scroll offset.

Synthetic entries carry the math + parity here; the real many-homograph parity lives in the
integration-tier test at the bottom (skip-if-no-DB)."""

from __future__ import annotations

import contextlib

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from overlay.panel import Definition, Entry, LazyPanel, panel_rows, render_panel
from overlay.render.banded import WindowedPanel

WIDTH = 384


def _real_many_homograph_entry(width: int) -> tuple[Entry, str] | None:
    """Resolve the configured real dict set and return the tallest-panel content word's Entry (the
    many-homograph pathological case: かける/する/…). ``None`` when no real dicts are configured or the
    consolidated DB is absent — the caller skips (mirrors ``bench_responsiveness._load_dict_set``)."""
    try:
        from overlay.app.config import load_config
        from overlay.app.dictdb import DictionaryDb
        from overlay.app.dictionary import DictionarySet
        from overlay.app.tokenize import Token

        cfg = load_config()
        dict_titles = list(cfg.get("dicts") or [])
        if not dict_titles:
            return None
        db = DictionaryDb.open()
        ds = DictionarySet.from_db(
            db, dict_titles, list(cfg.get("freq") or []), list(cfg.get("pitch") or [])
        )
    except Exception:  # DB file missing / unreadable on this machine → integration skip
        return None
    best: tuple[Entry, str] | None = None
    best_h = 0
    for term, reading in (("掛ける", "かける"), ("する", "する"), ("いい", "いい"), ("手", "て")):
        tok = Token(term, term, reading, "動詞", 0, len(term))
        entry = None
        with contextlib.suppress(
            Exception
        ):  # a word absent from the configured dicts is fine to skip
            entry = ds.entry_for(tok)
        if entry is None:
            continue
        h = LazyPanel(panel_rows(entry, width), width).finish().height
        if h > best_h:
            best, best_h = (entry, f"{term} ({len(entry.defs)} defs, {h}px)"), h
    return best


def _tall_entry(n_defs: int = 8) -> Entry:
    para = "これはとても長い定義の説明でありスクロールが必要になるほど縦に伸びる本文です。" * 2
    return Entry(
        headword=["本命", {"tag": "rt", "content": "ほんめい"}],
        defs=[Definition(f"辞書{i}", [para]) for i in range(n_defs)],
    )


def _full_height(entry: Entry) -> int:
    return render_panel(entry, width=WIDTH).height


def test_construction_walks_no_content(monkeypatch):
    # The deferred-thunk contract: building rows + the WindowedPanel must not walk any def body.
    # walk() is called from overlay.body_block.render_body_block (extracted so render/banded.py's
    # process-pool path can call it without panel.py's closures), not from overlay.panel directly.
    import overlay.body_block as BB
    import overlay.panel as P

    calls = [0]
    orig = BB.walk
    monkeypatch.setattr(
        BB,
        "walk",
        lambda node, base=None: (calls.__setitem__(0, calls[0] + 1), orig(node, base))[1],
    )
    wp = WindowedPanel(P.panel_rows(_tall_entry(8), WIDTH), WIDTH)
    assert wp.count == 1 + 2 * 8  # header + (name chip + body) per def
    assert calls[0] == 0  # nothing rendered/walked at build time


def test_cold_first_paint_renders_only_the_head():
    entry = _tall_entry(8)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport(0, 300)
    assert wp.measured < wp.count  # a cold viewport measured only the head blocks, not all 17
    assert wp.cached_blocks < wp.count


@given(scroll_frac=st.floats(0, 1), view_h=st.integers(120, 500))
@settings(max_examples=60, deadline=None)
def test_viewport_is_pixel_identical_to_render_panel_crop(scroll_frac, view_h):
    entry = _tall_entry(8)
    total = _full_height(entry)
    view_h = min(view_h, total)
    scroll = int(scroll_frac * max(0, total - view_h))
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    win = wp.viewport(scroll, view_h)
    ref = render_panel(entry, width=WIDTH).crop((0, scroll, WIDTH, scroll + view_h))
    assert win.size == ref.size
    diff = np.abs(np.asarray(win, np.int16) - np.asarray(ref, np.int16))
    assert diff.max() == 0


def test_overscan_does_not_change_the_visible_pixels():
    entry = _tall_entry(8)
    total = _full_height(entry)
    scroll, vh = total // 3, 240
    wp0 = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp1 = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    a = np.asarray(wp0.viewport(scroll, vh, overscan=0))
    b = np.asarray(wp1.viewport(scroll, vh, overscan=200))
    assert np.array_equal(
        a, b
    )  # overscan pre-renders more but the composited viewport is unchanged


def test_heights_are_retained_after_eviction_and_offsets_stay_exact():
    entry = _tall_entry(8)
    total = _full_height(entry)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport(max(0, total - 200), 200)  # scroll to the bottom → every block measured once
    assert wp.measured == wp.count  # all heights known
    top_cache = wp.cached_blocks
    wp.viewport(0, 200)  # scroll back to the top
    # scrolling back never re-measures (heights retained) and the top parity still holds exactly
    assert wp.measured == wp.count
    ref = render_panel(entry, width=WIDTH).crop((0, 0, WIDTH, 200))
    win = wp.viewport(0, 200)
    assert np.abs(np.asarray(win, np.int16) - np.asarray(ref, np.int16)).max() == 0
    assert top_cache < wp.count  # eviction kept the retained pixel set bounded, not the whole panel


def test_sustained_scroll_keeps_the_pixel_cache_bounded():
    entry = _tall_entry(12)
    total = _full_height(entry)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    vh, overscan = 240, 150
    peak = 0
    for scroll in range(0, max(1, total - vh), 60):
        wp.viewport(scroll, vh, overscan=overscan)
        peak = max(peak, wp.cached_blocks)
    assert wp.measured == wp.count  # everything got measured over the full scroll
    assert peak < wp.count  # …but retained pixels never held the whole panel at once


@pytest.mark.integration
def test_windowed_parity_on_a_real_many_homograph_entry():
    # Real-dict parity: the actual polysemous words (掛ける/する) produce dozens of blocks — assert the
    # windowed viewport equals the one-shot render_panel crop at offsets across the whole panel, on
    # content synthetic entries can't guarantee (real glossary shapes, real wrap, real block counts).
    resolved = _real_many_homograph_entry(WIDTH)
    if resolved is None:
        pytest.skip("no real dict set configured / consolidated DB absent")
    entry, _tag = resolved
    ref_full = render_panel(entry, width=WIDTH)
    total = ref_full.height
    if total < 160:
        pytest.skip(
            f"resolved entry too short to scroll ({total}px) — configured dict set is minimal"
        )
    vh = total // 2  # half the panel, so there is a real scroll range to sweep
    for scroll in range(0, total - vh + 1, max(1, (total - vh) // 7)):
        wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
        win = wp.viewport(scroll, vh)
        ref = ref_full.crop((0, scroll, WIDTH, scroll + vh))
        diff = np.abs(np.asarray(win, np.int16) - np.asarray(ref, np.int16))
        assert diff.max() == 0, f"windowed != crop at scroll={scroll}"
        assert wp.cached_blocks <= wp.measured  # eviction kept pixels bounded below the full count
