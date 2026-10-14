"""N1: viewport-first panel rendering — render the visible top, defer the rest."""

import threading

import numpy as np
import pytest

from saitenka.panel import (
    Definition,
    Entry,
    LazyPanel,
    Theme,
    compose_panel,
    header_add_rect,
    header_speaker_rect,
    panel_rows,
    render_panel,
)

WIDTH = 384


def _tall_entry(n_defs: int = 6) -> Entry:
    # each def body is a paragraph long enough to take real vertical space
    para = "これはとても長い定義の説明でありスクロールが必要になるほど縦に伸びる本文です。" * 2
    return Entry(
        headword=["本命", {"tag": "rt", "content": "ほんめい"}],
        defs=[Definition(f"辞書{i}", [para]) for i in range(n_defs)],
    )


def _split_first_def_entry():
    para = "とても長い定義の本文であり視界を超えて縦に伸びていく説明文です。" * 2
    return Entry(
        headword=["本命"],
        defs=[Definition("MonoA", [{"tag": "div", "content": [para]} for _ in range(10)])],
    )


def _multi_definition_entry():
    return Entry(
        headword=["観る", {"tag": "rt", "content": "みる"}],
        defs=[
            Definition(
                "MonoA", [{"tag": "div", "content": [f"意味{i}：説明文。"]} for i in range(6)]
            ),
            Definition("JMdict", ["to watch; to view"]),
        ],
    )


def _huge_single_block_entry(n_senses: int = 200) -> Entry:
    """A pathological first def: 200 senses in ONE block (br-separated prose, not an <ol>), the way
    some monolingual dicts store 取る — walk() yields a single enormous Block, so per-block streaming
    (N6) can't bound it; only mid-block clipping (Stage 6) can."""
    senses: list = []
    for i in range(n_senses):
        senses.append(f"{i + 1}. とても長い意味の説明文がここに続いて縦に伸びていく。")
        senses.append({"tag": "br"})
    return Entry(
        headword=["取る", {"tag": "rt", "content": "とる"}],
        defs=[Definition("MonoB", [{"tag": "div", "content": senses}])],
    )


@pytest.mark.parametrize(
    "entry", [_tall_entry(6), _split_first_def_entry(), _huge_single_block_entry()]
)
def test_render_to_defers_below_the_fold_rows(entry):
    lp = LazyPanel(panel_rows(entry, WIDTH), WIDTH)
    head = lp.render_to(300)
    assert 300 <= head.height <= 420
    assert not lp.complete

    full = lp.finish()

    assert lp.complete
    assert full.height > head.height


def test_panel_rows_defers_walk_to_render(monkeypatch):
    # Stage 6: building rows must not walk ANY def content (the SC-walk of a huge 取る-class def
    # costs 200+ ms — measured), and the head must walk only the defs the viewport shows.
    # walk() is called from panel.body.render_body_block so the process-pool path can use a module-level
    # worker without capturing row closures.
    import saitenka.panel as P
    import saitenka.panel.body as BB

    calls = [0]
    orig = BB.walk

    def counting(node, base=None, media=None):
        calls[0] += 1
        return orig(node, base, media)

    monkeypatch.setattr(BB, "walk", counting)
    rows = P.panel_rows(_tall_entry(6), WIDTH)
    assert calls[0] == 0, "panel_rows walked def content at build time"
    LazyPanel(rows, WIDTH).render_to(300)
    assert calls[0] < 6, "the head walked below-the-fold defs"


def test_partial_head_scan_boxes_stay_inside_the_strip():
    # The partial strip's scan boxes must be valid for the head image (no below-the-fold boxes).
    lp = LazyPanel(panel_rows(_huge_single_block_entry(), WIDTH), WIDTH)
    head = lp.render_to(300)
    for sb in lp.scan_boxes:
        assert sb.y >= 0 and sb.y + sb.h <= head.height


