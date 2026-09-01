"""Startup loading-spinner frame builder (drawn by the controller's poll loop)."""

from __future__ import annotations

from concurrent.futures import Future

import pytest
from util import await_ready, bare_gateway

from saitenka.app.loading import SPINNER, loading_image
from saitenka.mpvio.ipc import IPCRequest
from saitenka.runtime.events import SubtitleLanguageChanged


def test_loading_image_renders_a_visible_frame():
    img = loading_image("loading dictionaries", 0)
    assert img.width > 30 and img.getextrema()[3][1] > 0  # visible (non-transparent) pixels


def test_frames_cycle_through_spinner_glyphs():
    a = loading_image("x", 0).tobytes()
    b = loading_image("x", 1).tobytes()
    assert a != b or len(SPINNER) == 1  # different frame → different glyph → different bitmap


# --- the controller lifecycle: the spinner actually shows while loading, and stops when deps land ---


def test_draw_loading_paints_one_timer_authorized_frame(make_session):
    from util import FakeIPC

    from saitenka.app.overlay_ids import OverlayId

    r = make_session(FakeIPC())
    r.graph.profile.begin_loading()
    assert r.graph.ipc.fire_runtime_timer("lifecycle:loading-frame")
    adds = [
        command
        for command in r.graph.ipc.commands
        if command[:2] == ("overlay-add", OverlayId.LOADING)
    ]
    assert len(adds) == 1


# --- the mpv-native startup breadcrumb (the only feedback during mpv's pre-overlay file-load) --------
#
# The breadcrumb is a session-owned reducer (`runtime/startup_hint.py`), so these drive it the way the
# session does: publish the fact, let the consumer drain, assert on what mpv was told. Readiness is
# announced with a `StartupReady` event rather than a method call, and a reconnection is a real
# epoch replacement rather than a poke at the object — the old lease let a test claim a
# reconnection that never happened.


def _install(ipc):
    from saitenka.app.session.routes import install_session_reactor

    gateway = bare_gateway(ipc)
    return gateway, install_session_reactor(gateway)


def _hint_state(reactor):
    from saitenka.app.session.routes import STARTUP_HINT
    from saitenka.runtime.startup_hint import StartupHintState
    from saitenka.runtime.state import OwnerSlice

    slot = reactor.snapshot.state.session
    assert isinstance(slot, OwnerSlice)  # the owner slot is a slice, not one feature's state
    state = slot.get(STARTUP_HINT)
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

    from saitenka.runtime.startup_hint import STARTUP_HINT

    ipc = FakeIPC()
    _install(ipc)
    assert ("show-text", STARTUP_HINT, 30000) in ipc.commands


def test_show_startup_hint_skipped_for_screenshot(request):
    # A screenshot capture must not carry the breadcrumb, so it never touches mpv's OSD -- but the
    # session still gets its reactor, which is not the hint's to withhold.
    from util import FakeIPC

    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    reactor = install_session_reactor(gateway, startup_hint=False)
    assert ipc.commands == []
    assert reactor is not None


def test_ready_startup_hint_empties_the_osd_text():
    from util import FakeIPC

    ipc = FakeIPC()
    gateway, _reactor = _install(ipc)
    ipc.drain_events()
    _announce_ready(ipc, gateway)
    assert ("show-text", "", 1) in ipc.commands


def test_subtitle_draw_cannot_clear_the_hint_before_interactive_readiness(make_session):
    from util import FakeIPC

    ipc = FakeIPC()
    _install(ipc)
    r = make_session(ipc)
    ipc.drain_events()
    # plain path -> no dict/tokenize deps needed to raster a cue
    r.graph.track_commands.declare(SubtitleLanguageChanged("en"))
    r.graph.cue.set_subtitle("hello")
    assert r.graph.subtitle_presentation.renderer.logged_first
    assert ("show-text", "", 1) not in ipc.commands

    r.pump()
    assert ipc.commands.count(("show-text", "", 1)) == 1


@pytest.mark.parametrize("unavailable", [None, {}])
def test_interactive_readiness_waits_for_operable_osd_dimensions(unavailable, make_session):
    from util import FakeIPC

    ipc = FakeIPC()
    _install(ipc)
    r = make_session(ipc)
    ipc.drain_events()
    r.graph.playback.install_seed({"osd-dimensions": unavailable})

    r.pump()
    assert ("show-text", "", 1) not in ipc.commands

    r.graph.playback.install_seed({"osd-dimensions": {"w": 1920, "h": 1080}})
    r.pump()
    assert ipc.commands.count(("show-text", "", 1)) == 1


