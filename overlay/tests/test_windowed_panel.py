"""Stage 3: the real-row windowing adapter (``WindowedPanel``) — lazy offsets, height retention,
eviction, and golden parity vs a one-shot ``render_panel`` crop at every scroll offset.

Synthetic entries carry the math + parity here; the real many-homograph parity lives in the
integration-tier test at the bottom (skip-if-no-DB)."""

from __future__ import annotations

import contextlib

import numpy as np
import pytest
import util
from hypothesis import example, given, settings
from hypothesis import strategies as st
from overlay.panel import Entry, LazyPanel, panel_rows, render_panel
from overlay.render.banded import BandedTuning, WindowedPanel

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
    except Exception:  # noqa: BLE001  # DB file missing / unreadable on this machine → integration skip
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
    return util.tall_entry(n_defs)  # canonical shape lives in the shared matrix (anti-drift)


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


def test_def_body_rows_carry_the_windowed_api_and_agree_with_render():
    # Stage 5: only def-body rows get measure/render_window; both close over ONE layout handle and
    # agree with the full render (measure == render height, a band == the full crop).
    import numpy as np

    rows = panel_rows(_tall_entry(4), WIDTH)
    body_rows = [r for r in rows if r.body_args is not None]
    assert body_rows  # the def bodies
    assert all(r.measure is not None and r.render_window is not None for r in body_rows)
    # header/chip rows stay simple — no windowed API to split a one-band row
    assert all(r.measure is None and r.render_window is None for r in rows if r.body_args is None)
    row = body_rows[0]
    assert row.measure is not None and row.render_window is not None
    full = row.render()[0]
    assert row.measure() == full.height  # measure matches the full raster, no getmask2
    win, _scan, _links = row.render_window(0, full.height)
    assert np.array_equal(np.asarray(win), np.asarray(full))


def test_cold_first_paint_renders_only_the_head():
    entry = _tall_entry(8)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport(0, 300)
    assert wp.measured < wp.count  # a cold viewport measured only the head blocks, not all 17
    assert wp.cached_blocks < wp.count


def test_measure_to_warms_the_head_prefix_without_compositing():
    entry = _tall_entry(8)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.measure_to(300)  # warm/measure the head for placement — no viewport image built
    assert 0 < wp.measured < wp.count  # only the head prefix measured, not all 17 blocks
    assert wp.cached_blocks > 0  # the measured blocks are cached (a later hover is a hit)
    # the estimate is now backed by real measured heights and matches a viewport composite's clamp
    assert wp.full_height > 0


def test_retained_nbytes_is_smaller_when_blocks_are_compressed():
    entry = _tall_entry(8)
    live = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(compress=False))
    packed = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(compress=True))
    live.viewport(0, 300)
    packed.viewport(0, 300)
    assert live.retained_nbytes > 0
    # the mostly-transparent panel blocks compress hard → the warmed cache footprint is far smaller
    assert packed.retained_nbytes < live.retained_nbytes


@pytest.mark.parametrize("profile", util.PROFILES, ids=[p.id for p in util.PROFILES])
@given(scroll_frac=st.floats(0, 1), view_h=st.integers(120, 500))
@settings(max_examples=30, deadline=None)
# Pinned regression: at scale 2.0 the header row is >_BAND_PX(256) tall, so scrolling it partly off the
# top exposed the composite's non-body band-split bug (2nd band left blank). Deterministic on every run.
@example(scroll_frac=0.078125, view_h=120)
def test_viewport_is_pixel_identical_to_render_panel_crop(profile, scroll_frac, view_h):
    # windowed viewport == one-shot render_panel crop, now ACROSS the scale × width × entry matrix (was
    # only Theme() @ WIDTH). The engine must window byte-identically at hi-dpi — the crisp native panel
    # rasters at Theme(scale)/width×scale, a path the old scale-1.0 property never exercised.
    ref_full = (
        profile.reference_render()
    )  # single-source (width, theme): the panel and its crop agree
    total = ref_full.height
    view_h = min(view_h, total)
    scroll = int(scroll_frac * max(0, total - view_h))
    wp = profile.windowed()
    win = wp.viewport(scroll, view_h)
    ref = ref_full.crop((0, scroll, profile.width, scroll + view_h))
    assert win.size == ref.size
    diff = np.abs(np.asarray(win, np.int16) - np.asarray(ref, np.int16))
    assert diff.max() == 0


@given(scroll_frac=st.floats(0, 1), view_h=st.integers(120, 500))
@settings(max_examples=60, deadline=None)
def test_viewport_bgra_equals_to_bgra_array_of_viewport(scroll_frac, view_h):
    # #138 fast path: the per-band BGRA assembly (disjoint numpy row-copies) is byte-identical to
    # converting the whole RGBA viewport per frame — the invariant that lets blit_panel skip the
    # per-frame whole-viewport RGBA→BGRA convert.
    from overlay.mpvio.osd import to_bgra_array

    entry = _tall_entry(8)
    total = _full_height(entry)
    view_h = min(view_h, total)
    scroll = int(scroll_frac * max(0, total - view_h))
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    fast = wp.viewport_bgra(scroll, view_h)
    ref = to_bgra_array(wp.viewport(scroll, view_h))
    assert np.array_equal(fast, ref)


def test_viewport_bgra_matches_after_scroll_away_and_back():
    # A re-stored (scrolled-away-then-back) band must invalidate the BGRA memo, so the fast path never
    # serves stale pixels. Scroll to the bottom, back to the top, and the top frame must still match.
    from overlay.mpvio.osd import to_bgra_array

    entry = _tall_entry(8)
    total = _full_height(entry)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport_bgra(max(0, total - 200), 200)  # bottom → evicts the top bands
    fast = wp.viewport_bgra(0, 200)  # back to the top → top bands re-rastered
    ref = to_bgra_array(wp.viewport(0, 200))
    assert np.array_equal(fast, ref)


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
