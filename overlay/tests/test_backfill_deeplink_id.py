"""The deck-scoped deep-link ID backfill tool (`tools/backfill_deeplink_id.py`, #255): its pure
decision core — which empty-ID notes get which resolved seq — tested against constructed AnkiConnect
notes and a fake resolver, with no real Anki. Loaded by path (the tool lives outside the package)."""

import importlib.util
import sys
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "backfill_deeplink_id.py"


def _tool():
    spec = importlib.util.spec_from_file_location("backfill_deeplink_id", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _note(nid, **fields):
    """An AnkiConnect `notesInfo`-shaped note: {noteId, fields: {Name: {value, order}}}."""
    return {
        "noteId": nid,
        "fields": {
            name: {"value": val, "order": i} for i, (name, val) in enumerate(fields.items())
        },
    }


# a fake JMdict resolver: known (term, reading) -> ent_seq; everything else is unresolved
_SEQS = {("読む", "よむ"): "1456360", ("音", "おと"): "1576900"}


def _fake_resolve(term, reading):
    return _SEQS.get((term, reading))


def _plan(notes, tool, **overrides):
    kwargs = {
        "field_name": "ID",
        "word_field": "Expression",
        "reading_field": "ExpressionReading",
        "resolve": _fake_resolve,
    }
    kwargs.update(overrides)
    return tool.plan_backfill(notes, **kwargs)


def test_empty_id_with_a_resolvable_word_is_planned():
    tool = _tool()
    notes = [_note(1, Expression="読む", ExpressionReading="よむ", ID="")]
    plan = _plan(notes, tool)
    assert plan.writes == {1: "1456360"}
    assert plan.unresolved == 0 and plan.skipped_filled == 0
    assert plan.examples == [("読む", "1456360")]


def test_already_filled_id_is_never_overwritten():
    tool = _tool()
    notes = [_note(1, Expression="読む", ExpressionReading="よむ", ID="9999999")]
    plan = _plan(notes, tool)
    assert plan.writes == {}  # non-empty field untouched — idempotent re-run writes nothing
    assert plan.skipped_filled == 1


def test_empty_id_with_an_unresolvable_word_is_counted_not_written():
    tool = _tool()
    notes = [_note(1, Expression="架空語", ExpressionReading="かくうご", ID="")]
    plan = _plan(notes, tool)
    assert plan.writes == {}
    assert plan.unresolved == 1


def test_mixed_deck_partitions_into_fill_skip_and_unresolved():
    tool = _tool()
    notes = [
        _note(1, Expression="読む", ExpressionReading="よむ", ID=""),  # fill
        _note(2, Expression="音", ExpressionReading="おと", ID="1"),  # already filled → skip
        _note(3, Expression="架空", ExpressionReading="かくう", ID=""),  # unresolved
        _note(4, Expression="音", ExpressionReading="おと", ID=""),  # fill
    ]
    plan = _plan(notes, tool)
    assert plan.writes == {1: "1456360", 4: "1576900"}
    assert plan.skipped_filled == 1
    assert plan.unresolved == 1


def test_html_and_furigana_are_stripped_from_the_word_field():
    """A note whose Expression carries markup / Anki inline furigana still resolves — the plan reads
    the bare term, not the display HTML (the ID column checks the field's text, not its rendering)."""
    tool = _tool()
    notes = [_note(1, Expression="<b>読[よ]む</b>", ExpressionReading="よむ", ID="")]
    plan = _plan(notes, tool)
    assert plan.writes == {1: "1456360"}


def test_whitespace_only_id_is_treated_as_empty_and_backfilled():
    tool = _tool()
    notes = [_note(1, Expression="読む", ExpressionReading="よむ", ID="  ")]
    plan = _plan(notes, tool)
    assert plan.writes == {1: "1456360"}  # blank field is empty, not "already filled"


def test_custom_field_names_are_honoured():
    tool = _tool()
    notes = [_note(1, Word="読む", Reading="よむ", DeepLink="")]
    plan = _plan(notes, tool, field_name="DeepLink", word_field="Word", reading_field="Reading")
    assert plan.writes == {1: "1456360"}


def test_note_without_a_word_field_is_unresolved_not_a_crash():
    tool = _tool()
    notes = [_note(1, ID="")]  # no Expression field at all
    plan = _plan(notes, tool)
    assert plan.writes == {} and plan.unresolved == 1
