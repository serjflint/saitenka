"""Startup loading-spinner frame builder (drawn by the controller's poll loop)."""

from __future__ import annotations

from concurrent.futures import Future

import pytest

from saitenka.app.loading import SPINNER, loading_image
from saitenka.mpvio.ipc import IPCRequest


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
    from util import FakeIPC

    from saitenka.app import reader_deps
    from saitenka.app.controller import Reader
    from saitenka.app.overlay_ids import OverlayId

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
    from util import FakeIPC

    from saitenka.app.loading import STARTUP_HINT, show_startup_hint

    ipc = FakeIPC()
    show_startup_hint(ipc)
    assert ("show-text", STARTUP_HINT, 30000) in ipc.commands


def test_show_startup_hint_skipped_for_screenshot():
    # A screenshot capture must not carry the breadcrumb, so it never touches mpv's OSD.
    from util import FakeIPC

    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    show_startup_hint(ipc, screenshot=True)
    assert ipc.commands == []


def test_clear_startup_hint_empties_the_osd_text():
    from util import FakeIPC

    from saitenka.app.loading import clear_startup_hint

    ipc = FakeIPC()
    clear_startup_hint(ipc)
    assert ("show-text", "", 1) in ipc.commands


def test_subtitle_draw_cannot_clear_the_hint_before_interactive_readiness():
    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    r = Reader(ipc, startup_hint_lease=show_startup_hint(ipc))
    r.ov = _RecOv()
    r.subtitle_language = "en"  # plain path → no dict/tokenize deps needed to raster a cue
    r.set_subtitle("hello")
    assert r._first_sub_logged
    assert ("show-text", "", 1) not in ipc.commands

    r._mark_interactive_ready()
    assert ipc.commands.count(("show-text", "", 1)) == 1


@pytest.mark.parametrize("unavailable", [None, {}])
def test_interactive_readiness_waits_for_operable_osd_dimensions(unavailable):
    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    r = Reader(ipc, startup_hint_lease=show_startup_hint(ipc))
    r._observing = True
    r._observed["osd-dimensions"] = unavailable

    r._mark_interactive_ready()
    assert ("show-text", "", 1) not in ipc.commands

    r._observed["osd-dimensions"] = {"w": 1920, "h": 1080}
    r._mark_interactive_ready()
    assert ipc.commands.count(("show-text", "", 1)) == 1


class _AsyncIPC:
    def __init__(self):
        self.commands: list[tuple] = []
        self.requests: list[IPCRequest] = []

    def command_async(self, *args):
        request = IPCRequest(len(self.requests), 0, Future())
        self.commands.append(args)
        self.requests.append(request)
        return request


def test_late_show_acceptance_after_ready_clears_exactly_once():
    from saitenka.app.loading import HintOutcome, show_startup_hint

    ipc = _AsyncIPC()
    lease = show_startup_hint(ipc)
    assert lease is not None
    lease.mark_ready()
    assert lease.outcome is HintOutcome.PENDING
    assert ipc.commands == [("show-text", "saitenka starting...", 30000)]

    ipc.requests[0].future.set_result({"error": "success"})
    lease.mark_ready()

    assert lease.outcome is HintOutcome.ACCEPTED
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_late_show_rejection_never_authorizes_clear():
    from saitenka.app.loading import HintOutcome, show_startup_hint

    ipc = _AsyncIPC()
    lease = show_startup_hint(ipc)
    assert lease is not None
    lease.mark_ready()
    ipc.requests[0].future.set_result({"error": "property unavailable"})

    assert lease.outcome is HintOutcome.REJECTED
    assert ("show-text", "", 1) not in ipc.commands


def test_lost_show_reply_clears_only_after_a_live_reconnection():
    from saitenka.app.loading import HintOutcome, show_startup_hint

    ipc = _AsyncIPC()
    lease = show_startup_hint(ipc)
    assert lease is not None
    lease.mark_ready()
    ipc.requests[0].future.set_result({"error": "disconnected"})

    assert lease.outcome is HintOutcome.UNKNOWN
    assert ("show-text", "", 1) not in ipc.commands

    lease.connection_replaced()
    lease.connection_replaced()
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_lost_clear_reply_is_retried_once_on_the_replacement_connection():
    from saitenka.app.loading import show_startup_hint

    ipc = _AsyncIPC()
    lease = show_startup_hint(ipc)
    assert lease is not None
    ipc.requests[0].future.set_result({"error": "success"})
    lease.mark_ready()
    ipc.requests[1].future.set_result({"error": "disconnected"})

    lease.connection_replaced()
    assert ipc.commands.count(("show-text", "", 1)) == 2

    ipc.requests[2].future.set_result({"error": "success"})
    lease.connection_replaced()
    assert ipc.commands.count(("show-text", "", 1)) == 2


def test_reader_reconnect_notifies_the_startup_hint_lease(monkeypatch):
    from util import FakeIPC

    from saitenka.app.controller import Reader

    class Lease:
        replaced = False

        def connection_replaced(self) -> None:
            self.replaced = True

    lease = Lease()
    reader = Reader(FakeIPC(), startup_hint_lease=lease)
    monkeypatch.setattr(reader, "start_observing", lambda **_kwargs: None)
    monkeypatch.setattr(reader, "_on_file_loaded", lambda: None)
    monkeypatch.setattr(reader.subtitle_pipeline, "connection_replaced", lambda _reader: None)

    reader._on_ipc_reconnect()

    assert lease.replaced is True


def test_apply_deps_stops_the_spinner():
    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.overlay_ids import OverlayId

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

    from util import FakeIPC

    from saitenka.app.controller import Reader

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

    from util import FakeIPC

    from saitenka.app import reader_deps as rd
    from saitenka.app.controller import Reader

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
