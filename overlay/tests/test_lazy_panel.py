"""N1: viewport-first panel rendering — render the visible top, defer the rest."""

import numpy as np

from overlay.panel import (
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


def test_panel_rows_count_matches_entry():
    e = _tall_entry(3)
    rows = panel_rows(e, WIDTH)
    # 1 header + (name chip + body) per def
    assert len(rows) == 1 + 2 * 3


def test_render_to_defers_below_the_fold_rows():
    lp = LazyPanel(panel_rows(_tall_entry(6), WIDTH), WIDTH)
    head = lp.render_to(300)
    assert not lp.complete  # tall entry: the fold is not the end
    assert head.height >= 300  # the viewport is fully covered by real content
    n_head = len(lp._rendered)

    full = lp.finish()
    assert lp.complete
    assert len(lp._rendered) > n_head  # finishing rendered the deferred bodies
    assert full.height > head.height  # …and the panel got taller


def test_first_def_body_streams_block_by_block():
    # N6 → Stage 6: a long multi-block first definition must not be fully rasterised on first
    # paint — the head is a bounded strip (mid-def block budget), the rest deferred to finish().
    # (N6's one-row-per-block was replaced by one deferred row per def with capped raster.)
    para = "とても長い定義の本文であり視界を超えて縦に伸びていく説明文です。" * 2
    e = Entry(
        headword=["本命"],
        defs=[Definition("MonoA", [{"tag": "div", "content": [para]} for _ in range(10)])],
    )
    rows = panel_rows(e, WIDTH)
    assert len(rows) == 3  # header + def-name + ONE deferred body row
    lp = LazyPanel(rows, WIDTH)
    head = lp.render_to(300)
    assert not lp.complete  # the tall first body is NOT fully rasterised…
    assert head.height <= 300 + 120  # …only the strip that fills the viewport
    assert head.height >= 300  # but the viewport is fully covered
    full = lp.finish()
    assert full.height > head.height  # the deferred remainder streams in on finish


def test_split_first_def_body_is_pixel_identical_to_render_panel():
    # multi-block first def (the N6 split path) still composes byte-for-byte like the one-shot panel
    e = Entry(
        headword=["観る", {"tag": "rt", "content": "みる"}],
        defs=[
            Definition(
                "MonoA", [{"tag": "div", "content": [f"意味{i}：説明文。"]} for i in range(6)]
            ),
            Definition("JMdict", ["to watch; to view"]),
        ],
    )
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    lp.render_to(120)
    streamed = lp.finish()
    oneshot = render_panel(e, width=WIDTH)
    assert streamed.size == oneshot.size
    diff = np.abs(np.asarray(streamed, np.int16) - np.asarray(oneshot, np.int16))
    assert diff.max() == 0


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


def test_panel_rows_defers_walk_to_render(monkeypatch):
    # Stage 6: building rows must not walk ANY def content (the SC-walk of a huge 取る-class def
    # costs 200+ ms — measured), and the head must walk only the defs the viewport shows.
    import overlay.panel as P

    calls = [0]
    orig = P.walk

    def counting(node, base=None):
        calls[0] += 1
        return orig(node, base)

    monkeypatch.setattr(P, "walk", counting)
    rows = P.panel_rows(_tall_entry(6), WIDTH)
    assert calls[0] == 0, "panel_rows walked def content at build time"
    LazyPanel(rows, WIDTH).render_to(300)
    assert calls[0] < 6, "the head walked below-the-fold defs"


def test_huge_single_block_first_body_first_paints_bounded():
    # Stage 6: a 200-sense single-block first def must NOT rasterise past the viewport on first
    # paint — the head is a bounded strip, the rest is deferred to finish().
    lp = LazyPanel(panel_rows(_huge_single_block_entry(), WIDTH), WIDTH)
    head = lp.render_to(300)
    assert head.height >= 300  # the viewport is fully covered…
    assert head.height <= 300 + 120, (  # …but bounded (≤ one extra line + margins),
        f"head is {head.height}px — the whole block was rasterised"  # not the ~8000px full block
    )
    assert not lp.complete  # the tail is deferred


def test_huge_single_block_finish_is_pixel_identical_to_render_panel():
    # After a mid-block partial head, finish() must still compose byte-for-byte the same full panel.
    e = _huge_single_block_entry(40)
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    lp.render_to(300)  # partial (mid-block) head first…
    full = lp.finish()  # …then the complete panel
    oneshot = render_panel(e, width=WIDTH)
    assert full.size == oneshot.size
    diff = np.abs(np.asarray(full, np.int16) - np.asarray(oneshot, np.int16))
    assert diff.max() == 0


def test_partial_head_scan_boxes_stay_inside_the_strip():
    # The partial strip's scan boxes must be valid for the head image (no below-the-fold boxes).
    lp = LazyPanel(panel_rows(_huge_single_block_entry(), WIDTH), WIDTH)
    head = lp.render_to(300)
    for sb in lp.scan_boxes:
        assert sb.y >= 0 and sb.y + sb.h <= head.height


def test_thunks_run_at_most_once_each():
    # Rows render through TWO seams since Stage 6: the full thunk (``render``) and the bounded
    # strip (``render_capped`` — full when it doesn't clip). Contract: every row is FULLY rendered
    # exactly once across head + finish; at most one bounded partial strip is rendered extra (the
    # boundary row of the head, re-rendered fully by finish).
    full, partial = [0], [0]

    def _count_render(_orig):
        def thunk():
            full[0] += 1
            return _orig()

        return thunk

    def _count_capped(_orig):
        def capped(max_h):
            img, scan, links, complete = _orig(max_h)
            (full if complete else partial)[0] += 1
            return img, scan, links, complete

        return capped

    rows = panel_rows(_tall_entry(6), WIDTH)
    for r in rows:
        r.render = _count_render(r.render)
        if r.render_capped is not None:
            r.render_capped = _count_capped(r.render_capped)
    lp = LazyPanel(rows, WIDTH)
    lp.render_to(300)
    head_full = full[0]
    assert head_full < len(rows)  # a cold hover does NOT render every row
    lp.finish()
    assert full[0] == len(rows)  # each row fully rendered exactly once overall
    assert partial[0] <= 1  # ≤ one bounded strip (the head's boundary row)


def test_finish_is_pixel_identical_to_render_panel():
    e = _tall_entry(5)
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    lp.render_to(200)  # partial first…
    streamed = lp.finish()  # …then completed
    oneshot = render_panel(e, width=WIDTH)
    assert streamed.size == oneshot.size
    diff = np.abs(np.asarray(streamed, np.int16) - np.asarray(oneshot, np.int16))
    assert diff.max() == 0  # streaming in two passes composes byte-for-byte the same panel


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
    assert lp.scan_boxes == []  # no CJK to scan → nothing to hover


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
    assert lp.link_boxes
    lb = lp.link_boxes[0]
    assert lb.query == "見る"
    assert lb.x >= 0 and lb.x + lb.w <= img.width  # sits inside the panel
    assert lb.y >= 0 and lb.y + lb.h <= img.height
    assert lb.w > 0 and lb.h > 0


def test_link_boxes_absent_without_links():
    e = Entry(headword=["本命"], defs=[Definition("JMdict", ["favourite; front runner"])])
    lp = LazyPanel(panel_rows(e, WIDTH), WIDTH)
    lp.finish()
    assert lp.link_boxes == []


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
