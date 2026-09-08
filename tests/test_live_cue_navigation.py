"""Real mpv, real cue boundaries: a cue you navigate to must end up scannable.

Every defect in the cue-boundary family was found by a human watching an episode and sending a
report bundle. That is a terrible oracle -- it needs a person, an episode, and luck -- and it was
the only one available, because the live harness loaded a single cue spanning the whole clip and so
could not reach a *transition* at all.

The shape is from the field (`Ame to Kimi to` ep1): a cue whose neighbours' boundaries touch it, so
the successor arrives in the frame the predecessor ends, and whose neighbours are lines the
tokenizer skips entirely -- they own no box, so they can never explain away a missing one.

    0.5 - 2.5  ♬～
    2.5 - 5.0  犬…　かな？     <- the one that kept coming back unscannable
    5.0 - 7.5  ♬～

Opt-in: ``SAITENKA_LIVE=1`` — `uv run poe smoke-live`.
"""

from __future__ import annotations

import os

import pytest
from live_harness import BOUNDARY_CUES, live_reader, poll_until

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAITENKA_LIVE"),
    reason="live real-mpv test — set SAITENKA_LIVE=1; run `uv run poe smoke-live`",
)

SCANNABLE = "犬…　かな？"


def _cue_text(reader) -> str:
    return reader.graph.playback.cue.text


def _boxes(reader) -> list:
    return list(reader.graph.subtitle_presentation.cue.current.boxes)


@pytest.mark.live
@pytest.mark.timeout(30)
def test_a_cue_navigated_to_across_a_touching_boundary_becomes_scannable() -> None:
    """The whole family in one assertion: after stepping onto it, the cue carries boxes.

    Deliberately not "carries them immediately". The wait is a separate question with its own
    readout; this asks the one a viewer asks, which is whether the words are ever clickable at all.
    """
    with live_reader(cues=BOUNDARY_CUES) as (_tmp, reader, _ipc):
        reader.graph.subtitle_navigation.navigate(1)  # ♬～ -> 犬…　かな？, boundaries touching

        poll_until(
            reader,
            lambda: _cue_text(reader) == SCANNABLE,
            f"navigation never landed on {SCANNABLE!r}; saw {_cue_text(reader)!r}",
        )
        poll_until(
            reader,
            lambda: bool(_boxes(reader)),
            f"{SCANNABLE!r} was on screen with no hit boxes — it is not scannable",
        )


@pytest.mark.live
@pytest.mark.timeout(30)
def test_stepping_back_onto_a_cue_leaves_it_scannable() -> None:
    """The move a viewer makes *because* a cue was not scannable. It found the defect in the field
    and nothing automated covered it: the lookahead only ever read forward, so a backward step is
    the case with the coldest cache and the most to go wrong."""
    with live_reader(cues=BOUNDARY_CUES) as (_tmp, reader, _ipc):
        reader.graph.subtitle_navigation.navigate(1)
        poll_until(reader, lambda: _cue_text(reader) == SCANNABLE, "forward step never landed")
        reader.graph.subtitle_navigation.navigate(1)
        poll_until(reader, lambda: _cue_text(reader) != SCANNABLE, "second step never left the cue")

        reader.graph.subtitle_navigation.navigate(-1)

        poll_until(
            reader,
            lambda: _cue_text(reader) == SCANNABLE,
            f"backward step never returned to {SCANNABLE!r}",
        )
        poll_until(
            reader,
            lambda: bool(_boxes(reader)),
            f"{SCANNABLE!r} was not scannable on the second visit",
        )


@pytest.mark.live
@pytest.mark.timeout(30)
def test_no_box_survives_onto_a_cue_that_has_no_such_token() -> None:
    """The negative control, and the reason to trust the two above.

    Both of those assert boxes *appear*, which an implementation that simply never cleared them
    would also satisfy. This asserts the pairing instead: every box indexes a token the current cue
    actually has.

    Not "the next cue owns no box" -- an earlier draft asserted that, on the assumption that `♬～`
    tokenizes to nothing. It tokenizes to two tokens and is measured into two boxes; they are
    skippable for *lookup*, which is a different question. The premise was wrong and the live run
    said so on its first execution.

    This is the condition `TooltipController.hit` depends on: it indexes `tokens` by whatever box
    answers a click, so a box pointing past the list is a crash or a hit region on another cue's
    word -- the `tokens=0` beside `measured_boxes=3` the field trace caught.
    """
    with live_reader(cues=BOUNDARY_CUES) as (_tmp, reader, _ipc):
        reader.graph.subtitle_navigation.navigate(1)
        poll_until(reader, lambda: _cue_text(reader) == SCANNABLE, "forward step never landed")
        poll_until(reader, lambda: bool(_boxes(reader)), "the cue never became scannable")

        reader.graph.subtitle_navigation.navigate(1)  # onto the neighbour, boundaries touching

        poll_until(reader, lambda: _cue_text(reader) != SCANNABLE, "never left the scannable cue")
        poll_until(
            reader,
            lambda: _boxes_index_real_tokens(reader),
            "a box outlived its cue: it indexes a token the cue on screen does not have",
        )


def _boxes_index_real_tokens(reader) -> bool:
    cue = reader.graph.subtitle_presentation.cue.current
    return all(0 <= box.index < len(cue.tokens) for box in cue.boxes)
