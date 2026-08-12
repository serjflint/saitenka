"""Stage 7: structured-content walker → blocks, with goldens."""

import json
from pathlib import Path

import pytest
from util import assert_golden

from saitenka.model import Style
from saitenka.render.document import DocStyle, layout_document, render_document
from saitenka.render.flow import ChipBox, ImgBox, RubyBox
from saitenka.render.sc_adapter import walk

FIX = Path(__file__).resolve().parent / "fixtures"
BASE = Style(size=26)


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))["content"]


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


def _flow_text(block):
    parts = []
    for item in block.flow:
        if isinstance(item, RubyBox):
            parts.extend(span.text for span in item.base)
        elif hasattr(item, "text"):
            parts.append(item.text)
    return "".join(parts)


def test_jitendex_nested_content_preserves_readable_blocks():
    blocks = walk(_load("sc_jitendex_nested.json"), BASE)

    assert [
        (
            block.kind,
            block.list_type,
            block.ordinal,
            getattr(block, "marker", None),
            block.indent,
            _flow_text(block),
        )
        for block in blocks
    ] == [
        ("list-item", "ul", 1, "＊", 0, "noun"),
        ("list-item", "ol", 1, "①", 1, "bird"),
        ("para", None, None, None, 2, "鳥が鳴いた。"),
        ("para", None, None, None, 2, "A bird sang."),
        ("list-item", "ol", 2, "②", 1, "bird meat"),
        ("list-item", "ul", 2, "", 1, "fowl"),
        ("list-item", "ul", 3, "", 1, "poultry"),
        ("para", None, None, None, 2, "See: 鶏"),
        ("para", None, None, None, 0, "JMdict"),
    ]


def test_jitendex_structural_split_preserves_ruby_and_link_targets():
    blocks = walk(_load("sc_jitendex_nested.json"), BASE)
    rubies = [item for block in blocks for item in block.flow if isinstance(item, RubyBox)]

    assert [("".join(span.text for span in ruby.base), ruby.reading) for ruby in rubies] == [
        ("鳥", "とり"),
        ("鶏", "にわとり"),
    ]
    assert [span.href for span in rubies[1].base] == ["鶏"]


def test_jitendex_markers_reach_document_layout():
    blocks = walk(_load("sc_jitendex_nested.json"), BASE)

    document = layout_document(blocks, 800, BASE)

    assert [block.marker for block in document.blocks] == [
        "＊",
        "①",
        "",
        "",
        "②",
        "",
        "",
        "",
        "",
    ]
    assert len({block.x for block in document.blocks[4:7]}) == 1


def test_list_item_marker_moves_to_its_first_block_child():
    node = {
        "tag": "ol",
        "content": {
            "tag": "li",
            "style": {"listStyleType": '"①"'},
            "content": {"tag": "div", "content": "bird"},
        },
    }

    blocks = walk(node, BASE)

    assert [(block.kind, block.marker, block.indent, _flow_text(block)) for block in blocks] == [
        ("list-item", "①", 0, "bird")
    ]


def test_empty_parent_list_item_does_not_replace_nested_list_marker():
    node = {
        "tag": "ul",
        "content": {
            "tag": "li",
            "content": {
                "tag": "ol",
                "content": [
                    {"tag": "li", "content": "first"},
                    {"tag": "li", "content": "second"},
                ],
            },
        },
    }

    blocks = walk(node, BASE)

    assert [(block.marker, block.ordinal, _flow_text(block)) for block in blocks] == [
        (None, 1, "\N{NO-BREAK SPACE}"),
        (None, 1, "first"),
        (None, 2, "second"),
    ]


def test_parent_marker_does_not_replace_block_wrapped_nested_markers():
    node = {
        "tag": "ul",
        "content": {
            "tag": "li",
            "style": {"listStyleType": '"P"'},
            "content": {
                "tag": "div",
                "content": {
                    "tag": "ol",
                    "content": [
                        {"tag": "li", "content": "first"},
                        {"tag": "li", "content": "second"},
                    ],
                },
            },
        },
    }

    document = layout_document(walk(node, BASE), 800, BASE)

    assert [block.marker for block in document.blocks] == ["P", "1.", "2."]


