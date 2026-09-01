"""Nested-scan longest match: hovering an over-split inner word (コン of コンサート) must open the
whole dictionary term, mirroring the base tooltip's forward longest-match on a cue word."""

from __future__ import annotations

from saitenka_tokenize.japanese import Token
from saitenka_tokenize.registry import get_tokenizer
from util import FakeIPC

from saitenka.app.features.tooltip import nested_popup
from saitenka.app.session.factory import SessionServices
from saitenka.model import LinkBox, ScanBox
from saitenka.panel import Definition, Entry


class _DS:
    """A dict set that knows コンサート as a multi-token term but not its コン / サート pieces."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        head = extra_terms[0] if extra_terms else tok.surface
        return Entry(
            headword=[head],
            reading=getattr(tok, "reading", "") or head,
            defs=[Definition("辞書", ["定義"])],
        )

    def has_term(self, *forms):
        return "コンサート" in forms

    def rareness_rank(self, _token):  # protocol shape
        """No frequency dictionaries, so no blended rank and no pill."""
        return


def _tok(surface: str, start: int) -> Token:
    return Token(surface, surface, surface, "名詞", start, start + len(surface))


_SPLIT = [_tok("コン", 0), _tok("サート", 2)]  # unidic-style over-split of コンサート


def _extra_terms(dict_set, tokens):
    """The probe needs a dict set and a tokenizer, so it takes them — no session in the way."""
    return nested_popup._phrase_extra_terms(tokens, dict_set=dict_set, tokenizer=get_tokenizer())


def test_phrase_extra_terms_returns_the_longest_dictionary_match():
    assert _extra_terms(_DS(), _SPLIT) == ("コンサート",)


def test_phrase_extra_terms_is_empty_when_the_dict_set_has_no_phrase_probe():
    assert _extra_terms(object(), _SPLIT) == ()


class _RecordingDS:
    """Records the surface of every entry lookup, so a test can assert WHAT term was looked up."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        self.seen.append(tok.surface)
        return Entry(headword=[tok.surface], reading="", defs=[Definition("辞書", ["定義"])])

    def rareness_rank(self, _token):  # protocol shape
        """No frequency dictionaries, so no blended rank and no pill."""
        return


def test_link_query_is_looked_up_whole_not_tokenized(make_session):
    # Regression (それにしては → その): a cross-reference link ``?query=それにしては`` must look up the WHOLE
    # compound, not tokenize it and take the first token (それ). Both nav paths build a whole-query token.
    from saitenka_tokenize.japanese import query_token

    assert query_token("それにしては").surface == "それにしては"
    assert query_token("  ") is None

    ds = _RecordingDS()
    reader = make_session(FakeIPC(), services=SessionServices(dictionaries=ds))
    reader.graph.tooltip.navigated_panel("それにしては")
    assert ds.seen == ["それにしては"]  # the WHOLE query reached the lookup, not それ


def test_open_link_navigates_the_whole_query(monkeypatch, make_session):
    from saitenka.app.subtitle_render import NullRenderer

    ds = _RecordingDS()
    reader = make_session(FakeIPC(), services=SessionServices(dictionaries=ds))
    reader.graph.screen.osd = (1920, 1080)
    reader.graph.subtitle_presentation.cue.replace_geometry(origin=(0, 0))
    monkeypatch.setattr(reader.graph.subtitle_presentation, "renderer", NullRenderer())
    lb = LinkBox("それにつけても", 0, 0, 10, 10)
    nested_popup.open_link(
        reader.graph.tooltip.tip_ports,
        reader.graph.tooltip.panel_ports,
        lb,
        (0, 0),
        0,
    )  # no worker → synchronous open
    assert ds.seen == ["それにつけても"]  # the WHOLE query reached the lookup, not それ
    assert (
        reader.graph.tooltip.surface_state().nest.word == "それにつけても"
    )  # …and it's the shown nested word


def test_phrase_extra_terms_is_empty_off_a_known_phrase():
    assert _extra_terms(_DS(), [_tok("犬", 0), _tok("猫", 1)]) == ()


def test_show_nested_opens_the_whole_word_not_the_first_morpheme(monkeypatch, make_session):
    reader = make_session(FakeIPC(), services=SessionServices(dictionaries=_DS()))
    reader.graph.screen.osd = (1920, 1080)
    # Decouple from the live unidic split: the scan tail tokenises to コン + サート.
    monkeypatch.setattr(reader.graph.profile.profile.tokenizer, "tokenize", lambda _s: _SPLIT)

    nested_popup.show_nested(
        reader.graph.tooltip.tip_ports,
        reader.graph.tooltip.panel_ports,
        reader.graph.tooltip.word_lookup,
        ScanBox("コンサート", 0, 0, 20, 20),
    )

    assert reader.graph.tooltip.surface_state().nest.state is not None, "a nested popup must open"
    # The longest match is stacked on the panel's identity — コンサート, not the bare コン.
    assert reader.graph.tooltip.surface_state().nest.key.phrase_terms == ("コンサート",)