def test_thunks_run_at_most_once_each(monkeypatch):
    # Rows render through TWO seams since Stage 6: the full thunk (``render``) and the bounded
    # strip (``render_capped`` — full when it doesn't clip). Contract: every row is FULLY rendered
    # exactly once across head + finish; at most one bounded partial strip is rendered extra (the
    # boundary row of the head, re-rendered fully by finish).
    #
    # Pinned to the free-threaded (thread-pool) shared_executor branch: a GIL-build finish() calls
    # ``render_body_block(row.body_args)`` directly for def-body rows (the whole point of the
    # picklable-args split — a process pool can't cross a closure) instead of through ``row.render``,
    # which this test's per-row monkeypatch can't see. See
    # test_windowed_prefetch.test_parallel_and_sequential_render_ahead_agree for the same pin.
    import saitenka.parallel as PA

    monkeypatch.setattr(PA, "is_free_threaded", lambda: True)
    PA.shutdown_shared_executor()

    full, partial = [0], [0]
    # These thunks run concurrently on the forced free-threaded executor, so the instrumentation
    # counters need a lock — a bare ``full[0] += 1`` is a non-atomic read-modify-write and loses
    # increments under nogil (the count came out 12 vs 13 on CI). Only the counter is guarded.
    count_lock = threading.Lock()

    def _count_render(_orig):
        def thunk():
            with count_lock:
                full[0] += 1
            return _orig()

        return thunk

    def _count_capped(_orig):
        def capped(max_h):
            img, scan, links, complete = _orig(max_h)
            with count_lock:
                (full if complete else partial)[0] += 1
            return img, scan, links, complete

        return capped

    rows = panel_rows(_tall_entry(6), WIDTH)
    for r in rows:
        r.render = _count_render(r.render)
        if r.render_capped is not None:
            r.render_capped = _count_capped(r.render_capped)
    lp = LazyPanel(rows, WIDTH)
    try:
        lp.render_to(300)
        head_full = full[0]
        assert head_full < len(rows)  # a cold hover does NOT render every row
        lp.finish()
        assert full[0] == len(rows)  # each row fully rendered exactly once overall
        assert partial[0] <= 1  # ≤ one bounded strip (the head's boundary row)
    finally:
        PA.shutdown_shared_executor()


@pytest.mark.parametrize(
    ("entry", "viewport"),
    [
        (_multi_definition_entry(), 120),
        (_huge_single_block_entry(40), 300),
        (_tall_entry(5), 200),
    ],
)
def test_finish_is_pixel_identical_to_render_panel(entry, viewport):
    lp = LazyPanel(panel_rows(entry, WIDTH), WIDTH)
    lp.render_to(viewport)
    streamed = lp.finish()  # …then completed
    oneshot = render_panel(entry, width=WIDTH)
    assert streamed.size == oneshot.size
    diff = np.abs(np.asarray(streamed, np.int16) - np.asarray(oneshot, np.int16))
    assert diff.max() == 0  # streaming in two passes composes byte-for-byte the same panel


def test_finish_dispatches_to_shared_executor_on_both_builds(monkeypatch):
    # LazyPanel.finish() fans multi-row panels out to overlay.parallel.shared_executor — threads on
    # a free-threaded build, a process pool (def-body rows only) on a GIL build. Same dispatch
    # WindowedPanel.render_ahead already uses; simulate both builds like
    # test_windowed_prefetch.test_parallel_and_sequential_render_ahead_agree.
    import saitenka.parallel as PA

    e = _tall_entry(6)
    oneshot = render_panel(e, width=WIDTH)
    try:
        for ft in (True, False):
            PA.shutdown_shared_executor()
            monkeypatch.setattr(PA, "is_free_threaded", lambda ft=ft: ft)
            lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
            full = lp.finish()
            assert lp.complete
            assert full.size == oneshot.size
            diff = np.abs(np.asarray(full, np.int16) - np.asarray(oneshot, np.int16))
            assert diff.max() == 0
    finally:
        PA.shutdown_shared_executor()


def test_finish_with_a_single_pending_row_skips_the_pool(monkeypatch):
    # A defless entry's rows are just the header (panel_rows: 1 + 2*n_defs) — one pending row is
    # not worth dispatch overhead, so finish() must take the plain serial path and never touch
    # shared_executor.
    import saitenka.panel.compose as PC

    calls = []

    def _tracked_shared_executor(*_a, **_k):
        calls.append(1)

    monkeypatch.setattr(PC, "shared_executor", _tracked_shared_executor)
    e = Entry(headword=["本"], defs=[])
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    lp.finish()
    assert not calls


