"""Latin tokenizer (#254 W1) — segmentation, elision, offsets, content/skip classification.

Constructed directly against the real strategy (no fakes needed — it's pure). Asserts observable output:
the token surfaces / offsets / POS kinds and the two predicates every reader call site routes through.
"""

from __future__ import annotations

from overlay.app.profiles import default_tokenizer_for
from overlay.app.tokenizer import get_tokenizer
from overlay.app.tokenizer_latin import PUNCT, WORD, LatinTokenizer


def _surfaces(line: str) -> list[str]:
    return [t.surface for t in LatinTokenizer().tokenize(line)]


def test_splits_words_on_whitespace_and_punctuation():
    assert _surfaces("Le chat noir.") == ["Le", " ", "chat", " ", "noir", "."]


def test_elision_splits_on_apostrophe():
    # l'homme → clitic + content word + the apostrophe as its own PUNCT token between them.
    toks = LatinTokenizer().tokenize("l'homme")
    assert [(t.surface, t.pos) for t in toks] == [
        ("l", WORD),
        ("'", PUNCT),
        ("homme", WORD),
    ]


def test_typographic_apostrophe_also_splits():
    assert _surfaces("qu’il") == ["qu", "’", "il"]


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


def test_lemma_is_the_surface_lookup_layer_deinflects():
    # The tokenizer does not lemmatize — an inflected "mangé" keeps its surface as the lemma; the
    # deinflector resolves it downstream. Guards against a future "helpful" stemming here.
    (tok,) = [t for t in LatinTokenizer().tokenize("mangé") if t.pos == WORD]
    assert tok.lemma == "mangé"


def test_french_language_resolves_latin_without_explicit_tokenizer():
    assert default_tokenizer_for("fr") == "latin"
    assert default_tokenizer_for("de-CH") == "latin"
    assert get_tokenizer("latin").name == "latin"
