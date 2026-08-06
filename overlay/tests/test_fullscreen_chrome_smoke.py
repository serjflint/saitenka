"""Fullscreen / hi-dpi smoke for the chrome overlays (help · sidebar · stats).

On a 2x Retina fullscreen panel the OSD is ~3024x1898; ``Reader.chrome_scale`` grows the chrome with it
(the subtitle/tooltip already track ``osd_h``). This drives each chrome overlay's real draw path through
a capturing overlay and pins the two invariants the hi-dpi fix must hold — the overlay (1) stays fully
on-screen (scaling up must never overflow the OSD) and (2) actually grows versus 1080p (the regression
was that it did not). Real renderers, no subprocess/socket → default tier.
"""

from __future__ import annotations

import pytest

from overlay.app import analysis_overlay, help_overlay, sidebar
from overlay.app.config import PanelOptions, ReaderOptions
from overlay.app.controller import Reader
from overlay.app.sub_index import SubCue, SubIndex

BASELINE_1080 = (1920, 1080)
FULLSCREEN_HIDPI = (3024, 1898)  # 16" MacBook Pro Retina, fullscreen (hidpi_scale 2.0)


class FakeIPC:
    def __init__(self):
        self.props: dict = {}

    def command(self, *args):
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None, "error": "success"}


class FakeOverlay:
    """Captures the composited image handed to ``show`` so a test can read its uploaded geometry."""

    def __init__(self):
        self.shown: list = []

    def show(self, image, x=0, y=0, oid=0):
        self.shown.append((image, x, y, oid))

    def hide(self, oid=0):
        pass


def _reader(osd: tuple[int, int], *, ui_scale: float = 1.0) -> Reader:
    r = Reader(FakeIPC(), options=ReaderOptions(panels=PanelOptions(scale=ui_scale)))
    r.ov = FakeOverlay()
    r.osd = osd
    cues = [SubCue(float(i), float(i) + 0.8, f"cue {i}") for i in range(12)]
    r._sub_index = SubIndex(cues)
    r.sub_text = "cue 0"
    return r


def _draw_help(r: Reader) -> None:
    r._help_open = True
    help_overlay.redraw(r)


def _draw_sidebar(r: Reader) -> None:
    r.sidebar.open = True
    sidebar.redraw(r)


def _draw_stats(r: Reader) -> None:
    r.analysis.open = (
        True  # current=None → the "Analyzing…" status panel; enough to size the overlay
    )
    analysis_overlay.redraw(r)


CHROME = [("help", _draw_help), ("sidebar", _draw_sidebar), ("stats", _draw_stats)]


@pytest.mark.parametrize(("name", "draw"), CHROME)
def test_chrome_overlay_stays_on_screen_at_fullscreen_hidpi(name, draw):
    r = _reader(FULLSCREEN_HIDPI)
    draw(r)
    img, x, y, _oid = r.ov.shown[-1]  # the last (current) upload for this overlay
    w, h = img.size
    ow, oh = FULLSCREEN_HIDPI
    assert w > 0 and h > 0, f"{name} drew an empty image"
    assert w <= ow and h <= oh, f"{name} {w}x{h} exceeds osd {ow}x{oh}"
    assert x >= 0 and x + w <= ow, f"{name} runs off-screen horizontally: x={x} w={w} osd_w={ow}"
    assert y >= 0 and y + h <= oh, f"{name} runs off-screen vertically: y={y} h={h} osd_h={oh}"


@pytest.mark.parametrize(("name", "draw"), CHROME)
def test_chrome_overlay_grows_from_1080p_to_hidpi(name, draw):
    small = _reader(BASELINE_1080)
    draw(small)
    big = _reader(FULLSCREEN_HIDPI)
    draw(big)
    (sw, sh) = small.ov.shown[-1][0].size
    (bw, bh) = big.ov.shown[-1][0].size
    assert bw > sw or bh > sh, f"{name} did not scale up on the hi-dpi osd ({sw}x{sh} → {bw}x{bh})"


def test_hidpi_chrome_matches_a_manual_ui_scale_bump_at_1080p():
    """The fix makes the display factor do what users used to fake with a big ui_scale: help on the
    fullscreen hi-dpi osd at ui_scale 1.0 is about the size of help at 1080p with ui_scale bumped by the
    same osd_h/REF_H factor — i.e. ui_scale becomes a pure preference on top of OSD tracking."""
    hidpi = _reader(FULLSCREEN_HIDPI, ui_scale=1.0)
    _draw_help(hidpi)
    factor = FULLSCREEN_HIDPI[1] / 1080
    faked = _reader(BASELINE_1080, ui_scale=factor)  # old way: inflate ui_scale to compensate
    _draw_help(faked)
    hw = hidpi.ov.shown[-1][0].size[0]
    fw = faked.ov.shown[-1][0].size[0]
    assert abs(hw - fw) <= 2  # same font/layout scale → same panel width (± rounding)
