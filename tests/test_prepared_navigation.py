"""Indexed navigation commits the destination it presents, even while mpv lags."""

import pytest
from saitenka_subtitles import Cue, CueIndex
from session_builder import build_session
from util import FakeIPC

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.playback_observation import OBSERVED_PROPERTIES
from saitenka.app.subtitle_intents import SeekCue, SubtitleCommand
from saitenka.app.subtitle_render import NullRenderer


@pytest.mark.parametrize("delay", [-0.5, 0.0, 1.5])
@pytest.mark.parametrize("cotimed", [False, True])
def test_rapid_navigation_seeks_the_presented_index_despite_stale_mpv_position(delay, cotimed):
    ipc = FakeIPC()
    ipc.props.update({"sub-delay": delay, "time-pos": 1.5 + delay, "sub-start": 1 + delay})
    session = build_session(
        ipc,
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    try:
        session.graph.track_commands.navigation.current.sub_index = CueIndex(
            [
                Cue(1, 2, "猫"),
                Cue(4, 5, "犬"),
                Cue(7, 8, "猫"),
            ]
            + ([Cue(1, 2, "鳥"), Cue(4, 5, "魚")] if cotimed else [])
        )
        session.graph.playback.observe("sub-text", "猫\n鳥" if cotimed else "猫")
        session.pump()
        navigation = session.graph.subtitle_navigation

        for _ in range(2):
            navigation.seek(SeekCue(1, session.graph.cue.revision))

        seeks = [c for c in ipc.commands if c[0] in {"seek", "sub-seek"}]
        assert [c[0] for c in seeks] == ["seek", "seek"]
        assert [float(c[1]) for c in seeks] == pytest.approx([4.01 + delay, 7.01 + delay])
        assert all(c[2] == "absolute+exact" for c in seeks)
        assert session.graph.playback.cue.text == "猫"
    finally:
        session.close()


def test_indexed_navigation_uses_observed_inputs_without_synchronous_mpv_reads():
    ipc = FakeIPC()
    ipc.props.update({"sub-text": "猫", "sub-start": 1.0, "time-pos": 1.5})
    session = build_session(
        ipc,
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    try:
        session.graph.track_commands.navigation.current.sub_index = CueIndex(
            [
                Cue(1, 2, "猫"),
                Cue(4, 5, "犬"),
            ]
        )
        session.graph.playback.install_seed(
            {name: ipc.props.get(name) for name in OBSERVED_PROPERTIES}
        )
        session.graph.playback.observe("sub-text", "猫")
        session.pump()
        ipc.commands.clear()

        session.graph.stateless_commands.run(SubtitleCommand.NAVIGATE_NEXT)

        assert ("seek", "4.01", "absolute+exact") in ipc.commands
        assert not [c for c in ipc.commands if c[0] == "get_property"]
    finally:
        session.close()


@pytest.mark.parametrize(
    "observations",
    [
        (("sub-text", "猫"),),
        (("sub-text", "犬"), ("sub-text", "猫")),
        (("sub-text", ""), ("sub-text", "猫"), ("sub-text", "犬")),
        (("sub-start", 4.0), ("sub-text", "犬")),
        (("sub-end", 5.0), ("sub-text", "犬")),
        (("sub-start", 4.0), ("sub-end", 5.0), ("sub-text", "犬")),
    ],
)
def test_observation_from_superseded_seek_cannot_replace_latest_navigation(observations):
    ipc = FakeIPC()
    ipc.props.update({"time-pos": 1.5, "sub-start": 1})
    session = build_session(
        ipc,
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    try:
        session.graph.track_commands.navigation.current.sub_index = CueIndex(
            [
                Cue(1, 2, "猫"),
                Cue(4, 5, "犬"),
                Cue(7, 8, "鳥"),
            ]
        )
        session.graph.playback.observe("sub-text", "猫")
        session.pump()
        for _ in range(2):
            session.graph.subtitle_navigation.seek(SeekCue(1, session.graph.cue.revision))

        for name, value in observations:
            session.graph.playback.observe(name, value)
            session.pump()

        assert session.graph.playback.cue.text == "鳥"
    finally:
        session.close()


def test_landed_navigation_allows_a_short_cue_to_advance_to_repeated_prior_text():
    ipc = FakeIPC()
    ipc.props.update({"time-pos": 1.5, "sub-start": 1})
    session = build_session(
        ipc,
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    try:
        session.graph.track_commands.navigation.current.sub_index = CueIndex(
            [Cue(1, 2, "猫"), Cue(4, 5, "犬"), Cue(7, 7.1, "鳥"), Cue(7.1, 8, "猫")]
        )
        session.graph.playback.observe("sub-text", "猫")
        session.pump()
        for _ in range(2):
            session.graph.subtitle_navigation.seek(SeekCue(1, session.graph.cue.revision))
        session.graph.playback.observe("sub-text", "鳥")
        session.pump()

        session.graph.playback.observe("sub-text", "猫")
        session.pump()

        assert session.graph.playback.cue.text == "猫"
    finally:
        session.close()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("options/sub-speed", 1.1),
        ("options/sub-fps", 25),
        ("options/play-direction", "backward"),
        ("options/sub-filter-sdh", True),
    ],
)
def test_unmodelled_timing_or_filtering_delegates_without_provisional_color(option, value):
    ipc = FakeIPC()
    ipc.props.update({"time-pos": 1.5, "sub-start": 1, option: value})
    session = build_session(
        ipc,
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    try:
        session.graph.track_commands.navigation.current.sub_index = CueIndex(
            [
                Cue(1, 2, "猫"),
                Cue(4, 5, "犬"),
            ]
        )
        session.graph.playback.observe("sub-text", "猫")
        session.pump()

        session.graph.subtitle_navigation.seek(SeekCue(1, session.graph.cue.revision))

        assert [c for c in ipc.commands if c[0] in {"seek", "sub-seek"}] == [("sub-seek", "1")]
        assert session.graph.playback.cue.text == "猫"
    finally:
        session.close()
