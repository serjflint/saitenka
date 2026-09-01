"""WP4.5: navigation picks its target from the index and the observed facts, not from IPC order."""

from __future__ import annotations

import pytest
from saitenka_subtitles import Cue, CueIndex

from saitenka.app.subnav_policy import (
    cue_is_on_screen,
    filters_can_drop_a_cue,
    resolve_target,
)

CUES = (
    Cue(1.0, 3.0, "いち"),
    Cue(5.0, 7.0, "に"),
    Cue(9.0, 11.0, "さん"),
)
INDEX = CueIndex(list(CUES))
EMPTY = CueIndex([])


def target(**overrides: object):
    values: dict = {
        "delta": 1,
        "text": "いち",
        "sub_start": 1.0,
        "time_pos": 2.0,
        "nav_idx": -1,
        **overrides,
    }
    index = values.pop("index", INDEX)
    return resolve_target(index, **values)  # type: ignore[arg-type]


# --- on-screen detection -------------------------------------------------------------------------


def test_visible_text_means_the_cue_is_on_screen() -> None:
    assert cue_is_on_screen(CUES[0], text="いち", sub_start=None, time_pos=None)


def test_a_gap_falls_back_to_the_timings() -> None:
    """During a seek the text is stale, so sub-start and time-pos are the only evidence."""
    assert cue_is_on_screen(CUES[0], text="", sub_start=1.5, time_pos=None)
    assert cue_is_on_screen(CUES[0], text="", sub_start=None, time_pos=2.0)


def test_a_position_outside_the_span_is_not_on_screen() -> None:
    assert not cue_is_on_screen(CUES[0], text="", sub_start=4.0, time_pos=4.0)
    assert not cue_is_on_screen(CUES[0], text="   ", sub_start=None, time_pos=None)


def test_the_cue_end_is_exclusive() -> None:
    assert not cue_is_on_screen(CUES[0], text="", sub_start=3.0, time_pos=None)
    assert cue_is_on_screen(CUES[0], text="", sub_start=2.999, time_pos=None)


# --- target selection ----------------------------------------------------------------------------


def test_next_steps_forward_from_the_showing_cue() -> None:
    chosen = target(delta=1)

    assert chosen is not None
    assert (chosen.index, chosen.cue.text) == (1, "に")


def test_previous_steps_back_from_the_showing_cue() -> None:
    chosen = target(delta=-1, text="に", sub_start=5.0, time_pos=6.0)

    assert chosen is not None
    assert chosen.cue.text == "いち"


def test_replay_returns_the_showing_cue() -> None:
    chosen = target(delta=0)

    assert chosen is not None
    assert chosen.cue.text == "いち"


def test_an_empty_index_has_no_target() -> None:
    assert target(index=EMPTY) is None


def test_text_that_matches_no_cue_has_no_target() -> None:
    assert target(text="どれでもない", sub_start=None, time_pos=None) is None


def test_stepping_past_the_last_cue_defers_to_mpv() -> None:
    assert target(delta=1, text="さん", sub_start=9.0, time_pos=10.0) is None


def test_stepping_before_the_first_cue_defers_to_mpv() -> None:
    assert target(delta=-1, text="いち", sub_start=1.0, time_pos=2.0) is None


# --- chaining while a seek is in flight ------------------------------------------------------------


def test_repeated_next_chains_forward_on_stale_timings() -> None:
    """After a nav render the timings still describe the OLD position; nav_idx is what keeps
    next/next/next stepping forward instead of snapping back."""
    first = target(delta=1)
    assert first is not None

    second = resolve_target(
        INDEX,
        delta=1,
        text=first.cue.text,
        sub_start=1.0,  # stale: mpv has not caught up
        time_pos=2.0,
        nav_idx=first.index,
    )

    assert second is not None
    assert (second.index, second.cue.text) == (2, "さん")


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_a_target_always_indexes_the_cue_it_returns(delta: int) -> None:
    chosen = target(delta=delta, text="に", sub_start=5.0, time_pos=6.0)

    assert chosen is not None
    assert INDEX.cues[chosen.index] is chosen.cue


@pytest.mark.parametrize(
    "settings",
    [
        {"sub-filter-sdh": True},
        {"sub-filter-regex": ["^SIGN:"]},
        {"sub-filter-jsre": ["/^\\[.*\\]$/"]},
        {"sub-filter-regex-enable": True, "sub-filter-regex": ["x"]},
        # mpv's string spellings for the same flags. `is True` read every one of these as "off",
        # which gives back the instant navigation on exactly the session that cannot have it.
        {"sub-filter-sdh": "yes"},
        {"sub-filter-sdh": "YES"},
        {"sub-filter-regex-enable": "yes", "sub-filter-regex": ["^SIGN:"]},
        {"sub-filter-sdh": 1},
    ],
)
def test_a_filter_that_can_drop_a_cue_is_detected(settings: dict) -> None:
    """The cue index is the subtitle FILE's; these run between the file and the screen. With one
    active, stepping by index can land on a cue mpv never shows."""
    assert filters_can_drop_a_cue(settings) is True


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"sub-filter-sdh": False, "sub-filter-regex": [], "sub-filter-jsre": []},
        {"sub-filter-regex": None, "sub-filter-jsre": None},
        {"sub-filter-regex": ""},
        # Configured but switched off: mpv compiles nothing, so nothing is dropped.
        {"sub-filter-regex-enable": False, "sub-filter-regex": ["^SIGN:"]},
        {"sub-filter-regex-enable": "no", "sub-filter-regex": ["^SIGN:"]},
        {"sub-filter-sdh": "no"},
        {"sub-filter-sdh": 0},
        # Neither spelling: an option that answers something unexpected is not evidence of a filter.
        {"sub-filter-sdh": "maybe"},
        {"sub-filter-sdh": 7},
    ],
)
def test_an_unfiltered_session_keeps_its_instant_navigation(settings: dict) -> None:
    """The negative control, and the expensive half: this guard gives up the instant render, so a
    reading that fired on an ordinary session would cost every user the feature."""
    assert filters_can_drop_a_cue(settings) is False
