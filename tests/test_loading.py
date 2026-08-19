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
#
# The breadcrumb is a session-owned reducer (`app/startup_hint.py`), so these drive it the way the
# session does: publish the fact, let the consumer drain, assert on what mpv was told. Readiness is
# announced with a `StartupReady` event rather than a method call, and a reconnection is a real
# epoch replacement rather than a poke at the object — the old lease let a test claim a
# reconnection that never happened.


def _install(ipc):
    from saitenka.app.session_routes import install_session_reactor

    gateway = runtime_gateway(ipc)
    return gateway, install_session_reactor(gateway)


def _hint_state(reactor):
    from saitenka.app.startup_hint import StartupHintState

    state = reactor.snapshot.state.session
    assert isinstance(state, StartupHintState)
    return state


def _announce_ready(ipc, gateway) -> None:
    from saitenka.runtime import StartupReady

    assert gateway.publish_session_event(StartupReady())
    ipc.drain_events()


def _replace_connection(ipc, gateway, epoch: int) -> None:
    """Actually replace the connection, which is the only thing that resolves a lost ack."""
    sink = getattr(ipc, "connection_sink", None) or ipc._connection_sink
    sink("replaced", epoch)
    assert gateway._commit_replacement(epoch, ())
    ipc.drain_events()


def test_show_startup_hint_posts_mpv_osd_text():
    from util import FakeIPC

    from saitenka.app.loading import STARTUP_HINT

    ipc = FakeIPC()
    _install(ipc)
    assert ("show-text", STARTUP_HINT, 30000) in ipc.commands


def test_show_startup_hint_skipped_for_screenshot():
    # A screenshot capture must not carry the breadcrumb, so it never touches mpv's OSD -- but the
    # session still gets its reactor, which is not the hint's to withhold.
    from util import FakeIPC

    from saitenka.app.session_routes import install_session_reactor

    ipc = FakeIPC()
    reactor = install_session_reactor(runtime_gateway(ipc), startup_hint=False)
    assert ipc.commands == []
    assert reactor is not None


def test_ready_startup_hint_empties_the_osd_text():
    from util import FakeIPC

    ipc = FakeIPC()
    gateway, _reactor = _install(ipc)
    ipc.drain_events()
    _announce_ready(ipc, gateway)
    assert ("show-text", "", 1) in ipc.commands


def test_subtitle_draw_cannot_clear_the_hint_before_interactive_readiness():
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    _install(ipc)
    r = Reader(ipc)
    ipc.drain_events()
    r.ov = _RecOv()
    r.subtitle_language = "en"  # plain path -> no dict/tokenize deps needed to raster a cue
    r.set_subtitle("hello")
    assert r._first_sub_logged
    assert ("show-text", "", 1) not in ipc.commands

    r._mark_interactive_ready()
    ipc.drain_events()
    assert ipc.commands.count(("show-text", "", 1)) == 1


@pytest.mark.parametrize("unavailable", [None, {}])
def test_interactive_readiness_waits_for_operable_osd_dimensions(unavailable):
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    _install(ipc)
    r = Reader(ipc)
    ipc.drain_events()
    r._observing = True
    r._playback = r._projection.seed(r._playback, "osd-dimensions", unavailable)

    r._mark_interactive_ready()
    ipc.drain_events()
    assert ("show-text", "", 1) not in ipc.commands

    r._playback = r._projection.seed(r._playback, "osd-dimensions", {"w": 1920, "h": 1080})
    r._mark_interactive_ready()
    ipc.drain_events()
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

    def drain_events(self, *_args, **_kwargs):
        return self.legacy_source()


def test_late_show_acceptance_after_ready_clears_exactly_once():
    from saitenka.app.loading import HintOutcome

    ipc = _AsyncIPC()
    gateway, reactor = _install(ipc)
    _announce_ready(ipc, gateway)
    assert _hint_state(reactor).outcome is HintOutcome.PENDING
    assert ipc.commands == [("show-text", "saitenka starting...", 30000)]

    ipc.requests[0].future.set_result({"error": "success"})
    ipc.drain_events()
    _announce_ready(ipc, gateway)

    assert _hint_state(reactor).outcome is HintOutcome.ACCEPTED
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_late_show_rejection_never_authorizes_clear():
    from saitenka.app.loading import HintOutcome

    ipc = _AsyncIPC()
    gateway, reactor = _install(ipc)
    _announce_ready(ipc, gateway)
    ipc.requests[0].future.set_result({"error": "property unavailable"})
    ipc.drain_events()

    assert _hint_state(reactor).outcome is HintOutcome.REJECTED
    assert ("show-text", "", 1) not in ipc.commands


def test_lost_show_reply_clears_only_after_a_live_reconnection():
    from saitenka.app.loading import HintOutcome

    ipc = _AsyncIPC()
    gateway, reactor = _install(ipc)
    _announce_ready(ipc, gateway)
    ipc.requests[0].future.set_result({"error": "disconnected"})
    ipc.drain_events()

    assert _hint_state(reactor).outcome is HintOutcome.UNKNOWN
    assert ("show-text", "", 1) not in ipc.commands

    _replace_connection(ipc, gateway, 1)
    _replace_connection(ipc, gateway, 2)
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_show_disconnect_delivered_after_reconnect_still_clears() -> None:
    """The reply lands after the epoch already moved on, so the ambiguity is resolved on arrival."""
    from saitenka.app.loading import HintOutcome

    ipc = _AsyncIPC()
    gateway, reactor = _install(ipc)
    _announce_ready(ipc, gateway)
    ipc.requests[0].future.set_result({"error": "disconnected"})
    assert ipc.connection_sink is not None
    ipc.connection_sink("replaced", 1)
    assert gateway._commit_replacement(1, ())

    ipc.drain_events()

    assert _hint_state(reactor).outcome is HintOutcome.UNKNOWN
    assert ipc.commands.count(("show-text", "", 1)) == 1


def test_lost_clear_reply_is_retried_once_on_the_replacement_connection():
    ipc = _AsyncIPC()
    gateway, _reactor = _install(ipc)
    ipc.requests[0].future.set_result({"error": "success"})
    ipc.drain_events()
    _announce_ready(ipc, gateway)
    ipc.requests[1].future.set_result({"error": "disconnected"})
    ipc.drain_events()

    _replace_connection(ipc, gateway, 1)
    assert ipc.commands.count(("show-text", "", 1)) == 2

    ipc.requests[2].future.set_result({"error": "success"})
    ipc.drain_events()
    _replace_connection(ipc, gateway, 2)
    assert ipc.commands.count(("show-text", "", 1)) == 2


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
