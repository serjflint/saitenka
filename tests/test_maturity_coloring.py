"""Acceptance coverage for FSRS maturity colors and their presentation boundary."""

import dicthelp
import pytest
from saitenka_dict import FreqDict
from saitenka_tokenize.japanese import Token
from saitenka_wordstate import Scorer
from saitenka_wordstate.fsrs import KnownSnap
from saitenka_wordstate.known import KnownWords
from util import FakeIPC, RecordingRasterProvider

import saitenka.app.features.profiles.dependencies as reader_deps
from saitenka.app.config import ReaderOptions, TooltipOptions
from saitenka.app.scoring import Coloring, Palette
from saitenka.app.session.factory import SessionInfrastructure, SessionServices
from saitenka.app.subtitle_render import SubtitleRenderer


def _token(surface: str = "本") -> Token:
    return Token(surface, surface, "ほん", "名詞", 0, len(surface), "")


@pytest.mark.parametrize("binary_known", [False, True])
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
def test_fsrs_state_has_configurable_style_and_precedes_frequency(
    state, tag, color_name, binary_known
):
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
                known=KnownWords.from_set(["本"] if binary_known else []),
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


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_hover_visibility_reuses_the_learning_style(make_session):
    scorer = Coloring(
        Scorer(
            known=KnownWords.from_set([]),
            fsrs_snap=KnownSnap.of({"本": "learning"}),
            enable_n_plus_one=False,
            enable_freq=False,
            enable_jlpt=False,
        )
    )
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    provider = RecordingRasterProvider(size=(10, 10))
    reader = make_session(
        ipc,
        services=SessionServices(
            scorer=scorer,
            dictionaries=dicthelp.load_set(),
        ),
        infrastructure=SessionInfrastructure(renderer=SubtitleRenderer(provider)),
        options=ReaderOptions(tooltip=TooltipOptions(annotation_mode="hover")),
    )
    reader.graph.presentation.refresh_osd()
    ipc.props["sub-text"] = "本"
    reader.graph.playback.observe("sub-text", "本")
    reader.pump()
    reader.graph.tooltip.select(0)
    assert provider.requests[-1].styles is None
    provider.requests.clear()

    for revealed in (True, False, True):
        reader.graph.tooltip.set_annotation_hover(revealed=revealed)

    assert [(request.hover, request.styles is None) for request in provider.requests] == [
        (0, False),
        (None, True),
        (0, False),
    ]
    assert [
        [(style.color, style.tag) for style in request.styles]
        for request in (provider.requests[0], provider.requests[2])
    ] == [[(scorer.palette.learning, "learning")]] * 2