class _AsyncIPC:
    def __init__(self):
        self.commands: list[tuple] = []
        self.requests: list[IPCRequest] = []
        self.session_loop = None
        self.connection_sink = None
        self.disconnected = False

    def command_async(self, *args, expected_connection_epoch=None):
        del expected_connection_epoch
        request = IPCRequest(len(self.requests), 0, Future())
        self.commands.append(args)
        self.requests.append(request)
        return request

    def install_runtime_ingress(self, _event_sink, connection_sink, session_loop, _gateway):
        self.connection_sink = connection_sink
        self.session_loop = session_loop

    def drain_events(self, *_args, **_kwargs):
        if self.session_loop is None:
            return []
        events: list = []
        self.session_loop.receive(0.0, events.append)
        return events


def test_late_show_acceptance_after_ready_clears_exactly_once():
    from saitenka.runtime.startup_hint import HintOutcome

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
    from saitenka.runtime.startup_hint import HintOutcome

    ipc = _AsyncIPC()
    gateway, reactor = _install(ipc)
    _announce_ready(ipc, gateway)
    ipc.requests[0].future.set_result({"error": "property unavailable"})
    ipc.drain_events()

    assert _hint_state(reactor).outcome is HintOutcome.REJECTED
    assert ("show-text", "", 1) not in ipc.commands


def test_lost_show_reply_clears_only_after_a_live_reconnection():
    from saitenka.runtime.startup_hint import HintOutcome

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
    from saitenka.runtime.startup_hint import HintOutcome

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


def test_apply_deps_stops_the_spinner(make_session):
    from util import FakeIPC

    from saitenka.app.features.profiles.dependencies import DependencyBundle
    from saitenka.app.overlay_ids import OverlayId

    r = make_session(FakeIPC())
    r.graph.profile.begin_loading()
    r.graph.profile.accept(DependencyBundle(r.graph.profile.identity))
    assert r.graph.profile.loading is False
    assert ("overlay-remove", OverlayId.LOADING) in r.graph.ipc.commands


def test_load_deps_async_uses_a_custom_build(make_session):
    """#16: `run` passes its own CLI-flag-aware builder; load_deps_async must call THAT (not the
    config-only build_reader_deps) and publish its result for the poll loop to inject."""

    from util import FakeIPC

    r = make_session(FakeIPC())
    called = {"n": 0}

    def _build():
        called["n"] += 1
        return "SCORER", None, None, None

    r.graph.profile.load({}, build=_build)
    assert r.graph.profile.loading is True  # spinner armed immediately (subs draw meanwhile)
    await_ready(lambda: r.graph.profile.ready, "the build thread never published deps")
    assert called["n"] == 1
    r.graph.profile.drain()
    assert r.graph.profile.scorer == "SCORER" and r.graph.profile.loading is False


def test_load_deps_async_consumes_a_prebuilt_hoisted_future(make_session):
    """The run-mode hoist: begin_deps_build starts the build BEFORE mpv launches; load_deps_async then
    consumes that Future (it must NOT build a second time) and publishes the result for the poll loop."""

    from util import FakeIPC

    import saitenka.app.features.profiles.dependencies as rd

    built = {"n": 0}

    def _build():
        built["n"] += 1
        return "SCORER", None, None, None

    fut = rd.begin_deps_build({}, _build)  # hoisted: runs before the reader exists
    r = make_session(FakeIPC())
    r.graph.profile.load({}, prebuilt=fut)  # consume the in-flight build, don't restart it
    await_ready(lambda: r.graph.profile.ready, "the build thread never published deps")
    assert (
        built["n"] == 1
    )  # built exactly once — by begin_deps_build, not re-run by load_deps_async
    r.graph.profile.drain()
    assert r.graph.profile.scorer == "SCORER"


def test_each_entrypoint_declares_its_own_startup_hint() -> None:
    """`attach` and `run` differ here on purpose, and nothing pinned the difference.

    The breadcrumb covers the file-load wait. `run` owns the player, so it shows one unless the
    session is a screenshot capture; `attach` joins an mpv that is already playing, so the wait it
    would cover has already happened. Unifying them looks like a tidy-up and is a behaviour change
    in both directions — a breadcrumb for a wait that never happens, or a screenshot carrying one.

    Asserted against the call site because that IS the fact: which argument each entrypoint passes
    is composition, and composition is what the duty census tracks.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    def hint_argument(relative: str) -> str:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "install_session_runtime"
            ):
                for keyword in node.keywords:
                    if keyword.arg == "startup_hint":
                        return ast.unparse(keyword.value)
        raise AssertionError(
            f"{relative} no longer installs a session runtime with a hint decision"
        )

    assert hint_argument("src/saitenka/app/commands/attach.py") == "False"
    assert hint_argument("src/saitenka/app/launch/run.py") == "not opts.screenshot"
