"""Latin tokenizer (#254 W1) — segmentation, elision, offsets, content/skip classification.

Constructed directly against the real strategy (no fakes needed — it's pure). Asserts observable output:
the token surfaces / offsets / POS kinds and the two predicates every reader call site routes through.
"""

from __future__ import annotations

from overlay.app.profiles import default_tokenizer_for
from overlay.app.tokenizer import get_tokenizer
from overlay.app.tokenizer_latin import WORD, LatinTokenizer


def _surfaces(line: str) -> list[str]:
    return [t.surface for t in LatinTokenizer().tokenize(line)]


def test_splits_words_on_whitespace_and_punctuation():
    assert _surfaces("Le chat noir.") == ["Le", " ", "chat", " ", "noir", "."]


def test_elision_clitic_keeps_its_apostrophe_and_resolves_to_the_full_word():
    # l'homme → the clitic "l'" (one token, apostrophe swallowed, lemma "le") + the content word.
    # No bare "l" and no stray "'" — hovering the clitic looks up "le", not a meaningless letter.
    toks = LatinTokenizer().tokenize("l'homme")
    assert [(t.surface, t.lemma, t.pos) for t in toks] == [
        ("l'", "le", WORD),
        ("homme", "homme", WORD),
    ]


def test_negation_clitic_resolves_so_the_content_word_is_its_own_token():
    # Regression: "n'avait" used to tokenize to a bare "n" + "'" + "avait"; hovering showed just "n".
    toks = [t for t in LatinTokenizer().tokenize("n'avait") if t.pos == WORD]
    assert [(t.surface, t.lemma) for t in toks] == [("n'", "ne"), ("avait", "avait")]


def test_typographic_apostrophe_also_elides():
    toks = [t for t in LatinTokenizer().tokenize("qu’il") if t.pos == WORD]
    assert [(t.surface, t.lemma) for t in toks] == [("qu’", "que"), ("il", "il")]


def test_sentence_initial_capital_lowercases_the_lemma_for_lookup():
    # "Le magasin" / "Ça va" — the surface keeps its case (display/mining) but the lemma decapitalizes
    # so the article/pronoun resolves instead of a capitalised proper-noun homograph (Yomitan's
    # decapitalize text-processor).
    le, _sp, magasin = LatinTokenizer().tokenize("Le magasin")
    assert (le.surface, le.lemma) == ("Le", "le")
    assert (magasin.surface, magasin.lemma) == ("magasin", "magasin")
    (ca,) = [t for t in LatinTokenizer().tokenize("Ça") if t.pos == WORD]
    assert (ca.surface, ca.lemma) == ("Ça", "ça")


def test_offsets_index_into_the_line():
    tok = get_tokenizer("latin")
    toks = tok.tokenize("a été")
    # "été" starts after "a" + space → index 2, ends at 5; the surface must slice back out of the line.
    ete = toks[-1]
    assert ete.surface == "été"
    assert (ete.start, ete.end) == (2, 5)
    assert "a été"[ete.start : ete.end] == "été"


def test_accented_letters_stay_in_one_word():
    assert _surfaces("déjà où français") == ["déjà", " ", "où", " ", "français"]


def test_content_and_skippable_are_not_complements():
    tok = LatinTokenizer()
    (word,) = [t for t in tok.tokenize("mot") if t.pos == WORD]
    # a WORD is content and not skippable; PUNCT/SPACE are skippable and not content.
    assert tok.is_content(word) and not tok.is_skippable(word)
    for t in tok.tokenize(" ,"):
        assert tok.is_skippable(t) and not tok.is_content(t)


def test_query_token_is_the_whole_query():
    tok = LatinTokenizer()
    got = tok.query_token("  bonjour  ")
    assert got is not None
    assert (got.surface, got.lemma, got.pos) == ("bonjour", "bonjour", WORD)
    assert tok.query_token("   ") is None


def test_lemma_is_the_lowercased_surface_lookup_layer_deinflects():
    # The tokenizer does not lemmatize — an inflected "mangé" keeps its surface as the lemma (only
    # decapitalized); the deinflector resolves the inflection downstream. Guards against future
    # "helpful" stemming here.
    (tok,) = [t for t in LatinTokenizer().tokenize("mangé") if t.pos == WORD]
    assert tok.lemma == "mangé"


def test_french_language_resolves_latin_without_explicit_tokenizer():
    assert default_tokenizer_for("fr") == "latin"
    assert default_tokenizer_for("de-CH") == "latin"
    assert get_tokenizer("latin").name == "latin"
