"""The runtime check on device 1's one claim: mpv's OSD renderer puts the colour where we said.

Which families the two renderers agree on is inferred from mpv's source. This is the channel that
can tell whether the inference is right on a real machine, and the epsilon it decides on is a
separator between the two measured classes — 0 px when they agree, 29 px when a face was substituted
— rather than a tolerance derived from either.
"""

from __future__ import annotations

import pytest

from saitenka.app import subtitle_calibration
from saitenka.app.subtitles import WordBox

PAYLOAD = (
    "{\\an7\\pos(100,600)\\fnArial\\fs48\\bord1.0\\shad0\\1c&HFFFFFF&}猫\n"
    "{\\an7\\pos(160,600)\\fnArial\\fs48\\bord1.0\\shad0\\1c&H0000FF&}を"
)


def test_the_signature_is_the_faces_and_the_surface_not_the_positions() -> None:
    """A cue that moved but kept its faces asks the same question, and `compute_bounds` stalls mpv's
    core — so it must not pay for a second answer. A resize does change it: mpv recomputes the box
    into the resolution it is handed."""
    moved = PAYLOAD.replace("\\pos(100,600)", "\\pos(300,200)")

    assert subtitle_calibration.payload_signature(PAYLOAD, (1920, 1080)) == (
        subtitle_calibration.payload_signature(moved, (1920, 1080))
    )
    assert subtitle_calibration.payload_signature(PAYLOAD, (1280, 720)) != (
        subtitle_calibration.payload_signature(PAYLOAD, (1920, 1080))
    )


def test_a_different_face_or_size_is_a_different_question() -> None:
    for changed in (
        ("\\fnArial\\fs48", "\\fnArial\\fs60"),
        ("\\fnArial\\fs48", "\\fnHelvetica\\fs48"),
    ):
        assert subtitle_calibration.payload_signature(
            PAYLOAD.replace(*changed), (1920, 1080)
        ) != subtitle_calibration.payload_signature(PAYLOAD, (1920, 1080))  # fmt: skip


def test_a_payload_with_no_text_asks_nothing() -> None:
    """The focus highlight alone is a vector drawing with no face in it — nothing to calibrate, and
    a call that stalled mpv to measure a rectangle we drew ourselves would be pure cost."""
    assert subtitle_calibration.payload_signature(
        "{\\p1}m 0 0 l 10 0 10 10 0 10{\\p0}", (0, 0)
    ) is (None)


def test_only_the_tokens_the_text_device_drew_are_compared() -> None:
    """A token the text device stood down on is not in the payload. Including its rect in our union
    would report a difference in what was ASKED for as a difference in how it was laid out."""
    boxes = [
        WordBox(0, 100, 600, 50, 40, "Arial", 48.0),
        WordBox(1, 160, 600, 50, 40, "Arial", 48.0),
        WordBox(2, 900, 600, 50, 40, "", 0.0),  # attachment-only: the raster device has this one
    ]

    assert subtitle_calibration.measured_bounds(boxes) == (100, 600, 210, 640)


def test_a_cue_the_text_device_did_not_draw_has_nothing_to_compare() -> None:
    assert subtitle_calibration.measured_bounds([WordBox(0, 0, 0, 5, 5)]) is None
    assert subtitle_calibration.measured_bounds([]) is None


def test_our_own_border_is_taken_back_out_before_the_comparison() -> None:
    """`mp_ass_get_bb` unions the outline images too, and the payload asks for a hairline border our
    measuring render never drew. Leaving it in would report that fixed inflation as drift on every
    cue, and the number would look like a real disagreement."""
    measured = (100, 600, 210, 640)
    reported = {"x0": 99.0, "y0": 599.0, "x1": 211.0, "y1": 641.0}

    drift = subtitle_calibration.drift_of(measured, reported, border=1.0)

    assert drift is not None
    assert (drift.left, drift.top, drift.right, drift.bottom) == (0.0, 0.0, 0.0, 0.0)
    assert drift.worst == 0.0


def test_drift_is_reported_per_edge_and_summarised_by_the_worst() -> None:
    """Per edge because the two failures look different: a substituted face is wider (the right edge
    moves), a different size moves top and bottom too."""
    drift = subtitle_calibration.drift_of(
        (100, 600, 210, 640), {"x0": 101.0, "y0": 601.0, "x1": 240.0, "y1": 641.0}, border=1.0
    )

    assert drift is not None
    assert drift.right == 29.0  # the -29px case the probe measured, from the other side
    assert drift.worst == 29.0


@pytest.mark.parametrize(
    "reported", [{}, {"x0": 1, "y0": 2, "x1": 3}, {"x0": "n/a", "y0": 0, "x1": 0, "y1": 0}]
)
def test_a_reply_that_is_not_a_box_is_not_a_drift_of_zero(reported: dict) -> None:
    """Zero would read as "measured and agreed", which is the one thing an unanswered probe must
    never claim."""
    assert subtitle_calibration.drift_of((0, 0, 1, 1), reported, border=0.0) is None


def test_the_verdict_does_not_turn_on_the_exact_epsilon() -> None:
    """The argument for acting on a two-point sample: both measured classes are classified the same
    way by any boundary strictly inside the gap between them. A regression that moved the epsilon
    onto either class would change a verdict, and this is what would catch it."""
    agreeing = subtitle_calibration.Drift(0.0, 0.0, 0.0, 0.0)
    substituted = subtitle_calibration.Drift(0.0, 0.0, 29.0, 0.0)

    assert 0.0 < subtitle_calibration.DRIFT_EPSILON_PX < 29.0
    assert agreeing.agrees is True
    assert substituted.agrees is False


def test_every_family_in_the_payload_is_named_by_the_verdict() -> None:
    """`compute_bounds` answers with one box for the whole payload, so it cannot say which family
    drifted. Naming all of them is the conservative reading, and conservative demotes to devices
    that draw the token correctly."""
    mixed = PAYLOAD.replace("\\fnArial\\fs48\\bord1.0\\shad0\\1c&H0000FF&", "\\fn@MS Gothic\\fs48")

    assert subtitle_calibration.payload_families(mixed) == {"arial", "ms gothic"}


def test_a_payload_with_no_faces_names_no_families() -> None:
    """Device 3's rules carry no `\\fn`, so a cue drawn entirely by them has nothing to demote — and
    demoting nothing must not read as demoting everything."""
    assert subtitle_calibration.payload_families(r"{\an7\pos(1,2)\1c&HFF&\p1}m 0 0 l 4 0") == set()


def test_a_family_already_demoted_is_not_in_our_side_of_the_next_comparison() -> None:
    """After a verdict the renderer stops drawing that family, so its rect leaves the payload. Left
    in our union it would report the demotion itself as a fresh drift, forever."""
    boxes = [
        WordBox(0, 100, 600, 50, 40, "Arial", 48.0),
        WordBox(1, 900, 600, 50, 40, "MS Gothic", 48.0),
    ]

    assert subtitle_calibration.measured_bounds(boxes, drifting=frozenset({"ms gothic"})) == (
        100,
        600,
        150,
        640,
    )
