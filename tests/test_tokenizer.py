"""The tokenizer-strategy seam: registry, delegation parity, and ProfileController ownership."""

import pytest
import util
from saitenka_tokenize.japanese import (
    Token,
    inflected_in,
    merge_dict_compounds,
    phrase_terms,
    query_token,
    tokenize,
)
from saitenka_tokenize.registry import (
    DEFAULT_TOKENIZER,
    UnidicTokenizer,
    get_tokenizer,
    register_tokenizer,
)
from session_builder import build_session

from saitenka.app.session.factory import SessionServices


class FakeIPC(util.FakeIPC):
    def __init__(self, props=None):
        super().__init__()
        self.props.update(props or {})


class _FakeTokenizer:
    """A stand-in strategy (whitespace, no morphology) — proves the reader routes through whatever
    strategy it holds, not the JP module functions."""

    name = "fake"

    def tokenize(self, _line, *, _strip_furigana=True, _merge=True):
        return []

    def is_content(self, _token):
        return True

    def is_skippable(self, token):
        return not token.surface.strip()

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
    import saitenka_tokenize.registry as mod

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
    reader = build_session(FakeIPC())
    assert reader.graph.profile.profile.tokenizer.name == "unidic"


class _SpyTokenizer(_FakeTokenizer):
    """Records which Protocol method each call site reached, so a test can assert ROUTING (the call
    happened through the active strategy) without caring about the JP-specific return value."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def tokenize(self, line, *, strip_furigana=True, merge=True):
        self.calls.append("tokenize")
        return super().tokenize(line, _strip_furigana=strip_furigana, _merge=merge)

    def query_token(self, query):
        self.calls.append("query_token")
        return super().query_token(query)

    def phrase_terms(self, tokens, index, has_term):
        self.calls.append("phrase_terms")
        return super().phrase_terms(tokens, index, has_term)


def test_swapped_tokenizer_reroutes_tooltip_phrase_probing():
    """A profile swap (``use_tokenizer``) must reroute the base tooltip's hover phrase-probe through
    the NEW strategy, not the JP module functions directly — the core of #254 phase 3a.2."""
    from saitenka.app.features.tooltip import tooltip

    class _DS:
        def has_term(self, *_forms):
            return True

    reader = build_session(FakeIPC(), services=SessionServices(dictionaries=_DS()))
    spy = _SpyTokenizer()
    reader.graph.profile.profile.use_tokenizer(spy)
    reader.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token(surface="本", lemma="本", reading="ほん", pos="名詞", start=0, end=1)]
    )

    tooltip.resolve_hover(
        reader.graph.tooltip.tip_ports,
        reader.graph.tooltip.word_lookup,
        reader.graph.tooltip.hover_inputs,
        0,
    )

    assert "phrase_terms" in spy.calls


def test_swapped_tokenizer_reroutes_nested_popup_link_lookup():
    """A profile swap must reroute a clicked cross-reference link's whole-query lookup through the
    NEW strategy, not ``tokenize.py``'s ``query_token`` directly."""
    from saitenka.app.features.tooltip import nested_popup
    from saitenka.model import LinkBox

    reader = build_session(FakeIPC(), services=SessionServices(dictionaries=object()))
    spy = _SpyTokenizer()
    reader.graph.profile.profile.use_tokenizer(spy)
    lb = LinkBox("query", 0, 0, 10, 10)

    nested_popup.open_link(
        reader.graph.tooltip.tip_ports,
        reader.graph.tooltip.panel_ports,
        lb,
        (0, 0),
        0,
    )

    assert "query_token" in spy.calls


def test_unidic_is_content_matches_pos_whitelist():
    """Negative control: the JP strategy's content-ness is exactly the unidic POS whitelist — a 名詞 is
    content, a 助詞 (particle) is not — so nothing about Japanese classification moved."""
    tok = UnidicTokenizer()
    assert tok.is_content(Token("本", "本", "ほん", "名詞", 0, 1))
    assert not tok.is_content(Token("は", "は", "は", "助詞", 0, 1))
    assert tok.is_skippable(Token("。", "。", "", "補助記号", 0, 1))
    assert not tok.is_skippable(Token("本", "本", "ほん", "名詞", 0, 1))


class _ParticleContentTokenizer(_FakeTokenizer):
    """Inverts the JP partition: only 助詞 (particles) count as content. A tokenizer is free to define
    content-ness however it likes — the mine path must follow whatever strategy the reader holds."""

    def is_content(self, token):
        return token.pos == "助詞"


def testmine_target_follows_the_active_tokenizers_content_partition():
    """The word ``mine_target`` picks is decided by ``reader.tokenizer.is_content``, not baked JP POS.
    Same tokens, two strategies → two different mined tokens; the unidic case is the JP negative
    control (the 名詞, never the 助詞)."""
    from saitenka.app.features.mining.miner import MineCue, mine_target

    particle = Token("は", "は", "は", "助詞", 0, 1)
    noun = Token("本", "本", "ほん", "名詞", 1, 2)

    jp = build_session(FakeIPC())
    tokens = [particle, noun]
    assert mine_target(MineCue(tokens, None, -1, jp.graph.profile.profile.tokenizer, 20)) == 1

    swapped = build_session(FakeIPC())
    swapped.graph.profile.profile.use_tokenizer(_ParticleContentTokenizer())
    assert mine_target(MineCue(tokens, None, -1, swapped.graph.profile.profile.tokenizer, 20)) == 0


def test_use_tokenizer_swaps_strategy_and_clears_cache():
    class _DS:
        def terms_exist(self, _forms):
            return set()

    reader = build_session(FakeIPC(), services=SessionServices(dictionaries=_DS()))
    reader.graph.cue.set_subtitle("本")

    fake = _FakeTokenizer()
    reader.graph.profile.profile.use_tokenizer(fake)
    reader.graph.cue.set_subtitle("本")

    assert reader.graph.profile.profile.tokenizer is fake
    assert reader.graph.subtitle_presentation.cue.current.tokens == []
