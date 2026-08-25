"""The friend's #242/#243 class: a mining field map that builds a note Anki *rejects* (its first/sort
field written empty), which doctor validated statically but nothing tied to the runtime note.

Two oracles, both off the real production seam (``mine_config_from`` builds the same MineConfig doctor
and the miner use):

* **Positive core (property):** expression → the note type's first field ⇒ ``build_note`` writes it
  non-empty ⇒ addable.
* **Safety tie (metamorphic):** every config that ``build_note`` renders *unaddable* (empty first field)
  is one ``doctor.check_mine_mapping`` *warns* about — doctor is never laxer than Anki's add
  precondition. The friend's exact capitalized-keys config is a pinned example.

(The reverse doesn't hold: doctor also warns on a non-fatal unknown entity whose field isn't the first —
that writes nothing but the note is still addable. The safety direction is the one that matters.)
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from saitenka.app import doctor as doc
from saitenka.app.anki import build_note
from saitenka.app.lookup import CardData
from saitenka.app.mining_config import mine_config_from

_CARD = CardData(expression="読む", reading="よむ", glossary_html="<ol><li>to read</li></ol>")


def _addable(note: dict, field_order: list[str]) -> bool:
    """Anki's precondition: the note type's FIRST (sort) field must be non-empty, else the add is
    rejected. ``build_note`` keys by real field name, so read the first field's written value."""
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
    """Positive core: whatever the note type's field order, mapping expression to its first field yields
    a non-empty first field — the config that CAN'T trip the empty-note rejection."""
    cfg = mine_config_from({"model": "M", "fields": {"expression": order[0]}})
    note = build_note(cfg, _CARD)
    assert _addable(note, order)


# (mine-table fields, note-type field order, addable?) — the safety tie asserts warn ⊇ unaddable.
_CASES = [
    ({"expression": "Front", "reading": "Reading"}, ["Front", "Reading"], True),  # clean
    (
        {"expression": "Graph", "reading": "Reading"},
        ["Front", "Reading", "Graph"],
        False,
    ),  # 1st unmapped
    ({"Expression": "Graph"}, ["Graph"], False),  # the friend's capitalized keys → all fields empty
    (
        {"expression": "Front", "bogus": "Extra"},
        ["Front", "Extra"],
        True,
    ),  # warns, but still addable
]


def test_every_unaddable_config_is_one_doctor_warns_about(monkeypatch):
    """The safety net: doctor must flag every config Anki would reject, so the friend never hits a silent
    empty-note add. Reverse isn't required (doctor may warn on a non-fatal unknown entity), so only the
    unaddable ⇒ warn direction is asserted, plus a clean config staying ok+addable."""
    for fields, order, addable in _CASES:
        cfg = mine_config_from({"model": "M", "fields": fields})
        assert _addable(build_note(cfg, _CARD), order) is addable, (fields, order)
        if not addable:
            assert _doctor_warns(monkeypatch, {"model": "M", "fields": fields}, order), fields


def test_clean_config_is_addable_and_doctor_does_not_warn(monkeypatch):
    fields, order = {"expression": "Front", "reading": "Reading"}, ["Front", "Reading"]
    assert _addable(build_note(mine_config_from({"model": "M", "fields": fields}), _CARD), order)
    assert not _doctor_warns(monkeypatch, {"model": "M", "fields": fields}, order)


def test_friends_capitalized_keys_config_is_the_pinned_regression(monkeypatch):
    """#242 verbatim: capitalized logical keys match no entity, every field writes empty, Anki rejects
    the note. Pinned so this exact shape can never regress to a silent pass."""
    fields, order = {"Expression": "Graph", "Reading": "Reading"}, ["Front", "Reading", "Graph"]
    note = build_note(mine_config_from({"model": "Basic Yomi", "fields": fields}), _CARD)
    assert not _addable(note, order)
    assert _doctor_warns(monkeypatch, {"model": "Basic Yomi", "fields": fields}, order)