def test_add_button_draws_only_inside_header_add_rect():
    # R2b: the ⊕ is drawn only with the flag, and exactly where header_add_rect() reports (so the
    # controller's click hit-test lines up with the pixels).
    e = _tall_entry(1)
    off = render_panel(e, width=WIDTH)
    on = render_panel(e, width=WIDTH, add_button=True)
    assert off.size == on.size
    diff = np.abs(np.asarray(off, np.int16) - np.asarray(on, np.int16)).sum(2)
    assert diff.sum() > 0  # the ⊕ added ink

    x, y, w, h = header_add_rect(WIDTH)
    mask = np.zeros(diff.shape, bool)
    mask[y : y + h, x : x + w] = True
    assert diff[~mask].sum() == 0  # …and nothing changed outside the reported rect


def test_scan_boxes_capture_cjk_cells_with_tails():
    # R4: each rendered CJK char in a def body becomes a hit cell carrying its Yomitan-style scan tail.
    e = Entry(headword=["本命"], defs=[Definition("MonoC", ["追いかける。"])])
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    img = lp.finish()
    boxes = lp.scan_boxes
    assert boxes
    assert boxes[0].text.startswith("追いかける")
    assert boxes[1].text.startswith("いかける")
    assert boxes[1].x > boxes[0].x  # cells advance left to right
    for sb in boxes:  # every cell sits inside the panel
        assert sb.x >= 0 and sb.x + sb.w <= img.width
        assert sb.y >= 0 and sb.y + sb.h <= img.height


def test_scan_boxes_absent_for_english_only_body():
    e = Entry(headword=["本命"], defs=[Definition("JMdict", ["favourite; front runner"])])
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    lp.finish()
    assert lp.scan_boxes == []
    assert [b for b in lp.link_boxes if not b.query.startswith("kanji:")] == []


def test_scan_boxes_grow_as_panel_finishes():
    lp = LazyPanel(panel_rows(_tall_entry(6), WIDTH), WIDTH)
    lp.render_to(300)
    partial = len(lp.scan_boxes)
    lp.finish()
    assert len(lp.scan_boxes) > partial  # deferred bodies add their hitboxes on finish


def test_link_boxes_capture_cross_references():
    # R4b: an internal <a> cross-reference in a def body becomes a clickable LinkBox inside the panel.
    body = ["同義語は", {"tag": "a", "href": "?query=見る", "content": "見る"}, "。"]
    e = Entry(headword=["観る"], defs=[Definition("MonoA", body)])
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    img = lp.finish()
    # the body cross-ref link (the header also emits per-kanji `kanji:` links, tested separately)
    lb = next(b for b in lp.link_boxes if not b.query.startswith("kanji:"))
    assert lb.query == "見る"
    assert lb.x >= 0 and lb.x + lb.w <= img.width  # sits inside the panel
    assert lb.y >= 0 and lb.y + lb.h <= img.height
    assert lb.w > 0 and lb.h > 0


def test_header_speaker_rect_lands_on_the_drawn_speaker():
    # the 🔊 hit-test region must actually cover drawn speaker ink (so clicks line up with pixels)
    e = _tall_entry(1)
    img = render_panel(e, width=WIDTH)
    x, y, w, h = header_speaker_rect(WIDTH)
    assert x + w <= img.width and y + h <= img.height
    region = np.asarray(img)[y : y + h, x : x + w]
    assert (region[:, :, 3] > 0).any()  # non-transparent ink is present in the rect


def test_header_button_is_check_when_mined():
    # the add button draws ✓ (mined) vs ⊕ (not mined) in the same header slot
    e = _tall_entry(1)
    plus_img = render_panel(e, width=WIDTH, add_button=True, mined=False)
    check_img = render_panel(e, width=WIDTH, add_button=True, mined=True)
    x, y, w, h = header_add_rect(WIDTH)
    a = np.asarray(plus_img)[y : y + h, x : x + w]
    b = np.asarray(check_img)[y : y + h, x : x + w]
    assert (a != b).any()  # ⊕ and ✓ glyphs differ in the button rect


def test_compose_panel_geometry():
    from PIL import Image

    rows = [(16, Image.new("RGBA", (100, 40))), (36, Image.new("RGBA", (100, 30)))]
    img = compose_panel(rows, WIDTH, Theme())
    m, gap = Theme().margin, Theme().gap
    assert img.height == 2 * m + 40 + 30 + gap
    assert img.width == WIDTH


