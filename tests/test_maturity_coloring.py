"""Acceptance coverage for FSRS maturity colors and their presentation boundary."""

import pytest
import util
from saitenka_dict import FreqDict
from saitenka_tokenize.japanese import Token
from saitenka_wordstate import Scorer
from saitenka_wordstate.fsrs import KnownSnap
from saitenka_wordstate.known import KnownWords
from util import RecordingRasterProvider

import saitenka.app.features.profiles.dependencies as reader_deps
from saitenka.app.config import ReaderOptions, TooltipOptions
from saitenka.app.scoring import Coloring, Palette
from saitenka.app.session.factory import SessionServices
from saitenka.app.subtitle_render import SubtitleRenderer


def _token(surface: str = "本") -> Token:
    return Token(surface, surface, "ほん", "名詞", 0, len(surface), "")


@pytest.mark.parametrize(
    ("state", "tag", "color_name"),
    [
        ("new", "freq", "freq_single"),
        ("learning", "learning", "learning"),
        ("young", "young", "young"),
        ("known", "known", "known"),
        ("forgotten", "forgotten", "forgotten"),
    ],
)
def test_fsrs_state_has_configurable_style_and_precedes_frequency(state, tag, color_name):
    palette = Palette(
        learning=(1, 2, 3, 255),
        young=(4, 5, 6, 255),
        known=(7, 8, 9, 255),
        forgotten=(10, 11, 12, 255),
        freq_single=(13, 14, 15, 255),
    )
    scorer = Coloring(
        Coloring(
            Scorer(
                known=KnownWords.from_set(["本"]),
                freq=FreqDict({"本": 1}, "test"),
                fsrs_snap=KnownSnap.of({"本": state}),
                enable_n_plus_one=False,
                enable_jlpt=False,
                freq_mode="single",
            )
        ),
        palette,
    )

    style = scorer.score_line([_token()])[0]

    assert (style.tag, style.color) == (tag, getattr(palette, color_name))


def test_n_plus_one_precedes_forgotten():
    tokens = [_token("私"), _token("人"), _token("本")]
    scorer = Coloring(
        Scorer(
            known=KnownWords.from_set(["私", "人"]),
            fsrs_snap=KnownSnap.of({"本": "forgotten"}),
            enable_freq=False,
            enable_jlpt=False,
        )
    )

    style = scorer.score_line(tokens)[2]

    assert (style.tag, style.color) == ("n+1", scorer.palette.n_plus_one)


def test_config_loads_fsrs_copy_and_controls_maturity_colors(tmp_path, monkeypatch):
    collection = tmp_path / "collection-copy.anki2"
    collection.touch()
    monkeypatch.setattr(
        "saitenka_wordstate.fsrs.load_knownness", lambda _path: KnownSnap.of({"本": "learning"})
    )
    scorer, _, _, _ = reader_deps.build_reader_deps(
        {
            "fsrs": {"collection": str(collection)},
            "palette": {"learning": "#010203", "young": "#040506"},
        },
        color=True,
    )

    assert scorer is not None
    assert scorer.score_line([_token()])[0].tag.startswith("learning")
    assert (scorer.palette.learning, scorer.palette.young) == (
        (1, 2, 3, 255),
        (4, 5, 6, 255),
    )


class _IPC(util.FakeIPC):
    pass


def test_hover_visibility_reuses_the_learning_style(monkeypatch, make_session):
    scorer = Coloring(
        Scorer(
            known=KnownWords.from_set([]),
            fsrs_snap=KnownSnap.of({"本": "learning"}),
            enable_n_plus_one=False,
            enable_freq=False,
            enable_jlpt=False,
        )
    )
    reader = make_session(
        _IPC(),
        services=SessionServices(
            scorer=scorer,
        ),
        options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover")),
    )
    reader.graph.subtitle_presentation.cue.replace_tokenized(tokens=[_token()])
    reader.graph.subtitle_presentation.cue.replace_tokenized(lines=[[object()]])
    reader.graph.subtitle_presentation.cue.replace_tokenized(
        styles=scorer.score_line(reader.graph.subtitle_presentation.cue.current.tokens)
    )
    reader.graph.tooltip.select(0)
    monkeypatch.setattr(reader.graph.overlay, "show", lambda *_args, **_kwargs: None)
    provider = RecordingRasterProvider(size=(10, 10))
    reader.graph.subtitle_presentation.renderer = SubtitleRenderer(provider)

    reader.graph.subtitle_presentation.draw()
    reader.graph.tooltip.set_annotation_hover(revealed=True)

    assert [request.styles for request in provider.requests] == [
        None,
        reader.graph.subtitle_presentation.cue.current.styles,
    ]
    assert reader.graph.subtitle_presentation.cue.current.styles[0].tag == "learning"
