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

    def show(self, _img, *_a, oid=None, **_kw):
        self.shown.append(oid)

    def hide(self, oid):
        self.hidden.append(oid)


def test_draw_loading_shows_spinner_throttles_then_resumes(monkeypatch):
    from overlay.app import reader_deps
    from overlay.app.controller import Reader
    from overlay.app.overlay_ids import OverlayId
    from util import FakeIPC

    # Drive the clock explicitly across the 80 ms throttle window: on a loaded free-threaded (no-GIL)
    # CI runner >80 ms can pass between two Python statements, so a real clock made the throttle
    # assertion flaky (#73). Stepping a fixed clock proves all three legs deterministically.
    clock = [100.0]
    monkeypatch.setattr(reader_deps.time, "monotonic", lambda: clock[0])

    r = Reader(FakeIPC())
    r.ov = _RecOv()
    r._loading = True
    r._load_next = 0.0  # allow an immediate first draw

    r._draw_loading()  # leg 1: initial draw
    assert OverlayId.LOADING in r.ov.shown  # spinner painted top-left
    assert r._load_frame == 1  # frame advanced
    n1 = len(r.ov.shown)

    clock[0] += 0.079  # leg 2: still inside the 80 ms window → suppressed
    r._draw_loading()
    assert len(r.ov.shown) == n1 and r._load_frame == 1

    clock[0] += 0.001  # leg 3: reaches the 80 ms boundary → draw resumes
    r._draw_loading()
    assert len(r.ov.shown) == n1 + 1 and r._load_frame == 2


# --- the mpv-native startup breadcrumb (the only feedback during mpv's pre-overlay file-load) --------


def test_show_startup_hint_posts_mpv_osd_text():
    from overlay.app.loading import STARTUP_HINT, show_startup_hint
    from util import FakeIPC

    ipc = FakeIPC()
    show_startup_hint(ipc)
    assert ("show-text", STARTUP_HINT, 30000) in ipc.commands


def test_show_startup_hint_skipped_for_screenshot():
    # A screenshot capture must not carry the breadcrumb, so it never touches mpv's OSD.
    from overlay.app.loading import show_startup_hint
    from util import FakeIPC

    ipc = FakeIPC()
    show_startup_hint(ipc, screenshot=True)
    assert ipc.commands == []


def test_clear_startup_hint_empties_the_osd_text():
    from overlay.app.loading import clear_startup_hint
    from util import FakeIPC

    ipc = FakeIPC()
    clear_startup_hint(ipc)
    assert ("show-text", "", 1) in ipc.commands


def test_first_subtitle_draw_clears_the_startup_hint():
    # The overlay is live once the first cue draws → the breadcrumb must be cleared exactly then.
    from overlay.app.controller import Reader
    from util import FakeIPC

    r = Reader(FakeIPC())
    r.ov = _RecOv()
    r.subtitle_language = "en"  # plain path → no dict/tokenize deps needed to raster a cue
    assert not r._first_sub_logged
    r.set_subtitle("hello")  # first cue draws → hint cleared exactly here
    assert r._first_sub_logged
    assert ("show-text", "", 1) in r.ipc.commands
    r.set_subtitle("world")  # a second cue must NOT re-clear (one-shot)
    assert r.ipc.commands.count(("show-text", "", 1)) == 1


def test_apply_deps_stops_the_spinner():
    from overlay.app.controller import Reader
    from overlay.app.overlay_ids import OverlayId
    from util import FakeIPC

    r = Reader(FakeIPC())
    r.ov = _RecOv()
    r._loading = True
    r._apply_deps({})  # background load finished (even with nothing) → spinner off
    assert r._loading is False
    assert OverlayId.LOADING in r.ov.hidden


def test_load_deps_async_uses_a_custom_build():
    """#16: `run` passes its own CLI-flag-aware builder; load_deps_async must call THAT (not the
    config-only build_reader_deps) and publish its result for the poll loop to inject."""
    import time

    from overlay.app.controller import Reader
    from util import FakeIPC

    r = Reader(FakeIPC())
    r.ov = _RecOv()
    called = {"n": 0}

    def _build():
        called["n"] += 1
        return "SCORER", None, None, None

    r.load_deps_async({}, build=_build)
    assert r._loading is True  # spinner armed immediately (subs draw meanwhile)
    for _ in range(300):  # wait for the background thread to publish
        if r._pending_deps is not None:
            break
        time.sleep(0.01)
    assert called["n"] == 1
    assert r._pending_deps == {
        "scorer": "SCORER",
        "anki": None,
        "mine_cfg": None,
        "dict_set": None,
    }
    r._apply_deps(r._pending_deps)  # main-thread injection
    assert r.scorer == "SCORER" and r._loading is False


def test_load_deps_async_consumes_a_prebuilt_hoisted_future():
    """The run-mode hoist: begin_deps_build starts the build BEFORE mpv launches; load_deps_async then
    consumes that Future (it must NOT build a second time) and publishes the result for the poll loop."""
    import time

    from overlay.app import reader_deps as rd
    from overlay.app.controller import Reader
    from util import FakeIPC

    built = {"n": 0}

    def _build():
        built["n"] += 1
        return "SCORER", None, None, None

    fut = rd.begin_deps_build({}, _build)  # hoisted: runs before the reader exists
    r = Reader(FakeIPC())
    r.ov = _RecOv()
    r.load_deps_async({}, prebuilt=fut)  # consume the in-flight build, don't restart it
    for _ in range(300):
        if r._pending_deps is not None:
            break
        time.sleep(0.01)
    assert (
        built["n"] == 1
    )  # built exactly once — by begin_deps_build, not re-run by load_deps_async
    assert r._pending_deps == {"scorer": "SCORER", "anki": None, "mine_cfg": None, "dict_set": None}
