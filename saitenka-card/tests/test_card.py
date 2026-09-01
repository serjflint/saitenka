"""The package installed alone, with no application around it.

The repo suite covers note construction in depth. What only this can answer is that the shape of a
card is reachable without anything that opens a socket — the whole point of the split.
"""

from __future__ import annotations

import pytest
from saitenka_card import (
    KNOWN_ENTITIES,
    CardContent,
    CardData,
    MineConfig,
    bold_word,
    build_note,
    markers_in,
    strip_field_html,
)

CARD = CardData("読む", "よむ", "<ol><li>to read</li></ol>", idseq="1234")


def test_a_preset_writes_only_the_fields_it_maps() -> None:
    """The silent-empty-note trap runs the other way too: an unmapped logical entity must not appear."""
    note = build_note(MineConfig.from_preset("Lapis"), CARD, CardContent(sentence_html="本を読む"))

    assert note["deckName"] == "Saitenka::Mining"
    assert note["fields"]["Expression"] == "読む"
    assert note["fields"]["Sentence"] == "本を読む"
    assert note["fields"]["IsWordAndSentenceCard"] == "1"


def test_the_french_preset_has_no_reading_field() -> None:
    """A Latin-tokenized mine grounds no kana reading, so the note type carries no field for one."""
    note = build_note(MineConfig.from_preset("French"), CardData("lire", "", ""))

    assert "ExpressionReading" not in note["fields"]


def test_a_card_format_wins_wholesale_over_the_field_map() -> None:
    """Not merged — the two ways of describing a card are exclusive, and a merge would write fields
    the user did not ask for."""
    cfg = MineConfig(card_format={"Word": "{expression}", "Gloss": "{glossary}"})

    note = build_note(cfg, CARD)

    assert set(note["fields"]) == {"Word", "Gloss", *cfg.flags}
    assert note["fields"]["Word"] == "読む"


def test_the_expression_field_is_found_through_a_template() -> None:
    """The dedup key. Under `card_format` it is whichever field's template mentions {expression},
    since that is the field that actually gets written."""
    assert MineConfig(card_format={"Word": "{expression}"}).expression_field() == "Word"
    assert MineConfig().expression_field() == "Expression"
    assert MineConfig(card_format={"Gloss": "{glossary}"}).expression_field() == ""


def test_an_unknown_card_kind_falls_back_rather_than_disabling_mining() -> None:
    """A `[mine].card_kind` typo would otherwise mark no template and produce an unusable card."""
    assert MineConfig(card_kind="nonsense").flags == {"IsWordAndSentenceCard": "1"}


def test_sentence_bolding_escapes_the_context_it_wraps() -> None:
    """Subtitle text is arbitrary; a raw `<` would inject markup into the Anki field."""
    assert bold_word("a <b>x</b> 読む", "読む") == "a &lt;b&gt;x&lt;/b&gt; <b>読む</b>"


@pytest.mark.parametrize("entity", sorted(KNOWN_ENTITIES))
def test_every_known_entity_is_writable_through_the_field_map(entity: str) -> None:
    """`doctor` validates a user's map against this set, so a name in it that writes nothing would
    pass validation and produce an empty field."""
    note = build_note(
        MineConfig(fields={entity: "Target"}),
        CARD,
        CardContent(sentence_html="s", picture="p.jpg", audio="a.mp3", misc="m", freq_html="f"),
    )

    assert "Target" in note["fields"]


def test_marker_names_are_read_out_of_a_template() -> None:
    assert markers_in("{expression} and {reading}") == {"expression", "reading"}


def test_field_html_is_stripped_for_comparison() -> None:
    assert strip_field_html(" <b>読む</b> ") == "読む"
