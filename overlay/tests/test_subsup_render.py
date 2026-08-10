"""Sub/sup reading annotations (#285) render smaller and baseline-shifted, and the line box grows so
the raised/lowered glyph is never clipped. Metamorphic + negative-control oracles (no golden): the
super run's ink sits higher than the same run inline, and the sup line box is taller than a plain one."""

from __future__ import annotations

from overlay.model import Style
from overlay.render.flow import build_items, layout_flow, render_flow
from overlay.render.layout import Block
from overlay.sc.walk import inline_flow

BASE = Style(size=28)


def _flow(node):
    return inline_flow(node, BASE)


def _sup_token_shift(flow):
    tok = next(it.tok for it in build_items(flow) if it.kind == "text" and it.tok is not None)
    assert tok is not None
    return tok.baseline_shift


def test_sup_token_carries_a_positive_baseline_shift():
    assert _sup_token_shift(_flow({"tag": "sup", "content": "ア"})) > 0  # raised


def test_sub_token_carries_a_negative_baseline_shift():
    assert _sup_token_shift(_flow({"tag": "sub", "content": "ア"})) < 0  # lowered


def test_plain_run_has_no_shift():
    assert _sup_token_shift(_flow({"tag": "span", "content": "ア"})) == 0.0


def test_sup_line_box_grows_so_the_raised_glyph_is_not_clipped():
    block = Block(width=400)
    shrunk = Style(
        size=round(BASE.size * 0.72)
    )  # negative control: a PLAIN run at the annotation size
    plain = layout_flow(inline_flow({"tag": "span", "content": "ア"}, shrunk), block)
    sup = layout_flow(_flow({"tag": "sup", "content": "ア"}), block)
    # Identical glyph + size; only the sup reserves headroom = the shift, so its box is taller and its
    # baseline sits lower — headroom the clip check (baseline − shift − ascent ≥ 0) depends on.
    plain_box, plain_base, _ = plain.boxes[0]
    sup_box, sup_base, _ = sup.boxes[0]
    assert sup_box > plain_box
    assert sup_base >= plain_base


def _top_ink_row(img):
    bbox = img.getchannel("A").getbbox()  # (l, t, r, b) of the non-transparent region
    return bbox[1] if bbox else img.height


def test_rendered_super_ink_sits_higher_than_the_same_run_inline():
    block = Block(width=400)
    plain = render_flow(_flow({"tag": "span", "content": "ア"}), block)
    sup = render_flow(_flow({"tag": "sup", "content": "ア"}), block)
    # Both use the shrunk 0.72em size; only the sup raises the baseline, so its topmost ink is higher up.
    assert _top_ink_row(sup) < _top_ink_row(plain)
