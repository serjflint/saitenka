"""French mining preset (#254 W6) — a French note type carries no kana-reading field, and a
Latin-tokenized mine grounds French definitions into the glossary with no furigana/pitch.

Constructed CardData (no live jamdict), so it runs regardless of the jmdict extra — the point is the
field MAP, not the JP dictionary.
"""

from __future__ import annotations

from saitenka_card import FRENCH_FIELDS, LAPIS_FIELDS, CardData, MineConfig, build_note


def test_french_preset_has_no_reading_field():
    cfg = MineConfig.from_preset("French")
    assert "reading" not in cfg.fields  # French has no kana reading
    assert cfg.model == "French"
    # every other Lapis logical field survives (expression/sentence/glossary/audio/id/freq…)
    assert set(cfg.fields) == set(LAPIS_FIELDS) - {"reading"}


def test_french_mine_writes_expression_and_glossary_but_no_reading_field():
    cfg = MineConfig.from_preset("French")
    # A French card: expression + French glossary, empty reading (Latin tokenizer sets reading="").
    card = CardData(expression="manger", reading="", glossary_html="<div>to eat</div>")
    note = build_note(cfg, card)
    fields = note["fields"]
    assert fields["Expression"] == "manger"
    assert fields["Glossary"] == "<div>to eat</div>"
    assert "ExpressionReading" not in fields  # the JP reading field is never written for French
    assert note["modelName"] == "French"


def test_french_fields_is_lapis_minus_reading():
    assert {k: v for k, v in LAPIS_FIELDS.items() if k != "reading"} == FRENCH_FIELDS