def test_table_inside_list_keeps_continuation_indentation():
    node = {
        "tag": "ol",
        "content": {
            "tag": "li",
            "content": [
                {"tag": "p", "content": "before"},
                "middle",
                {
                    "tag": "table",
                    "content": {
                        "tag": "tr",
                        "content": {"tag": "td", "content": "cell"},
                    },
                },
                "after",
            ],
        },
    }

    blocks = walk(node, BASE)

    assert [(block.indent, _flow_text(block)) for block in blocks] == [
        (0, "before"),
        (1, "middle"),
        (1, "cell"),
        (1, "after"),
    ]


def test_item_marker_overrides_markerless_nested_list():
    node = {
        "tag": "ul",
        "content": {
            "tag": "li",
            "style": {"listStyleType": '"P"'},
            "content": {
                "tag": "ul",
                "style": {"listStyleType": "none"},
                "content": {
                    "tag": "li",
                    "style": {"listStyleType": '"C"'},
                    "content": "child",
                },
            },
        },
    }

    document = layout_document(walk(node, BASE), 800, BASE)

    assert [block.marker for block in document.blocks] == ["P", "C"]


@pytest.mark.parametrize("field", ["style", "data"])
def test_malformed_list_metadata_does_not_abort_rendering(field):
    node = {"tag": "ul", field: "bad", "content": {"tag": "li", "content": "kept"}}

    blocks = walk(node, BASE)

    assert _flow_text(blocks[0]) == "kept"


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
    from saitenka.render.sc_adapter import link_query

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
    from saitenka.render.sc_adapter import _LINK_EXTERNAL

    node = {"tag": "a", "href": "https://www.edrdg.org/x?q=1", "content": "JMdict"}
    spans = [s for b in walk(node, BASE) for s in b.flow if hasattr(s, "style")]
    assert spans and all(s.href is None for s in spans)  # not clickable
    assert all(not s.style.underline for s in spans)  # no underline affordance
    assert all(s.style.color == _LINK_EXTERNAL for s in spans)  # muted gray


def test_internal_link_stays_blue_and_underlined():
    from saitenka.render.sc_adapter import _NAMED

    node = {"tag": "a", "href": "?query=見る", "content": "見る"}
    spans = [s for b in walk(node, BASE) for s in b.flow if getattr(s, "href", None)]
    assert spans and all(s.style.underline and s.style.color == _NAMED["blue"] for s in spans)


def test_img_becomes_opaque_box():
    node = {"tag": "img", "path": "x.png"}
    blocks = walk(node, BASE)
    assert any(isinstance(x, ImgBox) for b in blocks for x in b.flow)


def test_table_renders_rows_as_lines_with_separated_cells():
    # A table is no longer flattened to a blob: each row is a line (\n) and cells are │-separated, so
    # the grid structure survives. Header cells are bold.
    node = {
        "tag": "table",
        "content": [
            {
                "tag": "tr",
                "content": [{"tag": "th", "content": "語"}, {"tag": "th", "content": "訓"}],
            },
            {
                "tag": "tr",
                "content": [{"tag": "td", "content": "生"}, {"tag": "td", "content": "い"}],
            },
        ],
    }
    text = "".join(s.text for b in walk(node, BASE) for s in b.flow if hasattr(s, "text"))
    assert text == "語 │ 訓\n生 │ い"  # rows split by \n, cells by │, no trailing separator/newline
    header = next(s for b in walk(node, BASE) for s in b.flow if getattr(s, "text", "") == "語")
    assert header.style.weight == 700  # th cells are bold


def test_table_without_the_fix_would_be_a_blob():
    # Negative control: the fix is non-vacuous — the SAME cells run together into one blob if rows and
    # cells aren't separated. Proves the assertion above tests real structure, not incidental text.
    node = {
        "tag": "table",
        "content": [
            {
                "tag": "tr",
                "content": [{"tag": "td", "content": "生"}, {"tag": "td", "content": "い"}],
            }
        ],
    }
    text = "".join(s.text for b in walk(node, BASE) for s in b.flow if hasattr(s, "text"))
    assert "│" in text and text != "生い"


def test_sc_ruby_golden():
    img = render_document(
        walk(_load("sc_ruby.json"), BASE),
        width=240,
        style=DocStyle(background=(255, 255, 255, 255)),
    )
    assert_golden(img, "sc_ruby.png")


def test_sc_list_golden():
    img = render_document(
        walk(_load("sc_list.json"), BASE),
        width=340,
        style=DocStyle(background=(255, 255, 255, 255)),
    )
    assert_golden(img, "sc_list.png")
