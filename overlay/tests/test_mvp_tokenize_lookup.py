"""MVP: tokenizer (lemma/reading), furigana alignment, and JMdict → Entry adapter."""

import pytest

from overlay.app.lookup import entry_for, furigana
from overlay.app.tokenize import Token, tokenize

LINE = "門前の小僧習わぬ経を読む"


def _tok(surface: str) -> Token:
    return Token(surface, surface, "", "名詞", 0, len(surface))


@pytest.mark.parametrize(
    ("surface", "kana_only"),
    [
        ("そう", True),  # hiragana
        ("コーヒー", True),  # katakana incl. ー prolonged mark
        ("読む", False),  # has kanji
        (
            "ジョン・スミス",
            False,
        ),  # ・ nakaguro is a separator, not kana — must not read as kana-only
        ("A", False),  # latin
        ("", False),  # empty is not a kana word
    ],
)
def test_is_kana_only_excludes_katakana_punctuation(surface, kana_only):
    assert _tok(surface).is_kana_only is kana_only


def test_tokenize_surfaces_and_lemmas():
    toks = tokenize(LINE)
    # verb + conjugation tail merge into one hover unit (習わ + ぬ → 習わぬ), Yomitan-style
    assert [t.surface for t in toks] == ["門前", "の", "小僧", "習わぬ", "経", "を", "読む"]
    by_surface = {t.surface: t for t in toks}
    assert by_surface["習わぬ"].lemma == "習う"  # head verb's lemma drives the lookup
    assert by_surface["読む"].lemma == "読む"
    assert by_surface["小僧"].reading == "こぞう"  # katakana folded to hiragana
    # char offsets round-trip
    assert all(LINE[t.start : t.end] == t.surface for t in toks)


def test_auxiliary_verb_chains_merge_to_one_hover_unit():
    # N2: a verb + its て/で-auxiliary compound is ONE hover unit, whether the auxiliary is a verb
    # (いる/しまう/おく/いく/くる/みる) or an adjective (ほしい: ～てほしい).
    for text, head_lemma in [
        ("食べている", "食べる"),
        ("食べてしまう", "食べる"),
        ("やっておく", "遣る"),
        ("見ていく", "見る"),
        ("食べてくる", "食べる"),
        ("読んでみる", "読む"),
        ("食べてほしい", "食べる"),
        ("食べてほしかった", "食べる"),
        ("来てほしい", "来る"),
    ]:
        toks = tokenize(text)
        assert [t.surface for t in toks] == [text], (
            f"{text} did not merge: {[t.surface for t in toks]}"
        )
        assert toks[0].lemma == head_lemma  # the head verb's lemma drives the lookup


def test_merge_stops_at_real_word_boundaries():
    # the merge must not swallow following particles / clauses (格・係助詞 と・を・は)
    assert [t.surface for t in tokenize("預けたとしても")][:2] == ["預けた", "と"]
    assert [t.surface for t in tokenize("本を読む")] == ["本", "を", "読む"]
    assert [t.surface for t in tokenize("食べては")] == ["食べて", "は"]
    # ～てほしい merges, but the clause boundary after it (と) still separates
    assert tokenize("手伝ってほしいと言った")[0].surface == "手伝ってほしい"


def _at(surface: str, start: int) -> Token:
    return Token(surface, surface, surface, "名詞", start, start + len(surface))


def test_phrase_terms_longest_match_first_from_cursor():
    from overlay.app.tokenize import phrase_terms

    # line 数ある魔法 → 数(0-1) ある(1-3) 魔法(3-5); only 数ある is a term
    toks = [_at("数", 0), _at("ある", 1), _at("魔法", 3)]
    got = phrase_terms(toks, 0, lambda s: s == "数ある")
    assert got == (["数ある"], 2)  # terms, span end (covers 数+ある)


def test_phrase_terms_orders_longest_first_when_nested_terms_exist():
    from overlay.app.tokenize import phrase_terms

    toks = [_at("数", 0), _at("ある", 1), _at("程", 3)]
    # both the 2-token and 3-token concatenations are terms → longest first, span reaches the longest
    got = phrase_terms(toks, 0, lambda s: s in {"数ある", "数あるほど", "数ある程"})
    assert got == (["数ある程", "数ある"], 3)


def test_phrase_terms_anchors_at_cursor_and_returns_none_off_a_phrase():
    from overlay.app.tokenize import phrase_terms

    toks = [_at("数", 0), _at("ある", 1)]
    # hovering ある (mid-phrase) scans forward from ある, not backward → no term
    assert phrase_terms(toks, 1, lambda s: s == "数ある") is None
    # a lone word with no longer term
    assert phrase_terms([_at("猫", 0)], 0, lambda _s: True) is None


def test_phrase_terms_stops_at_line_break_and_punctuation():
    from overlay.app.tokenize import phrase_terms

    # ある sits on the next source line: its start (0) resets below the previous end → not adjacent
    cross_line = [_at("数", 0), _at("X", 5), _at("ある", 0)]
    assert phrase_terms(cross_line, 0, lambda s: s == "数ある") is None
    # a punctuation token between the two words stops the scan (SKIP_POS)
    punct = [_at("数", 0), Token("、", "、", "、", "補助記号", 1, 2), _at("ある", 2)]
    assert phrase_terms(punct, 0, lambda s: s in {"数ある", "数、ある"}) is None


def test_furigana_alignment():
    assert furigana("読む", "よむ") == [
        {"tag": "ruby", "content": ["読", {"tag": "rt", "content": "よ"}]},
        "む",
    ]
    assert furigana("小僧", "こぞう") == [
        {"tag": "ruby", "content": ["小僧", {"tag": "rt", "content": "こぞう"}]}
    ]
    assert furigana("の", "の") == ["の"]


def test_lookup_entry_yomu():
    tok = next(t for t in tokenize(LINE) if t.surface == "読む")
    entry = entry_for(tok)
    assert "verb" in entry.tags
    assert entry.defs and entry.defs[0].dict_name == "JMdict"
    # the glosses include the primary meaning
    text = str(entry.defs[0].content)
    assert "to read" in text


def test_lookup_disambiguates_by_reading():
    # 本 is a secondary kanji of もと (元/本/素/基); the ほん-reading token must pick 本=book,
    # not JMdict's first-returned もと entry.
    tok = next(t for t in tokenize("本を読む") if t.surface == "本")
    assert tok.reading == "ほん"
    entry = entry_for(tok)
    text = str(entry.defs[0].content)
    assert "book" in text
    assert "origin" not in text  # the もと sense must not win


def test_lookup_particle_has_minimal_entry():
    tok = next(t for t in tokenize(LINE) if t.surface == "を")
    entry = entry_for(tok)
    assert entry.tags == ["particle"]
    assert entry.defs  # a minimal "not found" definition, never empty
