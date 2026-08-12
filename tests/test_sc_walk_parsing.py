"""render/sc_adapter.py parsing helpers, exercised through link_query + inline_flow.

The CSS colour/size parsing branches (hex 3/6-digit, rgb(), named, em/%/px + their fallbacks) and the
`<a>`-target resolution were the uncovered part of the adapter. Driven via the public walker rather
than the private `_parse_*` helpers.
"""

from __future__ import annotations

import pytest

from saitenka.model import Style
from saitenka.render.sc_adapter import inline_flow, link_query

BASE = Style(size=20)


def _span(style: dict):
    """The single inline Span produced by walking a styled <span>あ</span>."""
    return inline_flow({"tag": "span", "style": style, "content": "あ"}, BASE)[0]


# --- link_query: which <a> targets open a related-note tooltip ------------------------------------


@pytest.mark.parametrize(
    ("href", "text", "expected"),
    [
        (None, "  用語  ", "用語"),  # bare <a> → its own (stripped) text is the term
        (None, "   ", None),  # blank text → nothing to open
        ("?query=%E7%8C%AB&x=1", "", "猫"),  # Yomitan cross-ref, percent-decoded
        ("bword://見出し", "見出し", "見出し"),  # relative/custom scheme → visible text
        ("https://example.com", "src", None),  # external source link → inert
        ("mailto:a@b.c", "mail", None),  # external → inert
    ],
)
def test_link_query(href, text, expected):
    assert link_query(href, text) == expected


def test_link_query_non_str_href_is_none():
    assert link_query(123, "x") is None  # type: ignore[arg-type]  # malformed SC: non-str href guarded


# --- colour parsing (via inline_flow) -------------------------------------------------------------


def test_color_hex6():
    assert _span({"color": "#abcdef"}).style.color == (171, 205, 239, 255)


def test_color_hex3_expands():
    assert _span({"color": "#0af"}).style.color == (0, 170, 255, 255)


def test_color_named():
    assert _span({"color": "red"}).style.color == (200, 40, 40, 255)


def test_color_rgb_function():
    assert _span({"color": "rgb(10, 20, 30)"}).style.color == (10, 20, 30, 255)


def test_color_bad_hex_inherits_base():
    assert _span({"color": "#zzzzzz"}).style.color == _span({}).style.color  # unparseable → inherit


def test_color_unknown_name_inherits_base():
    assert (
        _span({"color": "chartreuse"}).style.color == _span({}).style.color
    )  # not named/#/rgb → inherit


# --- size parsing (via inline_flow) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("font_size", "expected"),
    [
        ("2em", 40),  # relative to base(20)
        ("150%", 30),  # 1.5 × base
        ("18px", 18),  # absolute
        ("14", 14),  # bare number
    ],
)
def test_size_units(font_size, expected):
    assert _span({"fontSize": font_size}).style.size == expected


def test_size_bad_value_keeps_base():
    assert _span({"fontSize": "huge"}).style.size == BASE.size  # unparseable → base size


# --- inline style flags ---------------------------------------------------------------------------


def test_italic_and_underline_and_weight_flags():
    assert _span({"fontStyle": "italic"}).style.italic is True
    assert _span({"textDecorationLine": "underline"}).style.underline is True
    assert _span({"fontWeight": 700}).style.weight == 700


def test_line_through_sets_strike():
    assert _span({"textDecorationLine": "line-through"}).style.strike is True
    assert _span({"textDecorationLine": ["underline", "line-through"]}).style.strike is True
    assert _span({}).style.strike is False


# --- sub/sup superscript reading annotations (#285) -----------------------------------------------


def _tagged(tag: str):
    return inline_flow({"tag": tag, "content": "あ"}, BASE)[0]


def test_sup_tag_raises_and_shrinks():
    span = _tagged("sup")
    assert span.style.valign == 1  # raised
    assert span.style.size == round(BASE.size * 0.72)  # small annotation


def test_sub_tag_lowers_and_shrinks():
    span = _tagged("sub")
    assert span.style.valign == -1  # lowered
    assert span.style.size == round(BASE.size * 0.72)


def test_vertical_align_style_without_a_sub_sup_tag():
    # A plain span carrying style.verticalAlign is treated as sub/sup too (新明解 uses this form).
    assert _span({"verticalAlign": "super"}).style.valign == 1
    assert _span({"verticalAlign": "sub"}).style.valign == -1
    assert _span({"verticalAlign": "baseline"}).style.valign == 0
