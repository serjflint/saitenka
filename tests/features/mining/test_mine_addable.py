"""Doctor must flag configs that leave Anki's first field empty; #242 is pinned in the matrix."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from saitenka_card import CardData, build_note

from saitenka.app import doctor as doc
from saitenka.app.mining_config import mine_config_from

_CARD = CardData(expression="読む", reading="よむ", glossary_html="<ol><li>to read</li></ol>")


def _addable(note: dict, field_order: list[str]) -> bool:
    """Anki rejects a note with an empty first field."""
    return bool(note["fields"].get(field_order[0], "").strip())


def _doctor_warns(monkeypatch, mine_table: dict, field_order: list[str]) -> bool:
    monkeypatch.setattr(doc, "load_config", lambda: {"mine": mine_table})
    monkeypatch.setattr(
        doc, "_anki_call", lambda action, **_k: field_order if action == "modelFieldNames" else []
    )
    return doc.check_mine_mapping().status == "warn"


_FIELD = st.text("ABCDEFGabcdefg", min_size=1, max_size=6)


@given(order=st.lists(_FIELD, unique=True, min_size=1, max_size=5))
def test_expression_mapped_to_first_field_is_always_addable(order):
    """Mapping expression to the first field makes the note addable."""
    cfg = mine_config_from({"model": "M", "fields": {"expression": order[0]}})
    note = build_note(cfg, _CARD)
    assert _addable(note, order)


_CASES = [
    (
        {"model": "M", "fields": {"expression": "Front", "reading": "Reading"}},
        ["Front", "Reading"],
        True,
        False,
    ),
    (
        {"model": "M", "fields": {"expression": "Graph", "reading": "Reading"}},
        ["Front", "Reading", "Graph"],
        False,
        True,
    ),
    ({"model": "M", "fields": {"Expression": "Graph"}}, ["Graph"], False, True),
    (
        {"model": "M", "fields": {"expression": "Front", "bogus": "Extra"}},
        ["Front", "Extra"],
        True,
        None,  # Addable need not imply no warning.
    ),
    (
        {"model": "Basic Yomi", "fields": {"Expression": "Graph", "Reading": "Reading"}},
        ["Front", "Reading", "Graph"],
        False,
        True,
    ),  # #242 verbatim
]


@pytest.mark.parametrize(("mine_table", "order", "addable", "warning"), _CASES)
def test_every_unaddable_config_is_one_doctor_warns_about(
    monkeypatch, mine_table, order, addable, warning
):
    cfg = mine_config_from(mine_table)
    assert _addable(build_note(cfg, _CARD), order) is addable
    if warning is not None:
        assert _doctor_warns(monkeypatch, mine_table, order) is warning
