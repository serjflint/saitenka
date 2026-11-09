"""Selected dictionary entries reach Anki through registered mouse input."""

import numpy as np
import pytest
from dirty_equals import IsPartialDict
from util import assert_golden, await_ready


@pytest.mark.parametrize("phrase", [False, True])
@pytest.mark.parametrize(
    ("kind", "entry", "expression", "reading", "gloss"),
    [
        ("compound", 0, "超満員", "ちょうまんいん", "overcrowded"),
        ("single_compound", 0, "超満員", "ちょうまんいん", "overcrowded"),
        ("reading", 0, "人気", "にんき", "popularity"),
        ("reading", 1, "人気", "ひとけ", "signs of life"),
    ],
)
def test_mining_a_definition_uses_the_selected_nested_entry(
    lookup_session, phrase, kind, entry, expression, reading, gloss
):
    dataset = f"nested_{kind}" + ("_phrase" if phrase else "")
    ui, anki = lookup_session(dataset)
    ui.subtitle("船内環境").hover_character(0).scan_text(expression).add_entry(entry)
    await_ready(lambda: bool(anki.added), "no Anki note arrived", pump=ui.session.pump)

    assert len(anki.added) == 1
    assert anki.added[0]["fields"] == IsPartialDict(
        Expression=expression, ExpressionReading=reading, Glossary=f"<ol><li>{gloss}</li></ol>"
    )


def test_shared_header_mines_the_selected_alternate_reading(lookup_session):
    ui, anki = lookup_session("alternate_reading")
    ui.subtitle("船内").hover_character(0).add_entry(1)
    await_ready(lambda: bool(anki.added), "no Anki note arrived", pump=ui.session.pump)

    assert len(anki.added) == 1
    assert anki.added[0]["fields"] == IsPartialDict(
        Expression="船内",
        ExpressionReading="ふねうち",
        Glossary="<ol><li>alternate reading</li></ol>",
    )


def test_mining_unavailable_has_no_add_button(lookup_session):
    ui, _anki = lookup_session("unavailable", mining=False)
    ui.subtitle("船内").hover_character(0)
    images = ui.read_popup()

    for image in images:
        rgb = np.asarray(image)[:, :, :3]
        assert not ((rgb[:, :, 1] > 150) & (rgb[:, :, 0] < 150) & (rgb[:, :, 2] < 150)).any()
    assert_golden(images[0], "lookup_without_mining.png")
    assert_golden(images[-1], "lookup_without_mining_end.png")
