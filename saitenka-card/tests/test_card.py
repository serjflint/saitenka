"""The package installed alone, with no application around it.

The repo suite covers note construction in depth. What only this can answer is that the shape of a
card is reachable without anything that opens a socket — the whole point of the split.
"""

from __future__ import annotations

import pytest
from saitenka_card import (
    FRENCH_FIELDS,
    KNOWN_ENTITIES,
    LAPIS_FIELDS,
    CardContent,
    CardData,
    MineConfig,
    bold_word,
    build_note,
    markers_in,
    strip_field_html,
)

CARD = CardData("読む", "よむ", "<ol><li>to read</li></ol>", idseq="1234")


@pytest.mark.parametrize(
    ("config", "expected_fields"),
    [
        pytest.param(
            MineConfig.from_preset("Lapis"),
            {
                "Expression": "読む",
                "ExpressionReading": "よむ",
                "Sentence": "本を<b>読む</b>",
                "Glossary": "<ol><li>to read</li></ol>",
                "Picture": '<img src="p.jpg">',
                "SentenceAudio": "[sound:a.mp3]",
                "MiscInfo": "ep10 · 10:03",
                "ID": "1234",
                "Frequency": "<ul><li>FreqA: 12</li></ul>",
                "FreqSort": "12",
                "IsWordAndSentenceCard": "1",
            },
            id="lapis",
        ),
        pytest.param(
            MineConfig.from_preset("Kiku"),
            {
                "Expression": "読む",
                "ExpressionReading": "よむ",
                "Sentence": "本を<b>読む</b>",
                "Glossary": "<ol><li>to read</li></ol>",
                "Picture": '<img src="p.jpg">',
                "SentenceAudio": "[sound:a.mp3]",
                "MiscInfo": "ep10 · 10:03",
                "ID": "1234",
                "Frequency": "<ul><li>FreqA: 12</li></ul>",
                "FreqSort": "12",
                "IsWordAndSentenceCard": "1",
            },
            id="kiku",
        ),
        pytest.param(
            MineConfig(model="Animecards", fields={"expression": "Word", "reading": "Reading"}),
            {"Word": "読む", "Reading": "よむ", "IsWordAndSentenceCard": "1"},
            id="custom-map",
        ),
    ],
)
def test_a_preset_writes_only_the_fields_it_maps(config, expected_fields) -> None:
    """The silent-empty-note trap runs the other way too: an unmapped logical entity must not appear."""
    from saitenka_card.note import _entity_values

    content = CardContent(
        sentence_html="本を<b>読む</b>",
        picture="p.jpg",
        audio="a.mp3",
        misc="ep10 · 10:03",
        freq_html="<ul><li>FreqA: 12</li></ul>",
        freq_sort="12",
    )
    note = build_note(
        config,
        CARD,
        content,
        tags=["saitenka::mined", "saitenka::source::Show", "saitenka::ep::03", "saitenka::mined"],
    )

    assert set(_entity_values(CARD, content)) == set(KNOWN_ENTITIES)
    assert note["deckName"] == "Saitenka::Mining"
    assert note["modelName"] == config.model
    assert note["fields"] == expected_fields
    assert note["options"]["allowDuplicate"] is False
    assert note["tags"] == [
        "saitenka",
        "saitenka::mined",
        "saitenka::source::Show",
        "saitenka::ep::03",
    ]


def test_the_french_preset_has_no_reading_field() -> None:
    """A Latin-tokenized mine grounds no kana reading, so the note type carries no field for one."""
    config = MineConfig.from_preset("French")
    note = build_note(config, CardData("lire", "", "<div>to read</div>"))

    assert config.model == "French"
    assert (
        config.fields == FRENCH_FIELDS == {k: v for k, v in LAPIS_FIELDS.items() if k != "reading"}
    )
    assert note["fields"]["Expression"] == "lire"
    assert note["fields"]["Glossary"] == "<div>to read</div>"
    assert "ExpressionReading" not in note["fields"]
    assert note["modelName"] == "French"


def test_a_card_format_wins_wholesale_over_the_field_map() -> None:
    """Not merged — the two ways of describing a card are exclusive, and a merge would write fields
    the user did not ask for."""
    cfg = MineConfig(
        card_format={
            "Word": "{expression}",
            "Key": "{expression}",
            "Gloss": "{glossary}",
            "Pitch": "{pitch-accents}",
        }
    )

    note = build_note(
        cfg,
        CARD,
        markers={
            "expression": CARD.expression,
            "glossary": CARD.glossary_html,
            "pitch-accents": "よむ [0]",
        },
    )

    assert set(note["fields"]) == {"Word", "Key", "Gloss", "Pitch", *cfg.flags}
    assert note["fields"]["Word"] == note["fields"]["Key"] == "読む"
    assert note["fields"]["Pitch"] == "よむ [0]"


def test_the_expression_field_is_found_through_a_template() -> None:
    """The dedup key. Under `card_format` it is whichever field's template mentions {expression},
    since that is the field that actually gets written."""
    assert MineConfig(card_format={"Word": "{expression}"}).expression_field() == "Word"
    assert MineConfig().expression_field() == "Expression"
    assert MineConfig(card_format={"Gloss": "{glossary}"}).expression_field() == ""


@pytest.mark.parametrize(
    ("card_kind", "expected"),
    [
        ("sentence", {"IsSentenceCard": "1"}),
        ("word-and-sentence", {"IsWordAndSentenceCard": "1"}),
        ("click", {"IsClickCard": "1"}),
        ("audio", {"IsAudioCard": "1"}),
        ("none", {}),
        ("nonsense", {"IsWordAndSentenceCard": "1"}),
    ],
)
def test_an_unknown_card_kind_falls_back_rather_than_disabling_mining(
    card_kind: str, expected: dict[str, str]
) -> None:
    """A `[mine].card_kind` typo would otherwise mark no template and produce an unusable card."""
    assert MineConfig(card_kind=card_kind).flags == expected


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
