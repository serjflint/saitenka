"""The tokenizer-strategy seam (app/tokenizer.py): registry, UnidicTokenizer delegation parity, and the
Reader-owned swappable seam a profile switch (#254) flips."""

import pytest
from overlay.app.controller import Reader
from overlay.app.token_cache import TokenizedCue
from overlay.app.tokenize import (
    Token,
    inflected_in,
    merge_dict_compounds,
    phrase_terms,
    query_token,
    tokenize,
)
from overlay.app.tokenizer import (
    DEFAULT_TOKENIZER,
    UnidicTokenizer,
    get_tokenizer,
    register_tokenizer,
)


class FakeIPC:
    def __init__(self, props=None):
        self.props = props or {}
        self.commands = []

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}


class _FakeTokenizer:
    """A stand-in strategy (whitespace, no morphology) — proves the reader routes through whatever
    strategy it holds, not the JP module functions."""

    name = "fake"

    def tokenize(self, _line, *, _strip_furigana=True, _merge=True):
        return []

    def query_token(self, _query):
        return None

    def inflected_in(self, tokens, index):
        return tokens[index].surface

    def phrase_terms(self, _tokens, _index, _has_term):
        return None

    def merge_dict_compounds(self, tokens, _exists):
        return tokens


@pytest.fixture(autouse=True)
def _restore_registry():
    """Isolate registry mutations — a register_tokenizer in one test must not leak into another
    (pytest-randomly reorders)."""
    import overlay.app.tokenizer as mod

    saved = dict(mod._FACTORIES)
    yield
    mod._FACTORIES.clear()
    mod._FACTORIES.update(saved)


def test_get_tokenizer_defaults_to_unidic():
    tok = get_tokenizer()
    assert tok.name == "unidic"
    assert DEFAULT_TOKENIZER == "unidic"


def test_get_tokenizer_unknown_name_raises_listing_registered():
    with pytest.raises(ValueError, match=r"unknown tokenizer 'nope'.*unidic"):
        get_tokenizer("nope")


def test_register_tokenizer_makes_it_retrievable():
    register_tokenizer("fake-latin", _FakeTokenizer)
    assert get_tokenizer("fake-latin").name == "fake"


def test_unidic_tokenize_matches_module_function():
    line = "習わぬ経を読む"
    assert UnidicTokenizer().tokenize(line) == tokenize(line)


def test_unidic_query_token_matches_module_function():
    assert UnidicTokenizer().query_token("それにしては") == query_token("それにしては")


def test_unidic_inflected_in_matches_module_function():
    toks = tokenize("習わぬ")
    assert UnidicTokenizer().inflected_in(toks, 0) == inflected_in(toks, 0)


def test_unidic_phrase_terms_matches_module_function():
    toks = tokenize("お休み")

    def has_term(surface):
        return surface == "お休み"

    assert UnidicTokenizer().phrase_terms(tokens=toks, index=1, has_term=has_term) == phrase_terms(
        tokens=toks, index=1, has_term=has_term
    )


def test_unidic_merge_dict_compounds_matches_module_function():
    toks = tokenize("応急処置")

    def exists(_surfaces):
        return set()

    assert UnidicTokenizer().merge_dict_compounds(toks, exists) == merge_dict_compounds(
        toks, exists
    )


def test_reader_owns_unidic_tokenizer_by_default():
    reader = Reader(FakeIPC())
    assert reader.tokenizer.name == "unidic"


def test_use_tokenizer_swaps_strategy_and_clears_cache():
    reader = Reader(FakeIPC())
    tok = Token(surface="本", lemma="本", reading="ほん", pos="名詞", start=0, end=1)
    reader.token_cache.put("本", TokenizedCue(lines=[[tok]], tokens=[tok], styles=None))
    assert len(reader.token_cache) == 1

    fake = _FakeTokenizer()
    reader.use_tokenizer(fake)

    assert reader.tokenizer is fake
    assert (
        len(reader.token_cache) == 0
    )  # cached JP segmentation must not leak into the new strategy
