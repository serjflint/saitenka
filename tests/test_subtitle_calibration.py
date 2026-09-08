"""The runtime check on device 1's one claim: mpv's OSD renderer puts the color where we said.

Which families the two renderers agree on is inferred from mpv's source. This is the channel that
can tell whether the inference is right on a real machine, and the epsilon it decides on is a
separator between the two measured classes — 0 px when they agree, 29 px when a face was substituted
— rather than a tolerance derived from either.
"""

from __future__ import annotations

from dataclasses import replace

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


def test_weight_and_slant_make_it_a_different_question() -> None:
    r"""`\b1` selects a different font FILE, so a payload that differs only by it is asking whether
    a face the first payload never used lays out the same — which is the substitution this module
    exists to catch. Keyed on face and size alone, the cached "agrees" from the regular run
    suppressed the check on the bold one entirely."""
    plain = PAYLOAD.replace("\\fs48", "\\fs48")
    styled = PAYLOAD.replace("\\fs48", "\\fs48\\fscx50\\b1\\i1")

    assert subtitle_calibration.payload_signature(
        styled, (1920, 1080)
    ) != subtitle_calibration.payload_signature(plain, (1920, 1080))  # fmt: skip
    # The families still resolve out of the widened match, or every verdict would name nothing.
    assert subtitle_calibration.payload_families(styled) == {"arial"}


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
    """Per edge because the two failures look different: a substituted face lays the run out to a
    different width, a different size moves top and bottom too."""
    drift = subtitle_calibration.drift_of(
        (100, 600, 210, 640), {"x0": 101.0, "y0": 601.0, "x1": 182.0, "y1": 641.0}, border=1.0
    )

    assert drift is not None
    assert (
        drift.right == -29.0
    )  # the box came back narrower than our ink by a substituted face's run
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
    substituted = subtitle_calibration.Drift(0.0, 0.0, -29.0, 0.0)

    assert 0.0 < subtitle_calibration.DRIFT_EPSILON_PX < 29.0
    assert agreeing.agrees is True
    assert substituted.agrees is False


@pytest.mark.parametrize("sign", [1.0, -1.0])
@pytest.mark.parametrize("edge", ["right", "bottom"])
def test_a_substituted_face_demotes_whichever_way_it_moved_the_far_edge(
    edge: str, sign: float
) -> None:
    r"""The 29 px class must demote on a padded edge in BOTH directions.

    Its sign is unsettled — the commit that introduced the class wrote −29 in prose and +29 in the
    two tests it added (`0e13cca4`) — so the verdict is built not to need the answer. This is the
    regression guard for having assumed one: with the padding allowance at a full 32 px, a +29
    scored 0 and every positive drift up to 36 px read as agreement, which switched the rule off
    for the exact case it exists to catch while every gate stayed green.
    """
    drift = replace(subtitle_calibration.Drift(0.0, 0.0, 0.0, 0.0), **{edge: 29.0 * sign})

    assert drift.agrees is False


def test_the_accept_band_on_a_padded_edge_covers_the_measured_padding_and_stays_clear_of_the_signal() -> (
    None
):
    """What a padded edge accepts is `TILE_PADDING_PX + DRIFT_EPSILON_PX`, and it is squeezed from
    both sides — so pin both, not just one.

    Below the measured overshoot, cues the two renderers agree on demote for allocation they cannot
    help. Near the substituted-face class, the discount eats the signal the module exists to catch.
    A one-sided `band < 29` is not enough: it passes at 24 + 4 = 28, one pixel from that class, and
    the whole suite stayed green there.
    """
    band = subtitle_calibration.TILE_PADDING_PX + subtitle_calibration.DRIFT_EPSILON_PX

    assert band >= subtitle_calibration.MEASURED_PADDING_CEILING_PX
    assert subtitle_calibration.SUBSTITUTED_FACE_DRIFT_PX - band >= 7.0


def test_the_two_readings_a_real_session_produced_land_on_opposite_verdicts() -> None:
    """The oracle's calibration against the field, from one episode measured twice.

    With the overprint misanchored the origin edges read +13/+20; with it fixed they read exactly
    0/0 while the right and bottom still read +10/+15 — the tile padding, which is present in both.
    A rule that cannot separate these two is either blind to a real defect or vetoes the fix for it,
    and this repo has now shipped both mistakes.
    """
    misanchored = subtitle_calibration.Drift(13.0, 20.0, -41.0, 34.0)
    anchored = subtitle_calibration.Drift(0.0, 0.0, 10.0, 15.0)

    assert misanchored.agrees is False
    assert anchored.agrees is True


def test_padding_is_only_ever_added_to_the_right_and_bottom() -> None:
    """`mp_ass_get_bb` unions bitmap rectangles, and libass rounds each one out to a tile on those
    two edges alone. So the same magnitude is a layout difference on an origin edge and an
    allocation detail on a far one, and the verdict has to read them differently."""
    on_origin = subtitle_calibration.Drift(15.0, 0.0, 0.0, 0.0)
    on_far_edge = subtitle_calibration.Drift(0.0, 0.0, 15.0, 0.0)

    assert on_origin.agrees is False
    assert on_far_edge.agrees is True


def test_a_far_edge_still_bites_when_it_cannot_be_padding() -> None:
    """The negative control for the allowance: padding only ever grows the box, and never by more
    than one tile. Both directions outside that stay disagreements, or the allowance would be a
    blanket amnesty on half the box."""
    beyond_a_tile = subtitle_calibration.Drift(
        0.0, 0.0, subtitle_calibration.TILE_PADDING_PX + 9, 0
    )
    shrunk = subtitle_calibration.Drift(0.0, 0.0, 0.0, -9.0)

    assert beyond_a_tile.agrees is False
    assert shrunk.agrees is False


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
