"""Startup loading-spinner frame builder (drawn by the controller's poll loop)."""

from __future__ import annotations

from concurrent.futures import Future

import pytest
from util import runtime_gateway

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


def test_draw_loading_paints_one_timer_authorized_frame():
    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.overlay_ids import OverlayId

    r = Reader(FakeIPC())
    r._loading = True
    r._draw_loading()
    adds = [
        command for command in r.ipc.commands if command[:2] == ("overlay-add", OverlayId.LOADING)
    ]
    assert len(adds) == 1
    assert r._load_frame == 1


# --- the mpv-native startup breadcrumb (the only feedback during mpv's pre-overlay file-load) --------


def test_show_startup_hint_posts_mpv_osd_text():
    from util import FakeIPC

    from saitenka.app.loading import STARTUP_HINT, show_startup_hint

    ipc = FakeIPC()
    show_startup_hint(runtime_gateway(ipc))
    assert ("show-text", STARTUP_HINT, 30000) in ipc.commands


def test_show_startup_hint_skipped_for_screenshot():
    # A screenshot capture must not carry the breadcrumb, so it never touches mpv's OSD.
    from util import FakeIPC

    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    show_startup_hint(runtime_gateway(ipc), screenshot=True)
    assert ipc.commands == []


def test_ready_startup_hint_empties_the_osd_text():
    from util import FakeIPC

    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    lease = show_startup_hint(runtime_gateway(ipc))
    assert lease is not None
    ipc.drain_events()
    lease.mark_ready()
    assert ("show-text", "", 1) in ipc.commands


def test_subtitle_draw_cannot_clear_the_hint_before_interactive_readiness():
    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    r = Reader(ipc, startup_hint_lease=show_startup_hint(runtime_gateway(ipc)))
    ipc.drain_events()
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
    r = Reader(ipc, startup_hint_lease=show_startup_hint(runtime_gateway(ipc)))
    ipc.drain_events()
    r._observing = True
    r._playback = r._projection.seed(r._playback, "osd-dimensions", unavailable)

    r._mark_interactive_ready()
    assert ("show-text", "", 1) not in ipc.commands

    r._playback = r._projection.seed(r._playback, "osd-dimensions", {"w": 1920, "h": 1080})
    r._mark_interactive_ready()
    assert ipc.commands.count(("show-text", "", 1)) == 1


class _AsyncIPC:
    def __init__(self):
        self.commands: list[tuple] = []
        self.requests: list[IPCRequest] = []
        self.legacy_source = list
        self.connection_sink = None
        self.disconnected = False

    def command_async(self, *args, expected_connection_epoch=None):
        del expected_connection_epoch
        request = IPCRequest(len(self.requests), 0, Future())
        self.commands.append(args)
        self.requests.append(request)
        return request

    def install_runtime_ingress(self, _event_sink, connection_sink, legacy_event_source, _gateway):
        self.connection_sink = connection_sink
        self.legacy_source = legacy_event_source

    def drain_events(self):
        return self.legacy_source()


def test_late_show_acceptance_after_ready_clears_exactly_once():
    from saitenka.app.loading import HintOutcome, show_startup_hint

    ipc = _AsyncIPC()
    lease = show_startup_hint(runtime_gateway(ipc))
    assert lease is not None
    lease.mark_ready()
    assert lease.outcome is HintOutcome.PENDING
    assert ipc.commands == [("show-text", "saitenka starting...", 30000)]

    ipc.requests[0].future.set_result({"error": "success"})
    ipc.drain_events()
    lease.mark_ready()

    assert lease.outcome is HintOutcome.ACCEPTED
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_late_show_rejection_never_authorizes_clear():
    from saitenka.app.loading import HintOutcome, show_startup_hint

    ipc = _AsyncIPC()
    lease = show_startup_hint(runtime_gateway(ipc))
    assert lease is not None
    lease.mark_ready()
    ipc.requests[0].future.set_result({"error": "property unavailable"})
    ipc.drain_events()

    assert lease.outcome is HintOutcome.REJECTED
    assert ("show-text", "", 1) not in ipc.commands


def test_lost_show_reply_clears_only_after_a_live_reconnection():
    from saitenka.app.loading import HintOutcome, show_startup_hint

    ipc = _AsyncIPC()
    lease = show_startup_hint(runtime_gateway(ipc))
    assert lease is not None
    lease.mark_ready()
    ipc.requests[0].future.set_result({"error": "disconnected"})
    ipc.drain_events()

    assert lease.outcome is HintOutcome.UNKNOWN
    assert ("show-text", "", 1) not in ipc.commands

    lease.connection_replaced()
    lease.connection_replaced()
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_show_disconnect_delivered_after_reconnect_still_clears() -> None:
    from saitenka.app.loading import HintOutcome, show_startup_hint

    ipc = _AsyncIPC()
    gateway = runtime_gateway(ipc)
    lease = show_startup_hint(gateway)
    assert lease is not None
    lease.mark_ready()
    ipc.requests[0].future.set_result({"error": "disconnected"})
    assert ipc.connection_sink is not None
    ipc.connection_sink("replaced", 1)
    assert gateway._commit_replacement(1, ())
    lease.connection_replaced()

    ipc.drain_events()

    assert lease.outcome is HintOutcome.UNKNOWN
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_lost_clear_reply_is_retried_once_on_the_replacement_connection():
    from saitenka.app.loading import show_startup_hint

    ipc = _AsyncIPC()
    gateway = runtime_gateway(ipc)
    lease = show_startup_hint(gateway)
    assert lease is not None
    ipc.requests[0].future.set_result({"error": "success"})
    ipc.drain_events()
    lease.mark_ready()
    ipc.requests[1].future.set_result({"error": "disconnected"})
    ipc.drain_events()

    lease.connection_replaced()
    assert ipc.commands.count(("show-text", "", 1)) == 2

    ipc.requests[2].future.set_result({"error": "success"})
    ipc.drain_events()
    lease.connection_replaced()
    assert ipc.commands.count(("show-text", "", 1)) == 2


def test_clear_disconnect_delivered_after_reconnect_still_retries(monkeypatch) -> None:
    from saitenka.app import loading
    from saitenka.app.loading import show_startup_hint

    spans: list[dict[str, object]] = []

    class Span:
        def __init__(self, fields: dict[str, object]) -> None:
            self.fields = fields

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def set(self, _key, _value) -> None:
            return None

    def traced(name: str, **fields):
        record = {"name": name, **fields}
        spans.append(record)
        return Span(record)

    monkeypatch.setattr(loading.otel_metrics, "traced", traced)

    ipc = _AsyncIPC()
    gateway = runtime_gateway(ipc)
    lease = show_startup_hint(gateway)
    assert lease is not None
    ipc.requests[0].future.set_result({"error": "success"})
    ipc.drain_events()
    lease.mark_ready()
    ipc.requests[1].future.set_result({"error": "disconnected"})
    assert ipc.connection_sink is not None
    ipc.connection_sink("replaced", 1)
    assert gateway._commit_replacement(1, ())
    lease.connection_replaced()

    ipc.drain_events()

    assert ipc.commands.count(("show-text", "", 1)) == 2
    ipc.requests[2].future.set_result({"error": "success"})
    ipc.drain_events()
    clear_spans = [span for span in spans if span.get("operation") == "clear"]
    assert [span["connection_epoch"] for span in clear_spans] == ["0", "1"]


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
    r._loading = True
    r._apply_deps({})  # background load finished (even with nothing) → spinner off
    assert r._loading is False
    assert ("overlay-remove", OverlayId.LOADING) in r.ipc.commands


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
