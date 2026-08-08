"""`[tooltip] tip_scale` — the tooltip's reference→display factor as a fixed cosmetic preference.

`_tip_display_scale` is the single boundary value the whole crisp path keys off (osd_h / REF_H by
default). A positive `tip_scale` FIXES it regardless of playback resolution — the knob the user tuned
by eye. These assert the override wins over the auto value, and that 0 keeps the resolution-tracking
default.
"""

from __future__ import annotations

import util
from overlay.app.controller import Reader
from overlay.app.prefetch import REF_H


def _reader(tip_scale: float, osd_h: int) -> Reader:
    r = Reader(util.FakeIPC(), dict_set=None, tip_scale=tip_scale)
    r.osd = (round(osd_h * 16 / 9), osd_h)
    return r


def test_auto_scale_tracks_the_video_viewport_when_tip_scale_is_zero():
    assert _reader(0.0, REF_H)._tip_display_scale == 1.0  # 1080p reference
    assert _reader(0.0, 2 * REF_H)._tip_display_scale == 2.0  # 4K → native 2×


def test_positive_tip_scale_overrides_the_resolution_derived_scale():
    # A fixed 1.5 wins at BOTH resolutions — the point of the knob is to detach from the OSD.
    assert _reader(1.5, REF_H)._tip_display_scale == 1.5  # larger than native on 1080p
    assert _reader(1.5, 2 * REF_H)._tip_display_scale == 1.5  # smaller than native on 4K


def test_raster_scale_snaps_osd_jitter_to_a_bucket():
    # The one-panel path rasters/composites/hit-tests at the BUCKETED scale, so an osd_h wobble of a few
    # px (scale jitter in the 3rd decimal) reuses cached native bands instead of re-rastering.
    assert _reader(0.0, 2161)._raster_scale == 2.0  # 2.0009 → 2.00
    assert _reader(0.0, 2159)._raster_scale == 2.0  # 1.9991 → 2.00
    assert _reader(0.0, 1621)._raster_scale == 1.5  # 1.5009 → 1.50
