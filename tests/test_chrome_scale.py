"""Chrome (help / sidebar / stats) tracks the OSD on a hi-dpi / large surface.

The regression: on a 2x Retina fullscreen panel the OSD is ~3024x1898, the subtitle and tooltip scale
with ``osd_h/REF_H`` (so they grow), but the chrome overlays were sized by a flat ``ui_scale`` and stayed
tiny beside them. ``SessionController.chrome_scale`` folds the same display factor into ``ui_scale``, clamped so it
only ever grows (an OSD at/under 1080p is unchanged — the goldens stay valid).
"""

from __future__ import annotations

import util

from saitenka.app.config import PanelOptions, ReaderOptions
from saitenka.app.session_controller import SessionController
from saitenka.runtime import events

REF_H = 1080


class FakeIPC(util.FakeIPC):
    pass


def _reader(scale: float) -> SessionController:
    return SessionController(FakeIPC(), options=ReaderOptions(panels=PanelOptions(scale=scale)))


def test_chrome_scale_is_flat_ui_scale_at_or_below_1080p():
    r = _reader(1.2)
    r.osd = (1920, 1080)
    assert r.chrome_scale == 1.2  # factor 1.0 at REF_H → unchanged (goldens pinned here stay valid)
    r.osd = (1280, 720)
    assert r.chrome_scale == 1.2  # clamped: never shrinks the chrome below the ui_scale baseline


def test_chrome_scale_grows_with_a_hi_dpi_osd():
    r = _reader(1.2)
    r.osd = (3024, 1898)  # 2x Retina fullscreen — the reported setup
    assert r.chrome_scale == 1.2 * (1898 / REF_H)  # tracks osd_h like the tooltip/subtitle
    assert r.chrome_scale > 2.0  # visibly larger than the flat 1.2 that drew it small


def test_help_panel_is_larger_on_a_hi_dpi_osd_than_at_1080p():
    """The end-to-end oracle for the fix: the same help overlay uploads bigger pixels on the Retina OSD
    than at 1080p, instead of staying at the flat-ui_scale size that read as 'small'."""
    r = _reader(1.2)
    r.osd = (1920, 1080)
    base = r.help_controller.document()
    r.osd = (3024, 1898)
    hidpi = r.help_controller.document()
    assert hidpi.width > base.width and hidpi.height > base.height


def test_subtitle_picker_is_larger_on_a_hi_dpi_osd_than_at_1080p():
    r = _reader(1.0)
    r.picker_controller.store.dispatch(events.PickerOpened())
    r.osd = (1920, 1080)
    r.picker_controller.redraw()
    assert r.picker_controller.panel.rect is not None
    base_width = r.picker_controller.panel.rect[2]

    r.osd = (3840, 2160)
    r.picker_controller.redraw()
    assert r.picker_controller.panel.rect is not None
    assert r.picker_controller.panel.rect[2] == base_width * 2