def _draw_line(canvas_h: int, y: int, font, text: str):
    """Draw ``text`` at integer offset ``y`` on a fresh transparent (premultiplied) RGBA canvas."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (WIDTH, canvas_h), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((8, y), text, font=font, fill=(255, 255, 255, 255))
    return np.asarray(im)


def test_integer_y_band_split_is_pixel_exact():
    """The invariant a future banded/tiled renderer relies on: with **integer** Y band boundaries,
    drawing the SAME text primitive into two adjacent band canvases (each clipping the glyph, one at
    the bottom edge and one at the top) and stacking them is byte-for-byte identical to one tall
    render — even when the cut falls *mid-glyph*.

    This holds because PIL/FreeType grid-fits glyphs to the integer pixel grid, so an integer Y
    translation leaves the raster unchanged and clipping is a pure buffer-window copy. It does NOT
    hold for fractional offsets (`getmask2` warns the raster shifts at fractional coordinates) — hence
    band edges must always be integers. AA fringe + CJK + Latin ligatures are all exercised; a Pillow
    change that broke this would silently seam every tiled tooltip, so this guards it."""
    from saitenka import fonts

    font = fonts.load(fonts.FontSpec("NotoSansJP.ttf", 28))
    text = "定義文の例 fi fl — anti-aliased 明鏡"
    full = _draw_line(120, 40, font, text)  # one tall render; glyphs span ~y20..y66

    # cut at integer y=60 (mid-glyph): band0 = rows[30:60), band1 = rows[60:90)
    band0 = _draw_line(30, 40 - 30, font, text)  # y=10, glyph bottoms clipped off the canvas
    band1 = _draw_line(30, 40 - 60, font, text)  # y=-20, glyph tops clipped off the canvas
    stacked = np.vstack([band0, band1])

    diff = np.abs(full[30:90].astype(np.int16) - stacked.astype(np.int16))
    assert (
        diff.max() == 0
    )  # the two clipped halves reconstitute the original exactly, seam included


def test_stacked_groups_emit_per_entry_mine_boxes_and_reading_sections():
    """A grouped Entry (Yomitan-style stacked entries) renders one block per reading, each with its
    own ⊕ as a 'mine:<card_index>' LinkBox and its reading as a nav section — the wiring a per-entry
    mine click rides. group_mined toggles a block's ⊕→✓ (both rendered, no crash)."""
    from saitenka.app.lookup import furigana
    from saitenka.panel import EntryGroup

    groups = [
        EntryGroup(furigana("退く", "のく"), "のく", [Definition("D", ["to step aside"])], 0),
        EntryGroup(furigana("退く", "しりぞく"), "しりぞく", [Definition("D", ["to retreat"])], 1),
    ]
    entry = Entry(headword=furigana("退く", "のく"), reading="のく", groups=groups)
    rows = panel_rows(entry, WIDTH, add_button=True, group_mined=(True, False))
    lazy = LazyPanel(rows, WIDTH)
    lazy.finish()
    mine = [lb for lb in lazy.link_boxes if lb.query.startswith("mine:")]  # skip header kanji links
    assert [lb.query for lb in mine] == ["mine:0", "mine:1"]
    # each ⊕ sits at the right edge, in reading (top-to-bottom) order
    assert [lb.query for lb in sorted(mine, key=lambda b: b.y)] == ["mine:0", "mine:1"]
    assert [sec for sec, _ in lazy.section_offsets()] == ["のく", "しりぞく"]


def test_no_groups_keeps_single_header_add_button_geometry():
    """Without groups the fused header ⊕ path is unchanged — no per-entry LinkBoxes leak in."""
    entry = Entry(headword=furigana_headword(), defs=[Definition("D", ["x"])])
    rows = panel_rows(entry, WIDTH, add_button=True)
    lazy = LazyPanel(rows, WIDTH)
    lazy.finish()
    assert [lb.query for lb in lazy.link_boxes if lb.query.startswith("mine:")] == []


def furigana_headword():
    from saitenka.app.lookup import furigana

    return furigana("読む", "よむ")
