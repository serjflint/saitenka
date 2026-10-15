"""Nested-scan longest match: hovering an over-split inner word (コン of コンサート) must open the
whole dictionary term, mirroring the base tooltip's forward longest-match on a cue word."""

from __future__ import annotations

import dicthelp
import pytest
from driver import Driver
from saitenka_tokenize.japanese import Token
from saitenka_tokenize.registry import UnidicTokenizer
from util import FakeIPC, await_ready, session_gateway

from saitenka.app.features.tooltip import hover_metadata, nested_popup
from saitenka.app.session.factory import SessionInfrastructure, SessionServices, TooltipWorkMode
from saitenka.app.subtitle_raster import PillowRasterProvider
from saitenka.app.subtitle_render import SubtitleRenderer
from saitenka.model import LinkBox
from saitenka.panel import Definition, Entry

_SPLIT = (
    Token("コン", "コン", "コン", "名詞", 0, 2),
    Token("サート", "サート", "サート", "名詞", 2, 5),
)


class _RecordingDS:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        self.seen.append(tok.surface)
        return Entry(headword=[tok.surface], reading="", defs=[Definition("辞書", ["定義"])])

    def rareness_rank(self, _token):
        return None


@pytest.mark.parametrize(("query", "nested"), [("それにしては", False), ("それにつけても", True)])
def test_link_query_is_looked_up_whole_not_tokenized(monkeypatch, make_session, query, nested):
    from saitenka.app.subtitle_render import NullRenderer

    ds = _RecordingDS()
    reader = make_session(FakeIPC(), services=SessionServices(dictionaries=ds))
    if nested:
        reader.graph.screen.osd = (1920, 1080)
        monkeypatch.setattr(reader.graph.subtitle_presentation, "renderer", NullRenderer())
        nested_popup.open_link(
            reader.graph.tooltip.tip_ports,
            reader.graph.tooltip.panel_ports,
            LinkBox(query, 0, 0, 10, 10),
            (0, 0),
            0,
        )
        assert reader.graph.tooltip.surface_state().nest.word == query
    else:
        reader.graph.tooltip.navigated_panel(query)

    assert ds.seen == [query]


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("probe", ["match", "miss", "absent"])
@pytest.mark.parametrize("work", [TooltipWorkMode.DEFERRED, TooltipWorkMode.INLINE])
def test_show_nested_opens_the_whole_word_not_the_first_morpheme(
    monkeypatch, make_session, tmp_path, probe, work
):
    tail = "犬猫" if probe == "miss" else "コンサート"
    split = (
        (Token("犬", "犬", "いぬ", "名詞", 0, 1), Token("猫", "猫", "ねこ", "名詞", 1, 2))
        if probe == "miss"
        else _SPLIT
    )
    terms = [
        ["猫", "ねこ", [tail]],
        ["コン", "コン", ["prefix"]],
        ["犬", "いぬ", ["dog"]],
        ["コンサート", "コンサート", ["whole phrase"]],
    ]
    dictionary = dicthelp.load_set([dicthelp.term_zip(tmp_path / "words.zip", "Words", terms)])
    if probe == "absent":
        monkeypatch.setattr(dictionary, "has_term", None)

    class SplitTokenizer(UnidicTokenizer):
        def tokenize(self, text, **kwargs):
            return list(split) if text == tail else super().tokenize(text, **kwargs)

    tokenizer = SplitTokenizer()
    monkeypatch.setattr(hover_metadata, "get_tokenizer", lambda _name: tokenizer)
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    gateway = session_gateway(ipc)
    reader = make_session(
        ipc,
        services=SessionServices(dictionaries=dictionary),
        infrastructure=SessionInfrastructure(
            renderer=SubtitleRenderer(PillowRasterProvider()), tooltip_work=work
        ),
    )
    monkeypatch.setattr(reader.graph.profile.profile.tokenizer, "tokenize", tokenizer.tokenize)
    try:
        reader.graph.presentation.refresh_osd()
        reader.graph.playback.observe("sub-text", "猫")
        reader.pump()
        ui = Driver(reader).move_to_word(0)
        await_ready(lambda: ui.tip_shown, "base tooltip did not open", pump=reader.pump)
        view = reader.graph.tooltip.surface_state().view
        box = next(box for box in view.state.windowed.scan_boxes() if box.text == tail)

        scale = reader.graph.tooltip.scale().raster
        ui.move(
            view.xy[0] + (box.x + box.w / 2) * scale,
            view.xy[1] + (box.y - view.scroll + box.h / 2) * scale,
        )

        await_ready(lambda: ui.nested_shown, "nested tooltip did not open", pump=reader.pump)
        expected = {"match": "コンサート", "miss": "いぬ", "absent": "コン"}[probe]
        assert reader.graph.tooltip.surface_state().nest.state.reading == expected
    finally:
        reader.close()
        gateway.close()
