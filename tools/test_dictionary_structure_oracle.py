from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from saitenka.app.dictdb import DictionaryDb

sys.path.insert(0, str(Path(__file__).parent))
from dictionary_structure_oracle import (
    TraceBlock,
    compare_blocks,
    compare_dictionary_structure,
)


def test_structure_oracle_accepts_matching_semantic_blocks():
    yomitan = (
        TraceBlock("①", 1, "bird"),
        TraceBlock(None, 2, "A bird sang."),
    )
    saitenka = (
        TraceBlock("①", 1, "bird"),
        TraceBlock(None, 2, "A bird sang."),
    )

    assert compare_blocks(yomitan, saitenka) is None


def test_structure_oracle_rejects_wrong_indent():
    yomitan = (TraceBlock(None, 2, "A bird sang."),)
    saitenka = (TraceBlock(None, 1, "A bird sang."),)

    difference = compare_blocks(yomitan, saitenka)

    assert difference is not None
    assert difference.index == 0


def test_structure_oracle_reports_the_first_glued_block():
    yomitan = (
        TraceBlock("①", 6, "bird"),
        TraceBlock(None, 8, "A bird sang."),
    )
    saitenka = (TraceBlock("①", 1, "birdA bird sang."),)

    difference = compare_blocks(yomitan, saitenka)

    assert difference is not None
    assert difference.index == 0
    assert difference.yomitan == yomitan[0]
    assert difference.saitenka == saitenka[0]


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_compares_nested_block_children_without_duplicating_the_parent(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    database = _database(
        tmp_path,
        [["鳥", "とり", "", "", 1, [_structured_details("one", "two")], 1, ""]],
    )

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_compares_table_rows_and_cells(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    table = {
        "type": "structured-content",
        "content": {
            "tag": "table",
            "content": [
                {
                    "tag": "tr",
                    "content": [{"tag": "td", "content": "a"}, {"tag": "td", "content": "b"}],
                },
                {
                    "tag": "tr",
                    "content": [{"tag": "td", "content": "c"}, {"tag": "td", "content": "d"}],
                },
            ],
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [table], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_compares_every_matching_definition(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    database = _database(
        tmp_path,
        [
            ["鳥", "とり", "", "", 2, ["first"], 1, ""],
            ["鳥", "とり", "", "", 1, ["second"], 2, ""],
        ],
    )

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert not report.passed
    assert [block.text for block in report.yomitan] == ["first", "second"]
    assert [block.text for block in report.saitenka] == ["firstsecond"]


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_exposes_glued_homograph_definitions(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    database = _database(
        tmp_path,
        [
            ["生", "なま", "", "", 2, ["raw"], 1, ""],
            ["生", "せい", "", "", 1, ["life"], 2, ""],
        ],
    )

    report = compare_dictionary_structure(database.path, "Jitendex", "生", "なま", Path(checkout))

    assert not report.passed
    assert [block.text for block in report.yomitan] == ["raw", "life"]
    assert [block.text for block in report.saitenka] == ["rawlife"]


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_compares_nested_images(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    image = {
        "type": "structured-content",
        "content": {"tag": "div", "content": ["before", {"tag": "img", "path": "x.png"}, "after"]},
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [image], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_transfers_list_marker_to_first_block_child(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {
            "tag": "ul",
            "content": {"tag": "li", "content": {"tag": "div", "content": "bird"}},
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_transfers_list_marker_to_first_table(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    table = {
        "tag": "table",
        "content": [
            {"tag": "tr", "content": {"tag": "td", "content": "a"}},
            {"tag": "tr", "content": {"tag": "td", "content": "b"}},
        ],
    }
    content = {
        "type": "structured-content",
        "content": {"tag": "ul", "content": {"tag": "li", "content": table}},
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_preserves_suppressed_marker_on_block_child(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {
            "tag": "ul",
            "data": {"content": "glossary"},
            "content": {"tag": "li", "content": {"tag": "div", "content": "bird"}},
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_uses_reading_lookup_fallback(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, ["bird"], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "とり", "", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    "glossary",
    [
        [{"type": "structured-content", "content": {"tag": "span", "content": "inline"}}],
        [{"type": "image", "path": "x.png"}],
    ],
)
def test_structure_oracle_compares_inline_only_definitions(tmp_path, glossary):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, glossary, 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_compares_forced_line_breaks(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {"tag": "div", "content": ["before", {"tag": "br"}, "after"]},
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
@pytest.mark.parametrize("container", ["li", "td"])
def test_structure_oracle_compares_forced_line_breaks_inside_structural_blocks(tmp_path, container):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    inner = {"tag": container, "content": ["before", {"tag": "br"}, "after"]}
    root = (
        {"tag": "ul", "content": inner}
        if container == "li"
        else {"tag": "table", "content": {"tag": "tr", "content": inner}}
    )
    content = {"type": "structured-content", "content": root}
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_compares_single_quoted_literal_marker(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {
            "tag": "ul",
            "style": {"listStyleType": "'＊'"},
            "content": {"tag": "li", "content": "bird"},
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
@pytest.mark.parametrize(("tag", "list_style"), [("ol", "disc"), ("ul", "decimal")])
def test_structure_oracle_uses_explicit_standard_marker_style(tmp_path, tag, list_style):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {
            "tag": tag,
            "style": {"listStyleType": list_style},
            "content": {"tag": "li", "content": "bird"},
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert not report.passed
    assert report.yomitan[0].marker != report.saitenka[0].marker


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_indents_markerless_nested_continuation(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    nested = {
        "tag": "ul",
        "style": {"listStyleType": "none"},
        "content": {"tag": "li", "content": "child"},
    }
    content = {
        "type": "structured-content",
        "content": {"tag": "ul", "content": {"tag": "li", "content": ["parent", nested]}},
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_preserves_explicit_marker_in_semantic_glossary(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {
            "tag": "ul",
            "data": {"content": "glossary"},
            "content": {
                "tag": "li",
                "style": {"listStyleType": "'＊'"},
                "content": "bird",
            },
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_does_not_move_marker_after_leading_break(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {
            "tag": "ul",
            "content": {"tag": "li", "content": [{"tag": "br"}, "bird"]},
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_preserves_parent_and_nested_list_markers(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    nested = {"tag": "ol", "content": {"tag": "li", "content": "child"}}
    content = {
        "type": "structured-content",
        "content": {"tag": "ul", "content": {"tag": "li", "content": nested}},
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()
    assert [(block.marker, block.depth, block.text) for block in report.saitenka] == [
        ("・", 0, ""),
        ("1.", 1, "child"),
    ]


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_preserves_nested_marker_with_block_first_child(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    nested = {
        "tag": "ol",
        "content": {"tag": "li", "content": {"tag": "div", "content": "child"}},
    }
    content = {
        "type": "structured-content",
        "content": {"tag": "ul", "content": {"tag": "li", "content": nested}},
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()
    assert [(block.marker, block.depth, block.text) for block in report.saitenka] == [
        ("・", 0, ""),
        ("1.", 1, "child"),
    ]


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_uses_first_semantic_item_for_marker_transfer(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    nested = {
        "tag": "ul",
        "data": {"content": "glossary"},
        "content": [
            {"tag": "li", "content": "default"},
            {"tag": "li", "style": {"listStyleType": "'②'"}, "content": "explicit"},
        ],
    }
    content = {
        "type": "structured-content",
        "content": {"tag": "ul", "content": {"tag": "li", "content": nested}},
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_rejects_unsupported_list_marker_projection(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    content = {
        "type": "structured-content",
        "content": {
            "tag": "ol",
            "style": {"listStyleType": "lower-alpha"},
            "content": {"tag": "li", "content": "bird"},
        },
    }
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, [content], 1, ""]])

    with pytest.raises(subprocess.CalledProcessError) as error:
        compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert "unsupported list-style-type: lower-alpha" in error.value.stderr


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_structure_oracle_agrees_with_yomitan_on_jitendex_fixture(tmp_path):
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the structured-content differential")
    fixture = Path(__file__).parent.parent / "tests" / "fixtures" / "sc_jitendex_nested.json"
    glossary = [json.loads(fixture.read_text(encoding="utf-8"))]
    database = _database(tmp_path, [["鳥", "とり", "", "", 1, glossary, 1, ""]])

    report = compare_dictionary_structure(database.path, "Jitendex", "鳥", "とり", Path(checkout))

    assert report.passed, report.as_markdown()


def _database(tmp_path, rows):
    archive = tmp_path / "jitendex.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("index.json", json.dumps({"title": "Jitendex", "format": 3}))
        output.writestr("term_bank_1.json", json.dumps(rows, ensure_ascii=False))
    database = DictionaryDb.open(tmp_path / "dictionary.sqlite")
    database.import_zip(archive, imported_at="2026-08-12T00:00:00+00:00")
    return database


def _structured_details(*texts):
    return {
        "type": "structured-content",
        "content": {
            "tag": "details",
            "content": [{"tag": "summary", "content": text} for text in texts],
        },
    }
