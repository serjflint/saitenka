"""Stage 9–12: the panel composited over a video frame (deterministic screenshot test).

Stands in for the live mpv window in CI: mpv draws the ``overlay-add`` bitmap over the video in one
surface, which is exactly ``composite(frame, panel)``. The live-window proof is
``examples/mpv_overlay.py`` (its output is saved under tests/artifacts/).
"""

from pathlib import Path

import numpy as np

from overlay.mpvio.compositor import composite, make_frame
from overlay.panel import load_entry, render_panel
from util import assert_golden

FIX = Path(__file__).resolve().parent / "fixtures"


def test_compositor_golden():
    panel = render_panel(load_entry(FIX / "yomu.json"), width=384, max_height=660)
    frame = make_frame(1280, 720, subtitle="門前の小僧習わぬ経を読む")
    out = composite(frame, panel, 40, 24).convert("RGB")
    assert out.size == (1280, 720)
    assert_golden(out, "mpv_composite.png", tol=2.5)


def test_overlay_independent_of_video_mode():
    """Airspace proof (Python stand-in): the panel is composited identically regardless of the
    video resolution/mode — i.e. going fullscreen can't hide or move it (it's in mpv's surface)."""
    panel = render_panel(load_entry(FIX / "yomu.json"), width=384, max_height=400)
    ref = np.asarray(panel.convert("RGBA"), np.int16)
    for w, h in [(1280, 720), (1920, 1080)]:
        out = composite(make_frame(w, h), panel, 40, 24)
        crop = out.crop((40, 24, 40 + panel.width, 24 + panel.height)).convert("RGBA")
        assert np.abs(np.asarray(crop, np.int16) - ref).mean() < 0.5
