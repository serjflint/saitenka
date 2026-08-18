"""Chrome (help / sidebar / stats) tracks the OSD on a hi-dpi / large surface.

The regression: on a 2x Retina fullscreen panel the OSD is ~3024x1898, the subtitle and tooltip scale
with ``osd_h/REF_H`` (so they grow), but the chrome overlays were sized by a flat ``ui_scale`` and stayed
tiny beside them. ``Reader.chrome_scale`` folds the same display factor into ``ui_scale``, clamped so it
only ever grows (an OSD at/under 1080p is unchanged — the goldens stay valid).
"""

from __future__ import annotations

from saitenka.app import help_overlay
from saitenka.app.config import PanelOptions, ReaderOptions
from saitenka.app.controller import Reader

REF_H = 1080


class FakeIPC:
    def command(self, *_args):
        return {"data": None, "error": "success"}

    def drain_events(self, *_args, **_kwargs):
        return []


def _reader(scale: float) -> Reader:
    return Reader(FakeIPC(), options=ReaderOptions(panels=PanelOptions(scale=scale)))


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
    base = help_overlay.document_for(r)
    r.osd = (3024, 1898)
    hidpi = help_overlay.document_for(r)
    assert hidpi.width > base.width and hidpi.height > base.height
