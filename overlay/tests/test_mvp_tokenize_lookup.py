"""MVP: tokenizer (lemma/reading), furigana alignment, and JMdict → Entry adapter."""

from overlay.app.lookup import entry_for, furigana
from overlay.app.tokenize import tokenize

LINE = "門前の小僧習わぬ経を読む"


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
