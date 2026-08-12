"""Nested-scan longest match: hovering an over-split inner word (コン of コンサート) must open the
whole dictionary term, mirroring the base tooltip's forward longest-match on a cue word."""

from __future__ import annotations

from util import FakeIPC

from saitenka.app import nested_popup
from saitenka.app.controller import Reader
from saitenka.app.tokenize import Token
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


def _tok(surface: str, start: int) -> Token:
    return Token(surface, surface, surface, "名詞", start, start + len(surface))


_SPLIT = [_tok("コン", 0), _tok("サート", 2)]  # unidic-style over-split of コンサート


def test_phrase_extra_terms_returns_the_longest_dictionary_match():
    reader = Reader(FakeIPC(), dict_set=_DS())
    assert nested_popup._phrase_extra_terms(reader, _SPLIT) == ("コンサート",)


def test_phrase_extra_terms_is_empty_when_the_dict_set_has_no_phrase_probe():
    reader = Reader(FakeIPC(), dict_set=object())
    assert nested_popup._phrase_extra_terms(reader, _SPLIT) == ()


class _RecordingDS:
    """Records the surface of every entry lookup, so a test can assert WHAT term was looked up."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        self.seen.append(tok.surface)
        return Entry(headword=[tok.surface], reading="", defs=[Definition("辞書", ["定義"])])


def test_link_query_is_looked_up_whole_not_tokenized():
    # Regression (それにしては → その): a cross-reference link ``?query=それにしては`` must look up the WHOLE
    # compound, not tokenize it and take the first token (それ). Both nav paths build a whole-query token.
    from saitenka.app import tooltip
    from saitenka.app.tokenize import query_token

    assert query_token("それにしては").surface == "それにしては"
    assert query_token("  ") is None

    ds = _RecordingDS()
    reader = Reader(FakeIPC(), dict_set=ds)
    tooltip._navigated_panel(reader, "それにしては")
    assert ds.seen == ["それにしては"]  # the WHOLE query reached the lookup, not それ


def test_open_link_navigates_the_whole_query(monkeypatch):
    from saitenka.app.subtitle_render import NullRenderer

    ds = _RecordingDS()
    reader = Reader(FakeIPC(), dict_set=ds)
    reader.osd = (1920, 1080)
    reader.sub_origin = (0, 0)
    monkeypatch.setattr(reader, "renderer", NullRenderer())
    lb = LinkBox("それにつけても", 0, 0, 10, 10)
    nested_popup.open_link(reader, lb, (0, 0), 0)  # no worker → synchronous open
    assert ds.seen == ["それにつけても"]  # the WHOLE query reached the lookup, not それ
    assert reader._nest.word == "それにつけても"  # …and it's the shown nested word


def test_phrase_extra_terms_is_empty_off_a_known_phrase():
    reader = Reader(FakeIPC(), dict_set=_DS())
    assert nested_popup._phrase_extra_terms(reader, [_tok("犬", 0), _tok("猫", 1)]) == ()


def test_show_nested_opens_the_whole_word_not_the_first_morpheme(monkeypatch):
    reader = Reader(FakeIPC(), dict_set=_DS())
    reader.osd = (1920, 1080)
    # Decouple from the live unidic split: the scan tail tokenises to コン + サート.
    monkeypatch.setattr(reader.tokenizer, "tokenize", lambda _s: _SPLIT)

    nested_popup.show_nested(reader, ScanBox("コンサート", 0, 0, 20, 20))

    assert reader._nest.state is not None, "a nested popup must open"
    # The longest match is stacked on the panel's identity — コンサート, not the bare コン.
    assert reader._nest.key.phrase_terms == ("コンサート",)
