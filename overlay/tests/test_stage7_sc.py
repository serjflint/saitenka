"""Stage 7: structured-content walker → blocks, with goldens."""

import json
from pathlib import Path

from overlay.model import Style
from overlay.render.document import render_document
from overlay.render.flow import ChipBox, ImgBox, RubyBox
from overlay.sc.walk import walk
from util import assert_golden

FIX = Path(__file__).resolve().parent / "fixtures"
BASE = Style(size=26)


def _load(name):
    return json.loads((FIX / name).read_text())["content"]


def test_ruby_fixture_produces_ruby_inlines():
    blocks = walk(_load("sc_ruby.json"), BASE)
    assert len(blocks) == 1
    rubies = [x for x in blocks[0].flow if isinstance(x, RubyBox)]
    assert len(rubies) == 5  # 門前 小僧 習 経 読
    assert rubies[0].reading == "もんぜん"


def test_list_fixture_structure():
    blocks = walk(_load("sc_list.json"), BASE)
    items = [b for b in blocks if b.kind == "list-item"]
    assert len(items) == 2
    assert [b.ordinal for b in items] == [1, 2]
    assert all(b.list_type == "ol" for b in items)


def test_unknown_tag_is_flattened_not_dropped():
    blocks = walk(_load("sc_list.json"), BASE)
    text = "".join(s.text for b in blocks for s in b.flow if hasattr(s, "text"))
    assert "未知タグは平坦化" in text  # content of <unknowntag> survived as text


def test_bold_and_link_styles_applied():
    blocks = walk(_load("sc_list.json"), BASE)
    spans = [s for b in blocks for s in b.flow if hasattr(s, "style")]
    assert any(s.style.weight == 700 for s in spans), "bold span not applied"
    assert any(s.style.underline for s in spans), "link underline not applied"


def _chips(blocks):
    return [x for b in blocks for x in b.flow if isinstance(x, ChipBox)]


def test_pos_tag_chip_uses_background_color_not_empty_box():
    # POS tags: backgroundColor + white text + borderRadius, NO borderColor. Dropping the
    # background left white-on-white text in an empty box — regression guard.
    node = {
        "tag": "span",
        "style": {
            "backgroundColor": "#565656",
            "color": "white",
            "borderRadius": "0.3em",
            "fontWeight": "bold",
        },
        "content": "noun",
    }
    chips = _chips(walk(node, BASE))
    assert len(chips) == 1
    cs = chips[0].chip_style
    assert chips[0].text == "noun"
    assert cs.bg == (0x56, 0x56, 0x56, 255)  # filled with the SC background
    assert cs.fg == (255, 255, 255, 255)  # white text stays legible on the fill
    assert cs.border is None  # borderRadius alone must not draw a stray border


def test_whitespace_marker_span_is_not_an_empty_chip():
    # R5: a bordered/filled span whose content is only whitespace (some dicts' accent/marker spacer) must
    # NOT render as a stray empty pill.
    for content in (" ", "　", "\xa0"):
        node = {
            "tag": "span",
            "style": {"borderColor": "#888", "borderWidth": "1px"},
            "content": content,
        }
        assert _chips(walk(node, BASE)) == []


def test_chip_label_is_stripped_of_surrounding_space():
    node = {
        "tag": "span",
        "style": {"backgroundColor": "#565656", "color": "white", "borderRadius": "0.3em"},
        "content": "  noun  ",
    }
    chips = _chips(walk(node, BASE))
    assert len(chips) == 1 and chips[0].text == "noun"  # padding stripped, still a chip


def test_bordered_label_chip_stays_transparent_with_border():
    node = {
        "tag": "span",
        "style": {"borderColor": "#888", "borderWidth": "1px"},
        "content": "逆引き",
    }
    chips = _chips(walk(node, BASE))
    assert len(chips) == 1
    cs = chips[0].chip_style
    assert cs.bg == (0, 0, 0, 0)  # transparent fill
    assert cs.border == (0x88, 0x88, 0x88, 255)


def test_link_query_resolves_internal_targets():
    from overlay.sc.walk import link_query

    assert link_query("?query=見る&wildcards=off", "みる") == "見る"  # Yomitan cross-ref → term
    assert link_query("?query=%E8%A6%8B%E3%82%8B") == "見る"  # URL-encoded
    assert link_query(None, "見る") == "見る"  # bare <a> → its text
    assert link_query("#anchor", "見る") == "見る"  # relative → text
    assert link_query("https://example.com", "site") is None  # external source → not a related note
    assert link_query("mailto:x@y.z", "mail") is None


def test_link_target_stamped_on_spans():
    # R4b: an internal <a> keeps its blue/underline styling AND carries its target term for clicking.
    node = {"tag": "a", "href": "?query=見る", "content": "見る"}
    spans = [s for b in walk(node, BASE) for s in b.flow if hasattr(s, "href")]
    linked = [s for s in spans if s.href]
    assert linked and all(s.href == "見る" for s in linked)
    assert all(s.style.underline for s in linked)  # still styled as a link


def test_external_link_is_muted_and_not_underlined():
    # A dictionary's source-attribution link is external — visually distinct from clickable cross-refs:
    # muted gray, NOT underlined, no click target.
    from overlay.sc.walk import _LINK_EXTERNAL

    node = {"tag": "a", "href": "https://www.edrdg.org/x?q=1", "content": "JMdict"}
    spans = [s for b in walk(node, BASE) for s in b.flow if hasattr(s, "style")]
    assert spans and all(s.href is None for s in spans)  # not clickable
    assert all(not s.style.underline for s in spans)  # no underline affordance
    assert all(s.style.color == _LINK_EXTERNAL for s in spans)  # muted gray


def test_internal_link_stays_blue_and_underlined():
    from overlay.sc.walk import _NAMED

    node = {"tag": "a", "href": "?query=見る", "content": "見る"}
    spans = [s for b in walk(node, BASE) for s in b.flow if getattr(s, "href", None)]
    assert spans and all(s.style.underline and s.style.color == _NAMED["blue"] for s in spans)


def test_img_becomes_opaque_box():
    node = {"tag": "img", "path": "x.png"}
    blocks = walk(node, BASE)
    assert any(isinstance(x, ImgBox) for b in blocks for x in b.flow)


def test_sc_ruby_golden():
    img = render_document(
        walk(_load("sc_ruby.json"), BASE), width=240, background=(255, 255, 255, 255)
    )
    assert_golden(img, "sc_ruby.png")


def test_sc_list_golden():
    img = render_document(
        walk(_load("sc_list.json"), BASE), width=340, background=(255, 255, 255, 255)
    )
    assert_golden(img, "sc_list.png")
