"""`[tooltip] tip_scale` — the tooltip's reference→display factor as a fixed cosmetic preference.

`TipScale.display` is the single boundary value the whole crisp path keys off (osd_h / REF_H by
default). A positive `tip_scale` FIXES it regardless of playback resolution — the knob the user tuned
by eye. These assert the override wins over the auto value, and that 0 keeps the resolution-tracking
default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import util
from session_builder import build_session

from saitenka.app.config import ReaderOptions
from saitenka.app.features.tooltip.prefetch import REF_H
from saitenka.app.session.factory import SessionServices

if TYPE_CHECKING:
    from saitenka.app.session.turn import SessionTurn


def _reader(tip_scale: float, osd_h: int) -> SessionTurn:
    r = build_session(
        util.FakeIPC(),
        services=SessionServices(
            dictionaries=None,
        ),
        options=ReaderOptions().with_overrides(tip_scale=tip_scale),
    )
    r.turn.screen.osd = (round(osd_h * 16 / 9), osd_h)
    return r.turn


def test_auto_scale_tracks_the_video_viewport_when_tip_scale_is_zero():
    assert _reader(0.0, REF_H).tooltip_controller.scale().display == 1.0  # 1080p reference
    assert _reader(0.0, 2 * REF_H).tooltip_controller.scale().display == 2.0  # 4K → native 2×


def test_positive_tip_scale_overrides_the_resolution_derived_scale():
    # A fixed 1.5 wins at BOTH resolutions — the point of the knob is to detach from the OSD.
    assert (
        _reader(1.5, REF_H).tooltip_controller.scale().display == 1.5
    )  # larger than native on 1080p
    assert (
        _reader(1.5, 2 * REF_H).tooltip_controller.scale().display == 1.5
    )  # smaller than native on 4K


def test_raster_scale_snaps_osd_jitter_to_a_bucket():
    # The one-panel path rasters/composites/hit-tests at the BUCKETED scale, so an osd_h wobble of a few
    # px (scale jitter in the 3rd decimal) reuses cached native bands instead of re-rastering.
    assert _reader(0.0, 2161).tooltip_controller.scale().raster == 2.0  # 2.0009 → 2.00
    assert _reader(0.0, 2159).tooltip_controller.scale().raster == 2.0  # 1.9991 → 2.00
    assert _reader(0.0, 1621).tooltip_controller.scale().raster == 1.5  # 1.5009 → 1.50


def test_the_bucket_applies_to_a_hand_set_scale_too():
    """`raster` derives from `display` whichever way `display` was arrived at. A fixed `tip_scale`
    that misses a bucket would otherwise raster at a scale the hit-test never inverts at."""
    scale = _reader(1.53, REF_H).tooltip_controller.scale()

    assert scale.display == 1.53  # the preference is honoured verbatim…
    assert scale.raster == 1.55  # …and still snapped for the band cache


def test_the_viewport_cap_is_reference_based_not_live_osd():
    """Same reason `TipScale.display` is a boundary value: a cap measured off the live OSD would
    make the render cache resolution-dependent, so 4K and 1080p would never share a band."""
    from saitenka.app.features.tooltip.prefetch import cap_for

    assert cap_for(0.5) == cap_for(0.5)  # no session, no OSD — nothing else to vary
    assert cap_for(0.9) > cap_for(0.5)
    margin = max(16, round(REF_H * 0.05))
    assert cap_for(1.0) == REF_H - 2 * margin  # never taller than the reference clear of the margin


def test_a_nested_popup_shrinks_only_when_the_room_above_is_worth_using():
    """Below `NEST_MIN_ABOVE` the popup would be a slit, so it drops below the word instead."""
    from saitenka.app.features.tooltip.nested_popup import NEST_MIN_ABOVE, TIP_GAP, nested_view_h

    margin = max(16, round(REF_H * 0.05))
    roomy = NEST_MIN_ABOVE + TIP_GAP + margin + 40

    assert nested_view_h(800, roomy, osd_h=REF_H, max_frac=1.0) == roomy - TIP_GAP - margin
    cramped = NEST_MIN_ABOVE + TIP_GAP + margin - 1
    assert (
        nested_view_h(800, cramped, osd_h=REF_H, max_frac=1.0) == 800
    )  # full height, placed below


def test_the_nested_cap_bounds_the_viewport_before_the_room_above_does():
    from saitenka.app.features.tooltip.nested_popup import nested_view_h
    from saitenka.app.features.tooltip.prefetch import cap_for

    assert nested_view_h(10_000, 10_000, osd_h=REF_H, max_frac=0.5) == cap_for(0.5)


def test_a_panel_prefers_the_space_above_the_word():
    """Above by default: the tooltip covers the cue's own line if it drops below, and the word being
    read is the one thing that must stay visible."""
    from saitenka.app.features.tooltip.tooltip_panel import TIP_GAP, place_panel

    _tx, ty = place_panel(300, 100, 600, 40, 200, scale=1.0, osd=(1920, REF_H))

    assert ty + 200 <= 600 - TIP_GAP + 1  # sits above the word box


def test_a_panel_with_no_room_above_drops_below_the_word():
    from saitenka.app.features.tooltip.tooltip_panel import place_panel

    _tx, ty = place_panel(300, 100, 40, 40, 400, scale=1.0, osd=(1920, REF_H))

    assert ty >= 40


def test_a_panel_is_clamped_into_the_safe_area_on_every_side():
    """A word near a corner would anchor the panel off-screen, and mpv clips rather than scrolls —
    the part that ran off would simply never be readable."""
    from saitenka.app.features.tooltip.tooltip_panel import place_panel

    osd = (1920, REF_H)
    margin = max(16, round(REF_H * 0.05))

    for wx, wy in ((0, 0), (1919, REF_H - 1), (0, REF_H - 1), (1919, 0)):
        tx, ty = place_panel(300, wx, wy, 40, 200, scale=1.0, osd=osd)
        assert margin <= tx and tx + 300 <= osd[0] - margin + 1
        assert margin <= ty and ty + 200 <= osd[1] - margin + 1


def test_placement_uses_the_displayed_size_not_the_reference_size():
    """The panel is composited at reference size and upscaled at upload, so placing it by the
    reference size puts a 2x panel half off the bottom of a Retina panel."""
    from saitenka.app.features.tooltip.tooltip_panel import place_panel

    osd = (1920, REF_H)
    _tx, one = place_panel(300, 900, 900, 40, 300, scale=1.0, osd=osd)
    _tx, two = place_panel(300, 900, 900, 40, 300, scale=2.0, osd=osd)

    assert two < one  # the taller displayed panel is pushed further up to still fit
    assert two + 2 * 300 <= osd[1]


def test_a_panel_too_tall_for_either_side_takes_the_roomier_one():
    """A long entry near the bottom fits neither above nor below. Above still wins because there is
    more of it there, and the clamp then trims what is left — dropping it below would put the
    definition into the few pixels under the word.
    """
    from saitenka.app.features.tooltip.tooltip_panel import place_panel

    osd = (1920, REF_H)
    word_y, word_h, height = 560, 40, 520  # taller than the room above, but roomier there anyway

    _tx, ty = place_panel(300, 900, word_y, word_h, height, scale=1.0, osd=osd)

    # The panel ends at or above the word's own bottom edge, i.e. it went up. Asserting merely
    # `ty < word_y` would not discriminate: the safe-area clamp pulls the below-branch back far
    # enough to satisfy that too, which is why a taller panel cannot show this.
    assert ty + height <= word_y + word_h
