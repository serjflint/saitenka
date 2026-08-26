"""Fullscreen / hi-dpi smoke for the chrome overlays (help · sidebar · stats).

On a 2x Retina fullscreen panel the OSD is ~3024x1898; ``SessionController.chrome_scale`` grows the chrome with it
(the subtitle/tooltip already track ``osd_h``). This drives each chrome overlay's real draw path through
a capturing overlay and pins the two invariants the hi-dpi fix must hold — the overlay (1) stays fully
on-screen (scaling up must never overflow the OSD) and (2) actually grows versus 1080p (the regression
was that it did not). Real renderers, no subprocess/socket → default tier.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import util

from saitenka.app.bindings import ANALYSIS_MSG
from saitenka.app.config import PanelOptions, ReaderOptions
from saitenka.app.features.sidebar import sidebar
from saitenka.app.session.assembly import build_session_assembly
from saitenka.app.session.controller import SessionController
from saitenka.runtime import events
from saitenka.runtime.help import HelpCommand
from saitenka.subtitles import Cue, CueIndex

BASELINE_1080 = (1920, 1080)
FULLSCREEN_HIDPI = (3024, 1898)  # 16" MacBook Pro Retina, fullscreen (hidpi_scale 2.0)


class FakeIPC(util.FakeIPC):
    pass


class FakeOverlay:
    """Captures the composited image and its placement so a test can read the uploaded geometry.

    Records at ``prepare``, which is where chrome presentation actually crosses into the overlay
    now that it goes through the fenced surface path. A fake that only implements ``show`` would
    quietly record nothing and every size assertion below would read an empty list.
    """

    def __init__(self):
        self.shown: list = []
        self.lifecycle_oids: set[int] = set()

    def prepare(self, image, x=0, y=0, *, oid=0, revision=0):  # noqa: ARG002
        self.shown.append((image, x, y, oid))
        self.lifecycle_oids.add(oid)
        return SimpleNamespace(oid=oid, path=None, tail=(), command=("overlay-add", oid))

    def commit_prepared(self, prepared):
        pass

    def discard_prepared(self, prepared):
        pass

    def physical_oid(self, oid):
        return oid

    def commit_remove(self, oid):
        self.lifecycle_oids.discard(oid)

    def remove_lifecycle_now(self, oid):  # noqa: ARG002
        return {"error": "success"}

    def submit_surface_transaction(self, *, owner, identity, command, on_finished):  # noqa: ARG002
        from saitenka.runtime import EffectFinished, EffectId, EffectOutcome

        on_finished(EffectFinished(EffectId(0), owner, identity, EffectOutcome.SUCCEEDED))

    def show(self, image, x=0, y=0, oid=0):
        self.shown.append((image, x, y, oid))

    def hide(self, oid=0):
        pass


def _reader(osd: tuple[int, int], *, ui_scale: float = 1.0) -> SessionController:
    ipc = FakeIPC()
    options = ReaderOptions(panels=PanelOptions(scale=ui_scale))
    overlay = FakeOverlay()
    assembly = build_session_assembly(ipc, options, runtime_submit=None, overlay=overlay)
    r = SessionController(ipc, options=options, assembly=assembly)
    r.osd = osd
    cues = [Cue(float(i), float(i) + 0.8, f"cue {i}") for i in range(12)]
    r.episode.sub_index = CueIndex(cues)
    r.sub_text = "cue 0"
    return r


def _draw_help(r: SessionController) -> None:
    r.help_controller.store.dispatch(HelpCommand.TOGGLE)
    r.help_controller.redraw()


def _draw_sidebar(r: SessionController) -> None:
    r.sidebar_controller.store.dispatch(
        events.SidebarShown(r.sidebar_view.active, r.sidebar_view.capacity)
    )
    sidebar.draw(r.sidebar_view)


def _draw_stats(r: SessionController) -> None:
    r._handle(ANALYSIS_MSG)


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


@pytest.mark.parametrize(("name", "draw"), CHROME)
def test_chrome_left_open_is_swept_by_close(name, draw):
    """What the fenced path buys beyond ordering. `Overlay.prepare` registers each oid it stages,
    so a panel still up at teardown is now removed by the close sweep — a direct `ov.show` left it
    on a detached mpv's screen, since nothing knew it was there."""
    r = _reader(FULLSCREEN_HIDPI)
    draw(r)
    assert r.ov.lifecycle_oids, f"{name} staged nothing the close sweep could find"

    r.lifecycle_surfaces.close()

    assert not r.ov.lifecycle_oids
