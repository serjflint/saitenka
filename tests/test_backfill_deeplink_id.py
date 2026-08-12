"""The deck-scoped deep-link ID backfill tool (`tools/backfill_deeplink_id.py`, #255): its pure
decision core — which empty-ID notes get which resolved seq — tested against constructed AnkiConnect
notes and a fake resolver, with no real Anki. Loaded by path (the tool lives outside the package)."""

import importlib.util
import sys
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "backfill_deeplink_id.py"


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


def test_note_whose_model_lacks_the_word_field_is_counted_missing_not_unresolved():
    tool = _tool()
    notes = [_note(1, ID="")]  # target field present but no Expression field on this model
    plan = _plan(notes, tool)
    assert plan.writes == {}
    assert (
        plan.missing_field == 1 and plan.unresolved == 0
    )  # can't read a word → not a resolution miss


def test_note_whose_model_lacks_the_target_field_is_never_planned_for_a_write():
    """P2: a note whose model has no `ID` field at all must be skipped-and-counted, never planned —
    AnkiConnect would reject a write to a nonexistent field. Distinct from 'present but empty'."""
    tool = _tool()
    notes = [_note(1, Expression="読む", ExpressionReading="よむ")]  # no ID field on this model
    plan = _plan(notes, tool)
    assert plan.writes == {}
    assert plan.missing_field == 1 and plan.unresolved == 0 and plan.skipped_filled == 0


def test_present_but_empty_word_is_unresolved_not_missing_field():
    """A genuinely empty (but present) word field is a resolution miss, kept distinct from a model that
    lacks the field — the two tallies must not collapse."""
    tool = _tool()
    notes = [_note(1, Expression="", ExpressionReading="", ID="")]
    plan = _plan(notes, tool)
    assert plan.writes == {} and plan.unresolved == 1 and plan.missing_field == 0


# --- query building (P1 #1: deck/model name escaping) -------------------------------------------


def test_build_query_escapes_a_deck_name_containing_a_quote():
    """A deck name with a `"` must not break out of the quoted `deck:"..."` term (which would re-scope
    onto unintended notes → writes outside the deck under --apply). Reuses anki._q, like dedupe()."""
    tool = _tool()
    q = tool.build_query('Bad"Deck::Mining')
    assert q == 'deck:"BadDeck::Mining"'  # the injected quote is stripped, term stays closed
    assert q.count('"') == 2  # exactly the wrapping pair, no stray quote


def test_build_query_escapes_model_and_appends_raw_query():
    tool = _tool()
    q = tool.build_query('My"Deck', model='No"tetype', query="is:due")
    assert 'deck:"MyDeck"' in q and 'note:"Notetype"' in q  # both names sanitized
    assert q.endswith("is:due")  # raw advanced query appended, untouched


# --- apply-result inspection (P1 #2: per-note multi failures counted) ---------------------------


class _FakeAnki:
    """A stand-in for saitenka.app.anki.Anki: its `multi` returns a per-action {result,error} list, the
    exact shape AnkiConnect emits — some notes succeed, some carry an error that does NOT raise."""

    def __init__(self, per_note):
        self.per_note = per_note  # note_id -> None (ok) or an error string
        self.sent = []

    def _call(self, action, *, actions):
        assert action == "multi"
        out = []
        for a in actions:
            nid = a["params"]["note"]["id"]
            self.sent.append(nid)
            out.append({"result": None, "error": self.per_note.get(nid)})
        return out


def test_apply_writes_counts_per_note_failures_separately():
    tool = _tool()
    anki = _FakeAnki({1: None, 2: "cannot update note because it was deleted", 3: None})
    filled, failures = tool._apply_writes(anki, "ID", {1: "111", 2: "222", 3: "333"})
    assert filled == 2
    assert [nid for nid, _ in failures] == [2]  # only the errored note is a failure
    assert "deleted" in failures[0][1]
    assert anki.sent == [1, 2, 3]  # all attempted


def test_apply_writes_treats_a_short_result_list_as_failures():
    """If `multi` returns fewer results than actions (a truncated/odd response), the unmatched notes
    must count as failures, not vanish into a falsely-clean 'filled N'."""
    tool = _tool()

    class _ShortAnki:
        def _call(self, _action, *, actions):
            assert len(actions) == 2  # both writes attempted
            return [{"result": None, "error": None}]  # but only one result returned

    filled, failures = tool._apply_writes(_ShortAnki(), "ID", {1: "111", 2: "222"})
    assert filled == 1 and len(failures) == 1 and failures[0][0] == 2
