"""Startup loading-spinner frame builder (drawn by the controller's poll loop)."""

from __future__ import annotations

from overlay.app.loading import SPINNER, loading_image


def test_loading_image_renders_a_visible_frame():
    img = loading_image("loading dictionaries", 0)
    assert img.width > 30 and img.getextrema()[3][1] > 0  # visible (non-transparent) pixels


def test_frames_cycle_through_spinner_glyphs():
    a = loading_image("x", 0).tobytes()
    b = loading_image("x", 1).tobytes()
    assert a != b or len(SPINNER) == 1  # different frame → different glyph → different bitmap


# --- the controller lifecycle: the spinner actually shows while loading, and stops when deps land ---


class _RecOv:
    def __init__(self):
        self.shown: list = []
        self.hidden: list = []

    def show(self, img, x=0, y=0, oid=None):
        self.shown.append(oid)

    def hide(self, oid):
        self.hidden.append(oid)


def test_draw_loading_shows_spinner_then_throttles():
    from util import FakeIPC

    from overlay.app.controller import LOADING_ID, Reader

    r = Reader(FakeIPC())
    r.ov = _RecOv()
    r._loading = True
    r._load_next = 0.0  # allow an immediate first draw
    r._draw_loading()
    assert LOADING_ID in r.ov.shown  # spinner painted top-left
    assert r._load_frame == 1  # frame advanced
    shown_before = len(r.ov.shown)
    r._draw_loading()  # immediately again → throttled (now < _load_next), nothing new drawn
    assert len(r.ov.shown) == shown_before


def test_apply_deps_stops_the_spinner():
    from util import FakeIPC

    from overlay.app.controller import LOADING_ID, Reader

    r = Reader(FakeIPC())
    r.ov = _RecOv()
    r._loading = True
    r._apply_deps({})  # background load finished (even with nothing) → spinner off
    assert r._loading is False
    assert LOADING_ID in r.ov.hidden
