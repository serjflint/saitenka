"""The tokenizer-strategy seam: registry, delegation parity, and ProfileController ownership."""

import pytest
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
from util import FakeIPC

from saitenka.app.session.factory import SessionServices


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


def test_reader_owns_unidic_tokenizer_by_default(make_session):
    reader = make_session(FakeIPC())
    assert reader.graph.profile.profile.tokenizer.name == "unidic"


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("work", ["INLINE", "DEFERRED"])
def test_swapped_tokenizer_reroutes_tooltip_phrase_probing(make_session, tmp_path, work):
    import dicthelp
    from driver import Driver
    from util import await_ready, session_gateway

    from saitenka.app.session.factory import SessionInfrastructure, TooltipWorkMode
    from saitenka.app.subtitle_raster import PillowRasterProvider
    from saitenka.app.subtitle_render import SubtitleRenderer

    class RoutedTokenizer(UnidicTokenizer):
        name = "test-hover-routing"

        def phrase_terms(self, tokens, index, has_term):
            if tokens[index].surface == "猫" and has_term("特別"):
                return ["特別"], index, index + 1
            return None

    register_tokenizer(RoutedTokenizer.name, RoutedTokenizer)
    dictionary = dicthelp.load_set(
        [
            dicthelp.term_zip(
                tmp_path / "words.zip",
                "Words",
                [
                    ["猫", "ねこ", ["ordinary lookup"]],
                    ["特別", "とくべつ", ["selected strategy lookup"]],
                ],
            )
        ]
    )
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    gateway = session_gateway(ipc)
    reader = make_session(
        ipc,
        services=SessionServices(dictionaries=dictionary),
        infrastructure=SessionInfrastructure(
            renderer=SubtitleRenderer(PillowRasterProvider()),
            tooltip_work=TooltipWorkMode[work],
        ),
    )
    try:
        reader.graph.profile.profile.use_tokenizer(RoutedTokenizer())
        reader.graph.presentation.refresh_osd()
        reader.graph.playback.observe("sub-text", "猫")
        reader.pump()
        ui = Driver(reader).move_to_word(0)
        await_ready(
            lambda: ui.tip_shown, "selected strategy tooltip did not open", pump=reader.pump
        )

        assert reader.graph.tooltip.surface_state().view.state.reading == "とくべつ"
    finally:
        reader.close()
        gateway.close()


def test_swapped_tokenizer_reroutes_nested_popup_link_lookup(make_session, tmp_path):
    import dicthelp
    from driver import Driver

    from saitenka.app.features.tooltip import nested_popup
    from saitenka.model import LinkBox

    class RoutedTokenizer(UnidicTokenizer):
        def query_token(self, query):
            return super().query_token("特別" if query == "query" else query)

    dictionary = dicthelp.load_set(
        [
            dicthelp.term_zip(
                tmp_path / "words.zip",
                "Words",
                [
                    ["猫", "ねこ", ["base lookup"]],
                    ["特別", "とくべつ", ["selected lookup"]],
                ],
            )
        ]
    )
    reader = make_session(FakeIPC(), services=SessionServices(dictionaries=dictionary))
    reader.graph.profile.profile.use_tokenizer(RoutedTokenizer())
    reader.graph.presentation.refresh_osd()
    reader.graph.playback.observe("sub-text", "猫")
    reader.pump()
    Driver(reader).move_to_word(0)
    view = reader.graph.tooltip.surface_state().view

    nested_popup.open_link(
        reader.graph.tooltip.tip_ports,
        reader.graph.tooltip.panel_ports,
        LinkBox("query", 0, 0, 10, 10),
        view.xy,
        view.scroll,
    )

    nested = reader.graph.tooltip.surface_state().nest
    assert (nested.word, nested.state.reading) == ("特別", "とくべつ")


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
    from saitenka.app.features.mining.miner import MineCue, mine_target

    tokens = [Token("は", "は", "は", "助詞", 0, 1), Token("本", "本", "ほん", "名詞", 1, 2)]
    assert mine_target(MineCue(tokens, None, -1, UnidicTokenizer(), 20)) == 1
    assert mine_target(MineCue(tokens, None, -1, _ParticleContentTokenizer(), 20)) == 0


def test_use_tokenizer_swaps_strategy_and_clears_cache(make_session):
    class _DS:
        def terms_exist(self, _forms):
            return set()

    register_tokenizer(_FakeTokenizer.name, _FakeTokenizer)
    reader = make_session(FakeIPC(), services=SessionServices(dictionaries=_DS()))
    reader.graph.playback.observe("sub-text", "本")
    reader.pump()
    reader.graph.profile.profile.use_tokenizer(_FakeTokenizer())
    reader.graph.playback.observe("sub-text", "")
    reader.pump()

    reader.graph.playback.observe("sub-text", "本")
    reader.pump()

    assert reader.graph.subtitle_presentation.cue.current.tokens == []


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_a_busy_speculative_tagger_does_not_serialize_other_cues(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from saitenka_tokenize import japanese

    expected = japanese.tokenize("本を読む")
    expected_blocked = japanese.tokenize("看板")
    original = japanese._tagger
    entered, release = threading.Event(), threading.Event()

    class HeldNode:
        def __init__(self, node):
            self.node = node
            self.surface = node.surface

        @property
        def feature(self):
            entered.set()
            assert release.wait(5)
            return self.node.feature

    class HeldTagger:
        def __init__(self, tagger):
            self.tagger = tagger

        def __call__(self, text):
            nodes = list(self.tagger(text))
            return [HeldNode(node) for node in nodes] if text == "看板" else nodes

    monkeypatch.setattr(japanese, "_tagger", lambda: HeldTagger(original()))
    with ThreadPoolExecutor(max_workers=2) as executor:
        blocked = executor.submit(japanese.tokenize, "看板")
        try:
            assert entered.wait(2)
            live = executor.submit(japanese.tokenize, "本を読む")
            assert live.result(timeout=1) == expected
        finally:
            release.set()
        assert blocked.result(timeout=1) == expected_blocked
