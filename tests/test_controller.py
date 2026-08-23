"""Controller: live-run startup + hover hysteresis (Yomitan-style linger)."""

import functools
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from driver import Driver
from hypothesis import given, settings
from hypothesis import strategies as st
from util import FakeIPC as RuntimeFakeIPC
from util import await_ready, keybind_registry, runtime_gateway

import saitenka.app.controller as C
from saitenka.app import bindings, miner, miner_ui, nested_popup, tooltip, tooltip_panel
from saitenka.app.controller import Reader
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.tooltip_panel import PanelKey


class FakeIPC(RuntimeFakeIPC):
    """The shared fake, with event draining disabled.

    Everything this used to define — the timer port, the inline correlated submit, the recording
    `command` — was a line-for-line copy of the shared fake. The copy is the hazard: it drifts, and
    a port it forgets sends production down a fallback branch production itself never takes.

    The one real difference is kept: these tests drive `pump` directly and assert against
    `commands`, so draining queued events here would fire observations they never asked for.
    """

    def receive_session(self, _timeout, _handle) -> None:
        return None


def test_hover_view_snapshots_the_hover_stack():
    from saitenka.runtime import events

    r = Reader(FakeIPC())
    r._pause_store.dispatch(events.HoverPauseClaimed(paused=True))
    r.episode.nav_idx = 4
    r.tip.nest.word = "読"
    view = r.hover_view()
    assert (view.paused, view.nav_idx) == (True, 4)
    assert view.nested.word == "読"
    # a frozen point-in-time copy: later mutation of the reader must not leak into a taken snapshot
    r._pause_store.dispatch(events.HoverPauseReleased())
    assert view.paused is True


def test_every_global_binding_reaches_mpv_as_one_command_string():
    """The trap this pins: a binding's command must be ONE string. Split into args it registers
    without complaint and silently never fires. That held for the per-key `keybind` form and holds
    for the section lines that replaced it — each line is `KEY script-message <msg>`.
    """
    ipc = FakeIPC()
    Reader(ipc, anki=object())._register_keybinds()

    contents = _section(ipc, C.GLOBAL_SECTION)[0]
    assert contents, "no global bindings registered"
    for line in contents.splitlines():
        key, _, spec = line.partition(" ")
        assert key and spec.startswith("script-message "), line
        assert len(spec.split()) == 2, f"command must be one string: {line}"

    keys = {line.split(" ", 1)[0] for line in contents.splitlines()}
    assert {
        "a",
        "c",
        "Ctrl+m",
        "Alt+p",
        "Alt+t",
        "Alt+b",
        "\\",
        "`",
        "Alt+a",
        "F1",
        "Ctrl+Shift+T",
    } <= keys
    # mouse controls are NOT in the global scope — they need their own FORCED section (see below)
    assert {"MBTN_LEFT", "WHEEL_UP", "WHEEL_DOWN"}.isdisjoint(keys)
    # …but they ARE dispatchable, so the registry a press goes through spans both sections
    assert "MBTN_LEFT" in keybind_registry(ipc)


def _section(ipc, name):
    """(contents, flags) of the last `define-section` for ``name``."""
    for cmd in reversed(ipc.commands):
        if cmd and cmd[0] == "define-section" and cmd[1] == name:
            return cmd[2], (cmd[3] if len(cmd) > 3 else None)
    return None, None


def test_the_global_bindings_register_as_one_section_at_default_priority():
    """One command, not one per key: ~24 correlated commands in flight before the reactor drains
    would compete for terminal reservations, and over the bound a bind is dropped with only a log
    line — a dead shortcut. Default priority is what mpv's own `keybind` gives, so a user's
    input.conf shadows these exactly as it did before; `force` would silently start overriding it.
    """
    ipc = FakeIPC()

    Reader(ipc)._register_keybinds()

    contents, flags = _section(ipc, C.GLOBAL_SECTION)
    assert flags == "default"
    assert ("enable-section", C.GLOBAL_SECTION) in [c[:2] for c in ipc.commands]
    assert len([c for c in ipc.commands if c and c[0] == "keybind"]) == 0
    assert len(contents.splitlines()) == len({ln.split(" ", 1)[0] for ln in contents.splitlines()})


def test_mouse_controls_live_in_a_separate_forced_section():
    """Clicks/wheel go into a FORCED mpv section so they outrank other scripts' forced MBTN_LEFT
    (uosc/inputevent); it's enabled only while a saitenka surface is up and released otherwise.
    Separate from the global one precisely because that must NOT be forced."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    contents, flags = _section(ipc, bindings.MOUSE_SECTION)
    name = bindings.MOUSE_SECTION
    assert flags == "force"
    assert "MBTN_LEFT script-message saitenka-click" in contents
    assert "WHEEL_UP script-message saitenka-scroll-up" in contents

    # no surface up → not enabled; a tooltip up → enabled; gone → disabled
    r._sync_mouse_capture()
    assert not any(c[0] == "enable-section" and c[1] == name for c in ipc.commands)
    r.tip.view.rect = (0, 0, 10, 10)
    r._sync_mouse_capture()
    assert ipc.commands[-1][:2] == ("enable-section", name)
    r.tip.view.rect = None
    r._sync_mouse_capture()
    assert ipc.commands[-1] == ("disable-section", name)


def test_hover_reacts_to_the_pointer_observation_not_to_a_tick():
    """WP5.1's ingress half, asserted where it is observable: a pointer observation alone has to
    move the hover, with no tick anywhere in the trace. The negative control is the same trace
    without the observation — a tick that still drove hover would pass both halves.
    """
    ipc = RuntimeFakeIPC()
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r.tokens = [Token("猫", "猫", "ねこ", "名詞", 0, 1)]
    r.boxes = [WordBox(0, 0, 0, 40, 40)]
    r.start_observing()
    assert r.hover == -1

    ipc.emit(
        {"event": "property-change", "name": "mouse-pos", "data": {"hover": True, "x": 20, "y": 20}}
    )
    r._drain_events()

    assert r.hover == 0  # the observation alone moved it
    r.close()


def test_mouse_capture_reasserts_itself_until_the_surface_goes_down():
    """A rival script can re-force its own section at any time, so ours is re-asserted on a repeating
    deadline. The due event re-checks rather than trusting the arm: re-forcing after the surface
    went down would take the mouse back from mpv for nothing."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    r.tip.view.rect = (0, 0, 10, 10)
    r._sync_mouse_capture()
    forced = ipc.commands.count(
        ("enable-section", bindings.MOUSE_SECTION, "allow-hide-cursor+allow-vo-dragging")
    )

    assert ipc.fire_runtime_timer("lifecycle:mouse-capture-reassert")  # a rival may have taken it
    assert (
        ipc.commands.count(
            ("enable-section", bindings.MOUSE_SECTION, "allow-hide-cursor+allow-vo-dragging")
        )
        == forced + 1
    )

    from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

    identity, due = ipc.timers["lifecycle:mouse-capture-reassert"]  # captured in flight
    r.tip.view.rect = None
    r._sync_mouse_capture()  # surface down → released, and the re-assertion retired with it
    assert ipc.commands[-1] == ("disable-section", bindings.MOUSE_SECTION)

    due(EffectFinished(EffectId(0), Owner.SESSION, identity, EffectOutcome.SUCCEEDED))

    assert ipc.commands[-1] == (
        "disable-section",
        bindings.MOUSE_SECTION,
    )  # the late one took nothing back


def test_hover_pause_key_is_configurable():
    from saitenka.app.config import KeyOptions, ReaderOptions

    ipc = FakeIPC()
    options = ReaderOptions(keys=KeyOptions(hover_pause_key="Alt+q"))
    Reader(ipc, options=options)._register_keybinds()
    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}
    assert binds["Alt+q"] == "script-message saitenka-toggle-hover-pause"


def test_subtitle_retry_key_is_configurable_and_dispatches(monkeypatch):
    from saitenka.app.config import KeyOptions, ReaderOptions

    ipc = FakeIPC()
    reader = Reader(ipc, options=ReaderOptions(keys=KeyOptions(subtitle_retry_key="Ctrl+r")))
    called = []
    monkeypatch.setattr(reader, "retry_japanese_subtitles", lambda: called.append(True))
    reader._register_keybinds()
    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}

    reader._handle(binds["Ctrl+r"].removeprefix("script-message "))

    assert called == [True]


# --- Stage 4: subtitle navigation keys (Alt+←/→/↓, sub-delay) ------------------------------------


def test_sub_nav_keybinds_registered_with_single_string():
    """Alt+LEFT/RIGHT/DOWN must be registered as keybind + single-string script-message (the known mpv
    gotcha: split args = key silently dead). z/Z/x are NOT ours — they pass through to mpv's builtin
    repeatable sub-delay bindings, so we must not shadow them."""
    ipc = FakeIPC()
    Reader(ipc)._register_keybinds()
    binds = {k: f"script-message {m}" for k, m in keybind_registry(ipc).items()}
    for key in ("Alt+LEFT", "Alt+RIGHT", "Alt+DOWN"):
        assert key in binds, f"{key} not registered; binds={list(binds)}"
        assert binds[key].startswith("script-message "), f"{key}: not script-message: {binds[key]}"
    for native in ("z", "Z", "x"):  # left to mpv's native sub-delay (repeatable, own OSD)
        assert native not in binds, f"{native} should pass through to mpv, not be bound by saitenka"


def test_sub_seek_prev_sends_ipc_command():
    """Receiving the sub-prev client-message must send sub-seek -1 to mpv IPC."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    binds = keybind_registry(ipc)
    sub_prev_msg = binds.get("Alt+LEFT")
    assert sub_prev_msg, "no Alt+LEFT keybind"
    # Dispatch the message and verify sub-seek -1 was sent
    r._handle(sub_prev_msg)
    assert ("sub-seek", "-1") in [(c[0], c[1]) for c in ipc.commands], (
        f"sub-seek -1 not sent; commands={ipc.commands}"
    )
    assert r.subtitle_pipeline.generation == 1


def test_sub_seek_next_sends_ipc_command():
    """Receiving the sub-next client-message must send sub-seek 1 to mpv IPC."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    binds = keybind_registry(ipc)
    r._handle(binds["Alt+RIGHT"])
    assert ("sub-seek", "1") in [(c[0], c[1]) for c in ipc.commands]


def test_a_navigation_step_for_a_replaced_cue_never_seeks(monkeypatch):
    """WP4.5: a navigation effect is qualified by the cue it was decided against.

    Today reduce and execute are adjacent, so this cannot happen live — which is exactly why it
    needs pinning now. The moment the step is queued behind anything, a seek carried out against a
    cue the user has already left moves the video somewhere they never asked for, and the whole
    keypress reads as a runtime glitch rather than a stale command.
    """
    from util import record_spans

    from saitenka.app.subtitle_intents import SeekCue

    spans = record_spans(monkeypatch)
    ipc = FakeIPC()
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    r.set_subtitle("猫を見る")
    stale = SeekCue(1, r.cue_revision)
    r.set_subtitle("犬も見る")  # the cue the step was decided against is gone

    assert not r.seek_cue(stale)

    assert not any(command and command[0] == "sub-seek" for command in ipc.commands)
    (decision,) = [span["attrs"] for span in spans if span["name"] == "sub_nav_identity"]
    assert decision["outcome"] == "superseded"  # dropped out loud, not silently
    assert r.seek_cue(SeekCue(1, r.cue_revision))  # and the current cue still navigates


def test_sub_seek_replay_sends_ipc_command():
    """Receiving the sub-replay client-message must send sub-seek 0 to mpv IPC."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    binds = keybind_registry(ipc)
    r._handle(binds["Alt+DOWN"])
    assert ("sub-seek", "0") in [(c[0], c[1]) for c in ipc.commands]


def test_sub_nav_config_knobs_respected():
    """Custom sub_prev_key/sub_next_key/sub_replay_key config knobs must be registered."""
    ipc = FakeIPC()
    r = Reader(ipc, sub_prev_key="Alt+a", sub_next_key="Alt+d", sub_replay_key="Alt+s")
    r._register_keybinds()
    binds = set(keybind_registry(ipc))
    assert "Alt+a" in binds
    assert "Alt+d" in binds
    assert "Alt+s" in binds


# --- #5: instant subtitle navigation via the parsed cue index -----------------------------------

_NAV_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nいち\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nに\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\nさん\n"
)


def _reader_with_index(monkeypatch):
    from saitenka.subtitles import CueIndex, parse_srt

    # The shared fake, not this file's local one: the settle-timer gate below asserts on its
    # schedule/cancel ledger, which is the only place "retired exactly once" is observable.
    ipc = RuntimeFakeIPC()
    r = Reader(ipc)
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    monkeypatch.setattr(r, "renderer", NullRenderer())  # skip the raster; assert state only
    r.episode.sub_index = CueIndex(parse_srt(_NAV_SRT))
    r._register_keybinds()
    return r, ipc


def _msg_for(ipc, key):
    # The message currently bound to `key` (KeyError if unbound). Shared parse: keybind_registry honours
    # later-binds-over-earlier and the `keybind KEY ignore` unbinds tooltip teardown emits.
    return keybind_registry(ipc)[key]


def test_anchor_snaps_the_nearest_cue_start_to_the_playhead(monkeypatch):
    # One-press manual re-time: at playhead 9s the nearest cue is さん (@10s), so sub-delay shifts by
    # -1s to land it here — every later cue follows by the same offset (fixes residual auto-sync drift).
    r, ipc = _reader_with_index(monkeypatch)
    monkeypatch.setattr(r, "toast", lambda *_a, **_k: None)
    ipc.props["time-pos"] = 9.0
    ipc.props["sub-delay"] = 0.0

    r._anchor_subtitles()

    assert ("set_property", "sub-delay", "-1.000") in ipc.commands


def test_anchor_is_cumulative_from_the_current_delay(monkeypatch):
    # A second anchor refines a first: from an existing +2s delay, snapping さん (@10s) to playhead 13s
    # sets an absolute delay of +3s (13 - 10), not +2 plus a fresh guess.
    r, ipc = _reader_with_index(monkeypatch)
    monkeypatch.setattr(r, "toast", lambda *_a, **_k: None)
    ipc.props["time-pos"] = 13.0
    ipc.props["sub-delay"] = 2.0

    r._anchor_subtitles()

    assert ("set_property", "sub-delay", "3.000") in ipc.commands


def test_anchor_warns_and_no_ops_without_a_subtitle_index(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    messages: list[str] = []
    monkeypatch.setattr(r, "toast", lambda text, *_a: messages.append(text))
    r.episode.sub_index = None

    r._anchor_subtitles()

    assert messages == ["No subtitle track to anchor"]
    assert not [c for c in ipc.commands if c[:2] == ("set_property", "sub-delay")]


@given(
    starts_ms=st.lists(st.integers(0, 600_000), min_size=1, max_size=8, unique=True),
    playhead_ms=st.integers(0, 600_000),
    delay_ms=st.integers(-10_000, 10_000),
)
@settings(max_examples=200, deadline=None)
def test_anchor_lands_the_nearest_cue_start_on_the_playhead_for_any_index(
    starts_ms, playhead_ms, delay_ms
):
    # The anchor invariant behind the single hand-picked example: whatever the cue set, current delay,
    # and playhead, the emitted sub-delay makes the nearest displayed cue's effective start coincide
    # with the playhead — the "snap what I'm hearing to now" contract, over the whole input space.
    from saitenka.subtitles import Cue, CueIndex

    ipc = FakeIPC()
    r = Reader(ipc)
    r.toast = lambda *_a, **_k: None  # instance-shadow the toast; assert the delay, not the OSD
    r.episode.sub_index = CueIndex([Cue(s / 1000, s / 1000 + 1.0, "x") for s in sorted(starts_ms)])
    playhead, delay = playhead_ms / 1000, delay_ms / 1000
    ipc.props["time-pos"] = playhead
    ipc.props["sub-delay"] = delay

    r._anchor_subtitles()

    emitted = [c for c in ipc.commands if c[:2] == ("set_property", "sub-delay")]
    assert emitted, "anchor must set sub-delay"
    new_delay = float(emitted[-1][2])
    nearest = min(r.episode.sub_index.cues, key=lambda c: abs((c.start + delay) - playhead))
    assert abs((nearest.start + new_delay) - playhead) < 1e-3  # ±the 3-decimal delay quantisation


def test_mine_current_video_forces_the_animated_clip(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    captured: dict = {}
    monkeypatch.setattr(r, "mine_current", lambda **k: captured.update(k))
    r.mine_current_video()
    assert captured == {
        "animated": True
    }  # the video-mine shortcut forces a motion clip for this mine


def test_mine_keybinds_register_even_when_anki_absent():
    """Regression: attach mode loads Anki ASYNC, after _register_keybinds runs — and we never
    re-register. A requires-gated bind left the mine keys permanently unbound (Ctrl+m/Ctrl+Shift+m/
    Shift+m did nothing) while the mouse add-button, checked live, still mined. Bindings must register
    with anki=None and stay bound; the handler no-ops until Anki lands."""
    from saitenka.app.bindings import MINE_ALL_MSG, MINE_MSG, MINE_VIDEO_MSG

    ipc = FakeIPC()
    Reader(ipc, anki=None)._register_keybinds()  # Anki not up yet (the attach-mode reality)
    assert _msg_for(ipc, "Ctrl+m") == MINE_MSG
    assert _msg_for(ipc, "Ctrl+Shift+m") == MINE_VIDEO_MSG
    assert _msg_for(ipc, "Shift+m") == MINE_ALL_MSG


def test_mine_video_key_registers_and_routes_to_the_video_mine(monkeypatch):
    from saitenka.app.bindings import MINE_VIDEO_MSG

    ipc = FakeIPC()
    reader = Reader(ipc, anki=object())
    reader._register_keybinds()  # mine bindings require anki
    assert _msg_for(ipc, "Ctrl+Shift+m") == MINE_VIDEO_MSG  # default shortcut is bound
    # and the message routes to the video-mine action (not the still mine)
    calls: list = []
    monkeypatch.setattr(reader, "mine_current_video", lambda: calls.append("video"))
    reader._handle(MINE_VIDEO_MSG)
    assert calls == ["video"]


def test_sub_nav_renders_target_line_instantly_and_still_seeks(monkeypatch):
    """Next must render the following cue's text in the overlay right away AND still issue the real
    sub-seek so the video catches up behind it."""
    r, ipc = _reader_with_index(monkeypatch)
    ipc.props["sub-text"] = "いち"
    r.set_subtitle("いち")  # currently on cue 1
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "に"  # cue 2 rendered instantly, before any seek settles
    assert ("sub-seek", "1") in [(c[0], c[1]) for c in ipc.commands]  # video seek still fired


def test_sub_nav_keeps_target_geometry_after_issuing_seek(monkeypatch):
    from saitenka.app.subtitle_render import NullRenderer as _Inert
    from saitenka.subtitles import (
        GeometryRequest,
        GeometrySnapshot,
        SubtitleEventId,
        SubtitleFrameId,
        SubtitleTrackId,
    )

    class PublishingRenderer(_Inert):
        """Publishes geometry at `activate`. Holds the Reader by construction: the cue identity it
        fabricates is host state, and `activate` receives a `SubtitleTarget` — deliberately narrower
        than a host, so a stub that needs one says so instead of taking it from the protocol."""

        def __init__(self, reader: Reader) -> None:
            self._reader = reader

        def draw(self, _request=None, _surfaces=None, _ipc=None, /, **_ports) -> None:
            return None

        def activate(self, _target=None, _sid=None, /) -> bool:
            reader = self._reader
            track_id = SubtitleTrackId("track-1")
            source_order = {"いち": 1, "に": 2}[reader.sub_text]
            event_id = SubtitleEventId(
                track_id, source_order * 1_000, source_order * 1_000 + 500, 0, source_order
            )
            request = GeometryRequest(
                reader.subtitle_pipeline.generation,
                track_id,
                SubtitleFrameId(track_id, (event_id,)),
                source_order * 1_000,
                reader.osd,
                reader.osd,
                b"[Script Info]\n",
            )
            ticket = reader.subtitle_pipeline.prepare(request)
            assert ticket is not None
            assert reader.subtitle_pipeline.publish(
                ticket,
                GeometrySnapshot(
                    request.generation,
                    request.track_id,
                    request.frame_id,
                    request.timestamp_ms,
                    request.variant,
                    (),
                ),
            )
            return True

    r, ipc = _reader_with_index(monkeypatch)
    r.renderer = PublishingRenderer(r)
    ipc.props["sub-text"] = "いち"
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0

    r._handle(_msg_for(ipc, "Alt+RIGHT"))

    assert r.subtitle_pipeline.current is not None
    assert r.subtitle_pipeline.current.frame_id.active_event_ids[0].source_order == 2


def test_sub_nav_prev_and_replay(monkeypatch):
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("に")  # cue 2
    ipc.props["sub-start"] = 4.0
    r._handle(_msg_for(ipc, "Alt+LEFT"))
    assert r.sub_text == "いち" and ("sub-seek", "-1") in [(c[0], c[1]) for c in ipc.commands]
    r.set_subtitle("に")
    ipc.props["sub-start"] = 4.0
    r._handle(_msg_for(ipc, "Alt+DOWN"))  # replay → same cue
    assert r.sub_text == "に"


def test_sub_nav_chains_forward_with_stale_position(monkeypatch):
    """Rapid next/next while the seek is still in flight (sub-start/time-pos stale) must keep
    stepping forward, resolved by the rendered text + the _nav_idx hint."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0  # stale for the whole burst (video hasn't caught up)
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "に"
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "さん"  # advanced past cue 2 despite the stale sub-start


def test_sub_nav_from_a_gap_opens_the_upcoming_cue(monkeypatch):
    """Navigating NEXT while no sub is on screen (a gap) must land ON the upcoming cue — matching
    mpv's sub-seek 1 — not skip past it."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("")  # nothing showing (between cues)
    ipc.props["time-pos"] = 8.5  # gap before cue 3 (starts at 10.0)
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "さん"  # cue 3, the upcoming one — not skipped


def test_sub_nav_records_otel_sub_seek_metric(monkeypatch):
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    r, ipc = _reader_with_index(monkeypatch)
    ipc.props["sub-text"] = "いち"
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        r._handle(_msg_for(ipc, "Alt+RIGHT"))
        snap = otel_metrics.snapshot()
        assert snap["saitenka.sub_seek.duration_ms"]["count"] == 1
        # cue_redraw fires from set_subtitle nested inside the sub_seek span above (the initial
        # "いち" render happened before telemetry was registered, so isn't counted here).
        assert snap["saitenka.cue_redraw.duration_ms"]["count"] == 1
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def test_sub_nav_span_and_cue_redraw_span_share_a_trace(monkeypatch, tmp_path):
    """The instant-nav keypress → drawn latency must be readable as ONE trace: sub_seek is the
    parent, cue_redraw (set_subtitle) nests inside it as a child sharing the same trace_id — that
    parent/child link is what makes seek-to-paint latency reconstructable from a trace.json export,
    instead of two spans with unrelated random trace_ids."""
    import opentelemetry.trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import set_tracer_provider
    from opentelemetry.util._once import Once

    from saitenka.app.otel_export import CTFSpanProcessor
    from saitenka.app.telemetry import span_gate

    r, ipc = _reader_with_index(monkeypatch)
    ipc.props["sub-text"] = "いち"
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0

    trace_path = tmp_path / "trace.json"
    processor = CTFSpanProcessor(trace_path, span_gate, start_thread=False)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    # The global TracerProvider is a real OTel "set once per process" latch (see
    # test_telemetry.py's _reset_providers docstring) — an earlier test in the same whole-suite run
    # (poe test-ft / poe cov, both single-process) may have already latched a DIFFERENT provider, in
    # which case set_tracer_provider below would silently no-op and production code (which reads the
    # global via trace.get_tracer(), not a locally-held reference) would trace into that other
    # provider instead of this test's own trace_path. Reset the private latch so this test is
    # order-independent; monkeypatch restores it after the test.
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", Once())
    set_tracer_provider(provider)
    span_gate.set(value=True)
    try:
        r._handle(_msg_for(ipc, "Alt+RIGHT"))
        processor.force_flush()
    finally:
        span_gate.set(value=False)
        provider.shutdown()

    import json

    events = json.loads(trace_path.read_text())["traceEvents"]
    spans = {e["name"]: e for e in events if e.get("ph") == "X"}
    assert "sub_seek" in spans
    assert "cue_redraw" in spans
    # The edge itself, not a shared root id: "same trace" was also true of a root and a sibling it
    # never called, and it is what the redraw being nested UNDER the seek has to survive.
    assert spans["cue_redraw"]["args"]["parent_id"] == spans["sub_seek"]["args"]["span_id"]


def test_reconcile_records_otel_sub_text_reconcile_metric(monkeypatch):
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    r, _ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        r._reconcile_sub_text("に")  # a genuine mpv-driven change, not a no-op/settle-guard swallow
        snap = otel_metrics.snapshot()
        assert snap["saitenka.sub_text_reconcile.duration_ms"]["count"] == 1
        assert snap["saitenka.cue_redraw.duration_ms"]["count"] == 1
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def test_sub_nav_without_index_only_seeks(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.set_subtitle("いち")
    r._register_keybinds()
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "いち"  # no index → overlay unchanged; mpv drives it via the seek
    assert ("sub-seek", "1") in [(c[0], c[1]) for c in ipc.commands]


def test_settle_guard_swallows_transient_empty_then_reconciles(monkeypatch):
    """After a nav render, an empty sub-text within the settle window is ignored (no blank flash);
    a non-empty mpv value (source of truth) reconciles and disarms the guard."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "に"  # rendered target
    r._reconcile_sub_text("")  # mpv's mid-seek blank
    assert r.sub_text == "に"  # swallowed — overlay didn't flash to nothing
    r._reconcile_sub_text("に")  # mpv settled on the matching cue
    assert r.sub_text == "に"
    r._reconcile_sub_text("さん")  # a genuine later change still adopts mpv's truth
    assert r.sub_text == "さん"


def test_settle_guard_expires_and_adopts_empty(monkeypatch):
    """Outside the settle window an empty sub-text is honoured (a real gap between cues clears it)."""
    r, _ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("に")
    r.retire_settle_window()  # window already closed
    r._reconcile_sub_text("")
    assert r.sub_text == ""


# --- WP4.5 gate: the settle timer is retired exactly once, by each of its four triggers ---------

_SETTLE = "subtitle:navigation-settle"


def _navigated(monkeypatch):
    """A reader mid-navigation: the settle window is open and its deadline is scheduled."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.episode.sub_settle.open
    assert ipc.timer_calls(_SETTLE) == ["schedule"]
    return r, ipc


def test_a_reconcile_cancels_the_settle_timer_exactly_once(monkeypatch):
    r, ipc = _navigated(monkeypatch)

    r._reconcile_sub_text("さん")  # a genuine later cue closes the window
    r._reconcile_sub_text("よん")  # a second reconcile must not cancel a window that is gone

    assert ipc.timer_calls(_SETTLE) == ["schedule", "cancel"]
    assert not r.episode.sub_settle.open


def test_a_source_replacement_cancels_the_settle_timer_exactly_once(monkeypatch):
    r, ipc = _navigated(monkeypatch)

    r._replace_subtitle_source("/media/next.srt", reason="test")
    r._replace_subtitle_source("/media/third.srt", reason="test")

    assert ipc.timer_calls(_SETTLE) == ["schedule", "cancel"]


def test_close_cancels_the_settle_timer(monkeypatch):
    """A deadline may not outlive the session that armed it."""
    r, ipc = _navigated(monkeypatch)

    r.close()

    assert ipc.timer_calls(_SETTLE) == ["schedule", "cancel"]
    assert not r.episode.sub_settle.open


def test_a_late_due_from_a_superseded_navigation_leaves_the_new_window_open(monkeypatch):
    """The classic race: the first nav's deadline arrives after a second nav opened its own."""
    r, ipc = _navigated(monkeypatch)
    stale = ipc.timers[_SETTLE][0]
    r._handle(_msg_for(ipc, "Alt+RIGHT"))  # supersede: a second nav, a second deadline

    r._settle_due(stale)

    assert r.episode.sub_settle.open


def test_the_matching_due_closes_the_window_without_a_cancel(monkeypatch):
    """A fired deadline is already spent; cancelling it would be the second retirement."""
    r, ipc = _navigated(monkeypatch)

    assert ipc.fire_runtime_timer(_SETTLE)

    assert not r.episode.sub_settle.open
    assert ipc.timer_calls(_SETTLE) == ["schedule"]


def test_repeated_empty_observation_is_idempotent(monkeypatch):
    """One cue-to-gap transition clears interaction; stable empty polls do no more work."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("に")
    metric_reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[metric_reader])
    otel_metrics.register(metric_reader, provider.get_meter("test"))
    try:
        r._reconcile_sub_text("")
        commands_after_transition = tuple(ipc.commands)
        for _ in range(1_000):
            r._reconcile_sub_text("")

        snap = otel_metrics.snapshot()
        assert r.sub_text == "" and r._cue_retired is True
        assert tuple(ipc.commands) == commands_after_transition
        assert snap["saitenka.sub_text_reconcile.duration_ms"]["count"] == 1
        assert snap["saitenka.cue_redraw.duration_ms"]["count"] == 1
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def test_settle_guard_swallows_mpv_reporting_the_pre_nav_cue(monkeypatch):
    """Found via a real-mpv smoke test: right after a nav render, mpv's own native sub-seek (fired
    behind it to catch the video up) can transiently re-report the cue we just navigated AWAY from —
    not just an empty blip. Naively adopting it would revert the render AND silently reset _nav_idx
    (any set_subtitle call does), breaking next/next/next chaining even though the render was already
    correct at the real target."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")  # cue 1
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "に" and r.hover_view().nav_idx == 1  # rendered target, chaining hint set
    r._retire_cue_identity("sub-start")  # seek timing landed after the instant target render
    r._reconcile_sub_text("いち")  # mpv transiently re-reports the pre-nav cue mid-seek
    assert r.sub_text == "に"  # swallowed — no revert flash
    assert r.hover_view().nav_idx == 1  # and, unlike a real set_subtitle call, chaining survives
    r._reconcile_sub_text("に")  # mpv settles; identity is reinstalled with the landed timing
    assert (
        r.sub_text == "に" and r.hover_view().nav_idx == 1 and not r._cue_retired
    )  # the settled cue is interactive without losing the chaining hint


def test_the_settle_deadline_retires_the_window_exactly_once(monkeypatch):
    """The window closes on its named due event, not on a wall clock the reconcile polls."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.episode.sub_settle.open
    assert "subtitle:navigation-settle" in ipc.timers

    assert ipc.fire_runtime_timer("subtitle:navigation-settle")

    assert r.episode.sub_settle.open is False
    r._reconcile_sub_text("")  # no longer guarded: a real gap now clears the overlay
    assert r.sub_text == ""


def test_a_superseded_navigation_deadline_cannot_close_the_current_window(monkeypatch):
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    stale = r.episode.sub_settle.identity
    r._handle(_msg_for(ipc, "Alt+RIGHT"))  # a second nav opens its own window

    r._settle_due(stale)

    assert r.episode.sub_settle.open


def test_replacing_the_subtitle_source_retires_the_settle_window(monkeypatch):
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.episode.sub_settle.open

    r._replace_subtitle_source("/media/next.mkv", reason="test")

    assert r.episode.sub_settle.open is False
    assert "subtitle:navigation-settle" not in ipc.timers


def test_settle_guard_reinstalls_retired_identity_for_same_text():
    ipc = FakeIPC()
    ipc.props.update({"sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.set_subtitle("同じ字幕")
    reader.episode.nav_prev_text = "同じ字幕"
    reader.episode.nav_idx = 1
    reader.episode.sub_settle = reader.episode.sub_settle.begin()
    reader._retire_cue_identity("sub-start")

    reader._reconcile_sub_text("同じ字幕")

    assert reader._cue_retired is False
    assert reader._current_cue_identity is not None
    assert reader.hover_view().nav_idx == 1


def test_navigation_identity_reinstall_does_not_count_the_cue_twice(monkeypatch):
    from saitenka.app.session_stats import SessionRecorder

    class Writer:
        def submit(self, _snapshot) -> None:
            pass

        def close(self, _timeout=2.0) -> None:
            pass

    reader, ipc = _reader_with_index(monkeypatch)
    reader.episode.session_recorder = SessionRecorder(
        "/anime/Show 01.mkv",
        clock=lambda: 0.0,
        wall_clock=lambda: 0.0,
        writer=Writer(),
    )
    reader.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0
    reader._handle(_msg_for(ipc, "Alt+RIGHT"))
    count_after_instant_render = reader.episode.session_recorder.snapshot.cue_count
    reader._retire_cue_identity("sub-start")

    reader._reconcile_sub_text("に")

    assert reader.episode.session_recorder.snapshot.cue_count == count_after_instant_render


def test_identical_text_navigation_counts_the_landed_cue():
    from saitenka.app.session_stats import SessionRecorder
    from saitenka.subtitles import Cue, CueIndex

    class Writer:
        def submit(self, _snapshot) -> None:
            pass

        def close(self, _timeout=2.0) -> None:
            pass

    ipc = FakeIPC()
    ipc.props.update({"sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.episode.sub_index = CueIndex([Cue(1.0, 2.0, "同じ"), Cue(3.0, 4.0, "同じ")])
    reader.episode.session_recorder = SessionRecorder(
        "/anime/Show 01.mkv",
        clock=lambda: 0.0,
        wall_clock=lambda: 0.0,
        writer=Writer(),
    )
    reader.set_subtitle("同じ")
    reader._sub_nav(1)
    assert reader.episode.session_recorder.snapshot.cue_count == 1
    ipc.props.update({"sub-start": 3.0, "sub-end": 4.0})
    reader._retire_cue_identity("sub-start")

    reader._reconcile_sub_text("同じ")

    assert reader.episode.session_recorder.snapshot.cue_count == 2


def test_navigation_hands_a_filtered_episode_back_to_mpv():
    """`--sub-filter-regex`/`-jsre` drop whole cues between the file and the screen, and the cue
    index is the file's. Stepping by index there renders a line mpv never shows and then settles
    somewhere else; mpv's own `sub-seek` cannot land on a cue mpv dropped, so the instant half is
    given up rather than aimed at silence."""
    from saitenka.subtitles import Cue, CueIndex

    ipc = FakeIPC()
    ipc.props.update({"sub-start": 1.0, "sub-end": 2.0, "options/sub-filter-regex": ["^SIGN:"]})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.episode.sub_index = CueIndex([Cue(1.0, 2.0, "いち"), Cue(3.0, 4.0, "に")])
    reader.set_subtitle("いち")

    assert reader._sub_nav(1) is False
    assert reader.sub_text == "いち", "the overlay was moved onto a cue mpv may not show"


def test_navigation_stays_instant_without_a_filter():
    """The negative control for the guard above: it costs the feature when it fires, so it must not
    fire on an ordinary session."""
    from saitenka.subtitles import Cue, CueIndex

    ipc = FakeIPC()
    ipc.props.update({"sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.episode.sub_index = CueIndex([Cue(1.0, 2.0, "いち"), Cue(3.0, 4.0, "に")])
    reader.set_subtitle("いち")

    assert reader._sub_nav(1) is True
    assert reader.sub_text == "に"


def test_reader_has_subtitle_state_before_any_cue():
    r = Reader(FakeIPC())
    assert r.sub_text == "" and r.tokens == [] and r.hover == -1


def test_pump_before_subtitle_does_not_raise():
    assert Reader(FakeIPC()).pump() is True


def _count_adds(ipc):
    return sum(1 for c in ipc.commands if c and c[0] == "overlay-add")


def test_paused_draw_schedules_and_fires_an_osd_nudge():
    """A subtitle draw while mpv is paused must re-flush the OSD — otherwise mpv doesn't present it
    until an input event (mpv #8172, the 'updates only on mouse move' bug).

    The re-flush is a named deadline now, so firing it *is* the nudge; waiting for another tick
    would prove only that ticks still happen."""
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    ipc.props["pause"] = True
    r = Reader(ipc)
    r.refresh_osd()
    ipc.props["sub-text"] = "いち"
    r._observe_property("sub-text", "いち")  # mpv reports the cue; the drain reconciles it
    r.pump()  # adopts the cue → draws SUB_ID while paused → arms the nudge
    assert r._nudge_pending is True
    before = _count_adds(ipc)

    assert ipc.fire_runtime_timer("lifecycle:paused-repaint")

    assert _count_adds(ipc) > before  # repaint re-issued the live overlay(s)
    assert r._nudge_pending is False


def test_a_burst_of_paused_draws_repaints_once():
    """The deadline's revision fence is what coalesces, so nothing has to track whether a nudge is
    already owed. Without it each draw arms its own and mpv is poked once per overlay op."""
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    ipc.props["pause"] = True
    r = Reader(ipc)
    r.refresh_osd()
    ipc.props["sub-text"] = "いち"
    r._observe_property("sub-text", "いち")
    r.pump()
    r._observe_property("sub-text", "に")
    r.pump()  # a second paused draw, before the first nudge was ever delivered
    before = _count_adds(ipc)

    assert ipc.fire_runtime_timer("lifecycle:paused-repaint")
    assert not ipc.fire_runtime_timer("lifecycle:paused-repaint")  # only one was ever pending

    assert _count_adds(ipc) > before
    assert r._nudge_pending is False


def test_playing_draw_does_not_nudge():
    """While playing, frames present on their own — no re-flush (would be per-tick waste)."""
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    ipc.props["pause"] = False
    r = Reader(ipc)
    r.refresh_osd()
    ipc.props["sub-text"] = "いち"
    r._observe_property("sub-text", "いち")
    r.pump()
    assert r.sub_text == "いち"  # the cue really did draw — otherwise the nudge check is vacuous
    assert r._nudge_pending is False


def test_overlay_repaint_reissues_live_overlays():
    from PIL import Image

    from saitenka.mpvio.osd import Overlay

    ipc = FakeIPC()
    ov = Overlay(ipc)
    ov.show(Image.new("RGBA", (4, 4)), 1, 2, oid=1)
    ov.hide(oid=1)  # removed → not live → must NOT be re-added
    ov.show(Image.new("RGBA", (4, 4)), 3, 4, oid=2)
    ipc.commands.clear()
    ov.repaint()
    adds = [c for c in ipc.commands if c and c[0] == "overlay-add"]
    assert len(adds) == 1 and adds[0][1] == 2  # only the still-live oid 2


def test_paused_nudge_records_otel_counters():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    ipc.props["pause"] = True
    r = Reader(ipc)
    r.refresh_osd()
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        ipc.props["sub-text"] = "いち"
        r._observe_property("sub-text", "いち")  # mpv reports the cue; the drain reconciles it
        r.pump()  # a draw lands while paused → osd.paused_draw, arms the nudge
        assert ipc.fire_runtime_timer("lifecycle:paused-repaint")  # → osd.paused_nudge
        snap = otel_metrics.snapshot()
        assert snap["saitenka.osd.paused_draw"]["value"] >= 1
        assert snap["saitenka.osd.paused_nudge"]["value"] >= 1
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def test_stall_stays_quiet_when_ipc_alive_but_no_subs(caplog):
    """A section with no subtitles (an OP) must NOT warn: IPC alive (bytes flowing + osd-dimensions ok)
    is healthy even with no cue for minutes — the old 'no subtitle text' warning was a false alarm."""
    import logging

    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    ipc._bytes_read = 500  # mpv's replies ARE arriving
    r = Reader(ipc)
    with caplog.at_level(logging.WARNING):
        r._check_startup_health()
    assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]


def test_stall_warns_when_read_direction_is_dead(caplog):
    """Zero bytes ever read = the Windows named-pipe failure → warn (nothing can draw), regardless of
    subtitles."""
    import logging

    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    ipc._bytes_read = 0  # dead read direction
    r = Reader(ipc)
    with caplog.at_level(logging.WARNING):
        r._check_startup_health()
    assert any("IPC looks dead" in rec.message for rec in caplog.records)


def test_word_switch_needs_dwell_but_first_open_is_instant(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["a", "b"]
    seen = []
    monkeypatch.setattr(r, "set_hover", lambda i: (seen.append(i), setattr(r, "hover", i)))
    # word 0 near (5,5), word 1 near (5,50); tooltip is off elsewhere
    monkeypatch.setattr(r, "_hit", lambda _x, y: 0 if y < 10 else (1 if y < 60 else -1))
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])

    def mouse(x, y):
        Driver(r, instant=False).move(x, y)

    mouse(5, 5)  # first hover → opens INSTANTLY (no dwell)
    assert seen == [0] and r.hover == 0
    mouse(5, 50)  # transit onto word 1 en route to the tooltip
    assert r.hover == 0  # …does NOT switch yet: the dwell is armed, not elapsed
    mouse(5, 50)  # still resting there — re-arming the same word must not re-fire
    assert r.hover == 0
    assert _fire_dwell(ipc, "hover-switch")  # rested long enough on word 1
    assert r.hover == 1  # …now it switches


def test_a_dwell_that_lands_after_the_cursor_left_changes_nothing(monkeypatch):
    """The state a clock-polled dwell could not reach at all.

    Polling asked "has enough time passed" at a moment the cursor was already elsewhere, so a stale
    dwell simply never fired. A deadline is a real in-flight thing: it can be delivered after the
    cursor moved on.

    Two guards stop it — the timer's revision fence and the handler's own target check — so neither
    mutates lethally on its own and only removing both fails this. That is deliberate depth, not a
    redundant check to tidy away.
    """
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.scan_delay = 0.25
    _hover_base_word(r)
    _hover_first_scan_cell(r)  # arms the scan dwell

    from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

    identity, due = ipc.timers["lifecycle:scan-open"]  # the due event, captured in flight
    # leaving retires the dwell — but the captured due event is already out there
    Driver(r, instant=False).move(5, 5)

    due(EffectFinished(EffectId(0), Owner.INTERACTION, identity, EffectOutcome.SUCCEEDED))

    assert r.hover_view().nested.state is None  # the revision fence rejected it
    assert r.hover_view().scan_target is None


def test_transit_over_word_does_not_switch(monkeypatch):
    # dragging up to the tooltip: brush word 1, then reach the tooltip — tooltip must stay on word 0
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["a", "b"]
    r.tip.view.rect = (100, 100, 80, 60)
    monkeypatch.setattr(r, "set_hover", lambda i: setattr(r, "hover", i))
    monkeypatch.setattr(r, "_hit", lambda _x, y: 0 if y < 10 else (1 if y < 60 else -1))
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])

    def mouse(x, y):
        Driver(r, instant=False).move(x, y)

    mouse(5, 5)  # tooltip on word 0
    mouse(5, 50)  # brush word 1 briefly (transit) — arms the switch dwell
    mouse(130, 130)  # arrive at the tooltip before it elapses

    assert _fire_dwell(ipc, "hover-switch")  # the dwell for the brushed word lands late…

    assert r.hover == 0 and not r.hover_view().tip.hide_pending  # …and is ignored


def test_hover_lingers_and_keeps_alive_over_tooltip(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["x"]
    r.tip.view.rect = (100, 100, 60, 40)
    # Both halves of the hover fact are stubbed: this test is about the DWELL, not about what a
    # build or a teardown does — the panel build needs a dictionary this Reader has no use for.
    monkeypatch.setattr(r, "set_hover", lambda i: setattr(r, "hover", i))
    monkeypatch.setattr(r, "retire_hover", lambda: setattr(r, "hover", -1))
    monkeypatch.setattr(r, "_hit", lambda x, y: 0 if (x < 10 and y < 10) else -1)
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])

    def mouse(x, y):
        Driver(r, instant=False).move(x, y)

    mouse(5, 5)  # on the word → hovered, no pending hide
    assert r.hover == 0 and not r.hover_view().tip.hide_pending

    mouse(300, 300)  # left the word → schedule hide, still shown
    assert r.hover == 0 and r.hover_view().tip.hide_pending

    mouse(120, 120)  # reached the tooltip in time → stays alive
    assert not r.hover_view().tip.hide_pending and r.hover == 0

    mouse(300, 300)  # leave everything → reschedule hide
    assert r.hover_view().tip.hide_pending
    assert _fire_dwell(ipc, "tooltip-hide")  # …and let it elapse
    assert r.hover == -1  # hidden only after the delay


def test_tooltip_capped_and_inside_safe_area():
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import tokenize

    r = Reader(FakeIPC(), tip_max_frac=0.5)
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = tokenize("本")
    r.boxes = [WordBox(0, 900, 1000, 40, 40)]  # word near the bottom (like a subtitle)
    Driver(r).move_to_word(0)

    # osd == REFERENCE (1080p) so tip_scale.display == 1.0 → viewport px are display px.
    margin = max(16, round(1080 * 0.05))
    assert r.tip.view.view_h <= round(1080 * 0.5)  # height capped
    _tx, ty = r.tip.view.xy
    assert ty >= margin  # top clears the header margin
    assert ty + r.tip.view.view_h <= 1080 - margin  # bottom stays inside the window


def test_panel_cache_avoids_rerender_on_revisit(monkeypatch):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token
    from saitenka.panel import Definition, Entry

    calls = []

    class FakeDS:
        def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
            calls.append(tok.surface)
            return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])

    r = Reader(FakeIPC(), dict_set=FakeDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [
        Token("本命", "本命", "ほんめい", "名詞", 0, 2),
        Token("読む", "読む", "よむ", "動詞", 2, 4),
    ]
    r.boxes = [WordBox(0, 100, 100, 40, 40), WordBox(1, 200, 100, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())  # keep our boxes
    ui = Driver(r)
    ui.move_to_word(0)
    ui.move_to_word(1)
    ui.move_to_word(0)  # revisit → served from cache
    assert calls == ["本命", "読む"]  # each word rendered once, not on every hover


def test_panel_cache_records_otel_render_and_cache_metrics(monkeypatch):
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token
    from saitenka.panel import Definition, Entry

    class FakeDS:
        def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
            return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])

    r = Reader(FakeIPC(), dict_set=FakeDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    # Two words, because a hit needs a *revisit*: hovering the word already hovered is not a second
    # lookup on the real input path — the cursor has to leave and come back for the cache to answer.
    r.tokens = [
        Token("本命", "本命", "ほんめい", "名詞", 0, 2),
        Token("読む", "読む", "よむ", "動詞", 2, 4),
    ]
    r.boxes = [WordBox(0, 100, 100, 40, 40), WordBox(1, 200, 100, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())  # keep our boxes

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        ui = Driver(r)
        ui.move_to_word(0)  # cache miss → render
        ui.move_to_word(1)  # cache miss → render
        ui.move_to_word(0)  # cache hit → no render
        snap = otel_metrics.snapshot()
        assert snap["saitenka.render.duration_ms"]["count"] == 2
        assert snap["saitenka.panel_cache.misses"]["value"] == 2
        assert snap["saitenka.panel_cache.hits"]["value"] == 1
    finally:
        otel_metrics.unregister()
        provider.shutdown()


class _FakeDS:
    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        from saitenka.panel import Definition, Entry

        return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])


def _reader_with_word(ipc):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = Reader(ipc, dict_set=_FakeDS(), pause_on_tooltip=True)
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    return r


def test_pause_on_tooltip_pauses_then_resumes(monkeypatch):
    ipc = FakeIPC()
    ipc.props["pause"] = False
    r = _reader_with_word(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())  # keep our boxes
    r._show_tooltip(0)  # tooltip shown → pause
    assert ("set_property", "pause", True) in ipc.commands
    r.hover = 0
    r.retire_hover()  # tooltip hidden → resume
    assert ("set_property", "pause", False) in ipc.commands


def test_set_hover_refuses_the_nothing_hovered_sentinel():
    """`set_hover` shows a word; "nothing hovered" is `retire_hover` and nothing else.

    The negative index used to forward to the teardown, which is how a caller that wanted only the
    teardown reached the build chain. Refusing it is the seam — a silent early-return would let the
    next caller re-introduce it.
    """
    r = _reader_with_word(FakeIPC())
    r.hover = 0
    with pytest.raises(ValueError, match="retire_hover"):
        r.set_hover(-1)


def test_pause_on_tooltip_respects_manual_pause():
    ipc = FakeIPC()
    ipc.props["pause"] = True  # user already paused
    r = _reader_with_word(ipc)
    Driver(r).move_to_word(0)
    assert not r.hover_view().paused  # never took ownership → won't resume


def test_hover_pause_toggle_releases_saitenka_owned_pause(monkeypatch):
    from saitenka.runtime import events

    ipc = FakeIPC()
    r = _reader_with_word(ipc)
    r._pause_store.dispatch(events.HoverPauseClaimed(paused=True))
    monkeypatch.setattr(r, "toast", lambda *_args: None)
    r.toggle_hover_pause()
    assert ("set_property", "pause", False) in ipc.commands


def test_hover_pause_toggle_changes_state_and_reports_it(monkeypatch):
    r = _reader_with_word(FakeIPC())
    messages = []
    monkeypatch.setattr(r, "toast", lambda text, _kind="ok": messages.append(text))
    r.toggle_hover_pause()
    assert (r.pause_on_tooltip, messages) == (False, ["hover auto-pause: off"])
    r.toggle_hover_pause()
    assert (r.pause_on_tooltip, messages) == (
        True,
        ["hover auto-pause: off", "hover auto-pause: on"],
    )


def test_hover_pause_toggle_preserves_external_pause(monkeypatch):
    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    monkeypatch.setattr(r, "toast", lambda *_args: None)
    r.toggle_hover_pause()
    assert ("set_property", "pause", False) not in ipc.commands


def test_hover_pause_toggle_disables_future_hover_pause(monkeypatch):
    ipc = FakeIPC()
    ipc.props["pause"] = False
    r = _reader_with_word(ipc)
    monkeypatch.setattr(r, "toast", lambda *_args: None)
    r.toggle_hover_pause()
    Driver(r).move_to_word(0)
    assert ("set_property", "pause", True) not in ipc.commands


def test_prefetch_queues_full_render_when_paused():
    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.sub_text = "本命"
    submitted = []
    r.prefetch_state.workers = 8
    r.prefetch_state.submitter = lambda **kwargs: submitted.append(kwargs) or True
    r._update_prefetch()
    queued = [entry["request"].item for entry in submitted]
    assert [i.token.surface for i in queued] == ["本命"]  # the content word got queued
    assert all(i.full for i in queued)  # engaged → a hover is imminent, full panel render


def test_prefetch_queues_cheap_warm_while_just_playing():
    """Not engaged (playing, mouse off the video) still queues the content word — as a cheap
    dict-only WARM (`full=False`), not the expensive full render. This is the idle time the video
    is only being watched/listened to: paying the JSON-decode cost here means a later hover (or the
    user pausing/mousing over) usually hits an already-warm `Dictionary._entry_cache`."""
    ipc = FakeIPC()
    ipc.props["pause"] = False  # playing, not engaged
    r = _reader_with_word(ipc)
    submitted = []
    r.prefetch_state.workers = 8
    r.prefetch_state.submitter = lambda **kwargs: submitted.append(kwargs) or True
    g0 = r.prefetch_state.gen
    r._update_prefetch()
    assert r.prefetch_state.gen == g0 + 1  # bumped → in-flight work is invalidated
    item = submitted[0]["request"].item
    assert item.token.surface == "本命"
    assert item.full is False  # idle-time warm only, no layout/drawing


def test_prefetch_worker_warms_cache_then_close_joins():
    ipc = RuntimeFakeIPC()
    gateway = runtime_gateway(ipc)
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.start_prefetch()
    try:
        r._update_prefetch()  # queue 本命 for the worker
        key = PanelKey(
            lemma="本命",
            surface="本命",
            reading="ほんめい",
            inflected="本命",
            width=r.tip_scale.width,
            anki_ok=False,  # no anki configured
            mined=False,
        )
        await_ready(lambda: key in r.tip.panel_cache, "the prefetch never warmed the panel")
        assert key in r.tip.panel_cache  # prefetched in the background, no hover needed
    finally:
        r.close()
        gateway.close()


def test_hover_off_window_still_lingers(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["x"]
    r.hover = 0
    monkeypatch.setattr(r, "set_hover", lambda i: setattr(r, "hover", i))
    Driver(r, instant=False).leave()  # cursor left the window
    assert r.hover == 0 and r.hover_view().tip.hide_pending  # scheduled, not instant


class _TallDS:
    """A dictionary entry far taller than one viewport — several long def bodies."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        from saitenka.panel import Definition, Entry

        para = "とても長い定義の本文でありスクロールが必要になるほど縦に伸びます。" * 6
        return Entry(
            headword=tok.surface,
            reading="ほんめい",
            defs=[Definition(f"辞書{i}", [para]) for i in range(6)],
        )

    def has_term(self, *_forms):
        return True  # the def body is all dictionary words → a body click doesn't fall to kanji


def _tall_reader(ipc):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = Reader(ipc, dict_set=_TallDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def test_show_tooltip_renders_only_the_head_then_grows_on_scroll(monkeypatch):
    # Viewport-first, windowed: a tall entry measures only the head that fills the viewport on show;
    # the windowed engine composites (and measures) the deferred tail as the user scrolls down.
    r = _tall_reader(FakeIPC())
    monkeypatch.setattr(r, "renderer", NullRenderer())
    Driver(r).move_to_word(0)
    wp = r.tip.view.state.windowed
    assert wp.measured < wp.count  # head only — the whole tall panel was NOT rendered up front
    assert r.tip.view.view_h >= r.tip_scale.cap - 1  # …but the viewport is fully covered
    assert (
        r.hover_view().tip.state.full_height >= r.tip.view.view_h
    )  # estimate at least fills the viewport
    before = wp.measured
    r.scroll_tip(r.tip.view.state.full_height)  # wheel toward the bottom
    assert wp.measured > before  # scrolling measured more blocks (the deferred tail)


def _add_button_center(r) -> tuple[float, float]:
    """Screen coords of the ⊕ in the card header."""
    from saitenka.panel import header_add_rect

    px, py, pw, ph = header_add_rect(r.tip_scale.width)
    sx, sy = r.tip.view.xy
    return sx + px + pw / 2, sy + (py - r.tip.view.scroll) + ph / 2


def _point_at_add_button(r) -> Driver:
    """Move onto the ⊕; return the driver so the click follows the cursor."""
    return Driver(r, instant=False).move(*_add_button_center(r))


@pytest.mark.usefixtures("anki_up")  # the ⊕ button only draws when AnkiConnect is reachable
def test_header_add_button_click_mines_hovered_word(monkeypatch):
    ipc = FakeIPC()
    r = _tall_reader(ipc)
    r.anki = object()  # mining available → ⊕ drawn and hit-testable
    r._anki_capability = SimpleNamespace(value=True, request=lambda: False)
    r._tts_ok = True
    r.hover = 0
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r._show_tooltip(0)
    events = []
    monkeypatch.setattr(r, "mine_current", lambda: events.append("mine"))
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    _point_at_add_button(r).click()
    assert events == ["mine"]  # ⊕ mined; did not fall through to TTS


def test_tooltip_empty_click_does_nothing(monkeypatch):
    # clicking an empty area of the card must NOT play audio (the 🔊 button is the only play affordance)
    ipc = FakeIPC()
    r = _tall_reader(ipc)
    r.anki = object()
    r.hover = 0
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r._show_tooltip(0)
    events = []
    monkeypatch.setattr(r, "mine_current", lambda: events.append("mine"))
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    tx, ty, tw, th = r.tip.view.rect
    Driver(r, instant=False).move(tx + tw / 2, ty + th - 5).click()  # low in the body
    assert events == []  # neither speaks nor mines


def test_tooltip_speaker_button_click_speaks(monkeypatch):
    from saitenka.panel import header_speaker_rect

    ipc = FakeIPC()
    r = _tall_reader(ipc)
    r._tts_ok = True
    r.hover = 0
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r._show_tooltip(0)
    events = []
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    px, py, pw, ph = header_speaker_rect(r.tip_scale.width)
    sx, sy = r.tip.view.xy
    Driver(r, instant=False).move(sx + px + pw / 2, sy + (py - r.tip.view.scroll) + ph / 2).click()
    assert events == ["speak"]  # only the 🔊 button plays audio


def test_header_add_button_absent_without_anki(monkeypatch):
    ipc = FakeIPC()
    r = _tall_reader(ipc)  # no anki
    r.hover = 0
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r._show_tooltip(0)
    cx, cy = _add_button_center(r)
    assert not tooltip.hit_header_add(
        tooltip.chrome_for(r.tip.view, scale=r.tip_scale, style=r.panel_style), cx, cy
    )  # no ⊕ button when mining is unavailable


# --- R4: nested scanning (hover a word inside the tooltip) ------------------------------------------


class _ScanDS:
    """A dictionary entry with a CJK (monolingual) body, so the panel carries scan hitboxes."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        from saitenka.panel import Definition, Entry

        return Entry(
            headword=tok.surface,
            reading="ほんめい",
            defs=[Definition("MonoC", ["追いかけること。また、その人。"])],
        )


def _scan_reader(ipc):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = Reader(ipc, dict_set=_ScanDS(), scan_delay=0.0)  # open immediately; dwell has its own tests
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def _fire_dwell(ipc, kind: str) -> bool:
    """Deliver one hover dwell deadline.

    These used to resolve inside `_update_hover` by comparing a monkeypatched clock. They are named
    deadlines now, so a zero delay is still a timer and has to be delivered — and a *late* one is
    expressible, which the clock version could not represent at all.
    """
    return ipc.fire_runtime_timer(f"lifecycle:{kind}")


def _hover_base_word(r) -> Driver:
    """Hover the subtitle word and return the driver the rest of the test drives.

    `move_to_word` hit-tests the cursor against the word's box, which `set_hover(0)` never did — a
    test that pokes the index proves the tooltip builds, never that a cursor over that word gets it.
    """
    return Driver(r, instant=False).move_to_word(0)


def _hover_first_scan_cell(r):
    """Arrive on the first scan cell of the base tooltip; return the ScanBox.

    The move goes through `Driver`, so it is mpv's `mouse-pos` read by the real `_update_hover` —
    the path a cursor takes. `instant=False` because these tests deliver their own dwells; an
    arrival that also fired them could not express "just arrived, nothing opens yet".
    """
    sb = r.tip.view.state.windowed.scan_boxes()[0]
    sx, sy = r.tip.view.xy
    Driver(r, instant=False).move(sx + sb.x + sb.w / 2, sy + (sb.y - r.tip.view.scroll) + sb.h / 2)
    return sb


def test_scan_hit_maps_cursor_to_inner_char(monkeypatch):
    r = _scan_reader(FakeIPC())
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.hover = 0
    r._show_tooltip(0)
    boxes = r.tip.view.state.windowed.scan_boxes()
    assert boxes
    sb = boxes[0]
    sx, sy = r.tip.view.xy
    hit = tooltip_panel.scan_hit(
        r.tip, r.tip_scale.raster, sx + sb.x + sb.w / 2, sy + sb.y + sb.h / 2
    )
    assert hit is not None and hit.text.startswith("追")


def _tall_nested_reader(ipc):
    """Scan reader whose inner-word lookup returns a TALL entry, so the nested popup must scroll."""
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = Reader(ipc, dict_set=_TallDS(), scan_delay=0.0)
    r.osd = (3840, 2160)  # 4K → the hi-dpi native compose path the report was captured on
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def test_nested_popup_scroll_reaches_the_bottom(monkeypatch):
    # Regression (report 20260805): the nested popup wouldn't scroll past ~the middle — its clamp read a
    # full_height that never converged, because the OLD crisp path composited a SEPARATE native panel
    # while the reference panel it clamped against stayed head-only. The one-panel scale-boundary arch
    # composites the SAME panel on every scroll, so each notch measures more of the tail (full_height
    # grows) and the wheel reaches the true bottom. Driven at 4K to exercise the native compose path.
    r = _tall_nested_reader(FakeIPC())
    monkeypatch.setattr(r, "renderer", NullRenderer())
    tok = r.tokens[0]
    nested_popup.open_nested(
        r.tip_ports, r.panel_ports, tok, tok.surface, nested_popup.Anchor(300.0, 2000.0, 40.0)
    )  # anchor low → nested_view_h keeps full height
    st = r.tip.nest.state
    assert st is not None
    assert st.windowed.measured < st.windowed.count  # head only on open — the tall tail is deferred
    # Wheel toward the bottom until the clamp stops moving; each notch grows the converging estimate.
    prev = -1
    for _ in range(200):
        tooltip_panel.scroll_view(r.tip_ports, r.tip.nest, 10_000)
        if r.tip.nest.scroll == prev:
            break
        prev = r.tip.nest.scroll
    assert (
        st.windowed.measured == st.windowed.count
    )  # whole panel measured — the estimate never froze
    assert r.tip.nest.scroll == max(
        0, st.full_height - r.tip.nest.view_h
    )  # reached the true bottom


def test_tooltip_geometry_is_resolution_independent():
    # The tooltip renders at the 1920×1080 REFERENCE, so its geometry — and thus the render cache key —
    # is IDENTICAL at 1080p and 4K. Only tip_scale.display changes, so a 1080p prewarm hits at any
    # playback resolution.
    r = _scan_reader(FakeIPC())
    r.osd = (1920, 1080)
    w_ref, cap_ref, scale_ref = r.tip_scale.width, r.tip_scale.cap, r.tip_scale.display
    r.osd = (3840, 2160)
    assert (r.tip_scale.width, r.tip_scale.cap) == (
        w_ref,
        cap_ref,
    )  # geometry unchanged by resolution
    assert scale_ref == 1.0 and r.tip_scale.display == 2.0  # only the DISPLAY scale changes


def test_tooltip_geometry_ignores_ui_scale():
    # The tooltip is a VIDEO-OVERLAY element: it tracks the vertical viewport (osd_h) via the display
    # scale, NOT the app-chrome ui_scale (its fonts are theme scale 1.0, so width must stay 1.0 too).
    # Regression: ui_scale × resolution once compounded to a too-wide tooltip on a hi-dpi screen.
    from saitenka.app.config import PanelOptions, ReaderOptions, TooltipOptions

    r = Reader(
        FakeIPC(),
        dict_set=_ScanDS(),
        options=ReaderOptions(
            panels=PanelOptions(scale=1.5), tooltip=TooltipOptions(tip_max_frac=0.5)
        ),
    )
    assert r.ui_scale == 1.5  # a large interface scale (for the sidebar / help / analysis panels)
    r.osd = (1920, 1080)
    assert r.tip_scale.width == 640  # reference width, NOT 640 × 1.5
    r.osd = (3840, 2160)  # 4K → displayed width tracks the vertical viewport, not ui_scale
    assert (
        r.tip_scale.width == 640 and r.tip_scale.display == 2.0
    )  # displayed ≈ 1280px, not 1920 (the bug)


class _NoThread:  # stand-in for threading.Thread so a test doesn't leak a daemon crisp worker
    def __init__(self, *a, **k) -> None:
        pass

    def start(self) -> None:
        pass


def test_scan_hit_round_trips_through_the_display_scale(monkeypatch):
    # At 4K the composited tooltip is upscaled 2×; a click at a scan cell's DISPLAYED centre must invert
    # that scale and still land on the cell (the hit-test must undo the upload's upscale).
    r = _scan_reader(FakeIPC())
    r.osd = (3840, 2160)  # scale 2.0
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.hover = 0
    r._show_tooltip(0)
    boxes = r.tip.view.state.windowed.scan_boxes()
    assert boxes
    sb = boxes[0]
    s = r.tip_scale.display
    assert s == 2.0
    sx, sy = r.tip.view.xy
    hit = tooltip_panel.scan_hit(
        r.tip,
        r.tip_scale.raster,
        sx + (sb.x + sb.w / 2) * s,
        sy + (sb.y + sb.h / 2 - r.tip.view.scroll) * s,
    )
    assert hit is not None and hit.text == sb.text


def test_hover_inner_word_opens_nested_popup(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)  # base tooltip on the subtitle word
    _hover_first_scan_cell(r)
    _fire_dwell(ipc, "scan-open")
    assert r.hover_view().nested.state is not None  # a nested popup opened…
    assert r.hover_view().nested.rect is not None
    assert r.hover_view().nested.word.startswith("追")  # …for the inner word under the cursor


def test_nested_scan_waits_for_dwell(monkeypatch):
    """Arriving on a cell arms the dwell; only its due event opens the popup. Re-arriving on the
    same cell must not re-arm, or a cursor jittering inside one cell would never settle."""
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.scan_delay = 0.25  # require the cursor to settle before opening
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    _hover_first_scan_cell(r)
    assert r.hover_view().nested.state is None  # just arrived — nothing opens yet
    _hover_first_scan_cell(r)  # re-arriving on the same cell must not re-arm
    assert r.hover_view().nested.state is None  # still settling on the same cell

    assert _fire_dwell(ipc, "scan-open")  # the cursor rested it out

    assert r.hover_view().nested.state is not None


def test_nested_scan_dwell_restarts_when_cursor_moves(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.scan_delay = 0.25
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    boxes = r.tip.view.state.windowed.scan_boxes()
    sx, sy = r.tip.view.xy

    ui = Driver(r, instant=False)

    def hover(sb):
        ui.move(sx + sb.x + sb.w / 2, sy + sb.y + sb.h / 2)

    hover(boxes[0])
    assert r.hover_view().scan_target == boxes[0].text
    hover(boxes[1])  # drift to a different cell before the dwell elapses

    assert r.hover_view().scan_target == boxes[1].text  # the dwell restarted on the new cell
    assert r.hover_view().nested.state is None  # no popup fired mid-drift
    assert _fire_dwell(ipc, "scan-open")  # and when it does elapse, it opens the NEW cell
    assert r.hover_view().nested.state is not None


def test_switch_base_word_drops_nested(monkeypatch):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.tokens = [
        Token("本命", "本命", "ほんめい", "名詞", 0, 2),
        Token("読む", "読む", "よむ", "動詞", 2, 4),
    ]
    r.boxes = [WordBox(0, 100, 300, 40, 40), WordBox(1, 500, 300, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    _hover_first_scan_cell(r)
    _fire_dwell(ipc, "scan-open")
    assert r.hover_view().nested.state is not None
    # a real switch: the cursor moves to the other word and its switch dwell comes due
    Driver(r).move_to_word(1)
    assert r.hover_view().nested.state is None  # the stale scan popup is dropped


def test_nested_lingers_then_dismisses(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])
    _hover_base_word(r)
    _hover_first_scan_cell(r)
    _fire_dwell(ipc, "scan-open")
    assert r.hover_view().nested.state is not None
    Driver(r, instant=False).move(5, 5)  # leave the whole stack
    assert r._hover_store.current.hysteresis.nest_hide_pending  # scheduled, not instant

    assert _fire_dwell(ipc, "nested-hide")

    assert r.hover_view().nested.state is None  # dismissed after the linger


@pytest.mark.usefixtures("anki_up")  # the ⊕ button only draws when AnkiConnect is reachable
def test_nested_add_button_mines_inner_word(monkeypatch):
    from saitenka.panel import header_add_rect

    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.anki = object()
    r._anki_capability = SimpleNamespace(value=True, request=lambda: False)
    r._tts_ok = True
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    _hover_first_scan_cell(r)
    _fire_dwell(ipc, "scan-open")
    assert r.hover_view().nested.token is not None
    mined = []
    monkeypatch.setattr(r, "_mine_token", lambda tok: mined.append(tok.surface))
    px, py, pw, ph = header_add_rect(r.tip_scale.width)
    nx, ny = r.tip.nest.xy
    Driver(r, instant=False).move(nx + px + pw / 2, ny + (py - r.tip.nest.scroll) + ph / 2).click()
    assert mined and mined[0].startswith("追")  # ⊕ mined the scanned inner word


# --- R4b: clickable cross-reference links (open the target term in the nested popup) ---------------


class _LinkDS:
    """A dictionary entry whose def body contains an internal <a> cross-reference to 見る."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        from saitenka.panel import Definition, Entry

        body = ["同義語は", {"tag": "a", "href": "?query=見る", "content": "見る"}, "。"]
        return Entry(headword=tok.surface, reading="みる", defs=[Definition("MonoA", body)])

    def kanji_for(self, _ch):
        return None  # this fixture has no kanji bank — a header-kanji click is a graceful no-op


def _link_reader(ipc):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = Reader(ipc, dict_set=_LinkDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("観る", "観る", "みる", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def _point_at_link(r) -> Driver:
    """Move onto the body cross-reference link; return the driver so the click follows the cursor.

    The header's per-kanji `kanji:` links sit first and are skipped — this is the body link.
    """
    lb = next(b for b in r.tip.view.state.windowed.link_boxes() if not b.query.startswith("kanji:"))
    sx, sy = r.tip.view.xy
    return Driver(r, instant=False).move(
        sx + lb.x + lb.w / 2, sy + (lb.y - r.tip.view.scroll) + lb.h / 2
    )


def test_click_cross_reference_navigates_base_in_place(monkeypatch):
    # Yomitan historyMode:new — a cross-reference click REPLACES the base tooltip content in place and
    # pushes the previous view for back, instead of spawning a fragile floating nested popup.
    from saitenka.app import tooltip

    ipc = FakeIPC()
    r = _link_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    assert r.hover_view().tip.state.windowed.link_boxes()  # the def body exposed a clickable link
    base = r.tip.view.state
    _point_at_link(r).click()
    assert r.hover_view().nested.state is None  # NOT a nested popup
    assert r.hover_view().tip.state is not None and r.hover_view().tip.state is not base
    assert tooltip.tip_back(r.tip_ports) is True and r.hover_view().tip.state is base
    assert tooltip.tip_back(r.tip_ports) is False


class _WildcardDS:
    """A def body whose cross-reference is a WILDCARD, plus a search() that returns clickable results."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        from saitenka.panel import Definition, Entry

        body = ["類語は", {"tag": "a", "href": "?query=食べ*", "content": "食べ…"}, "など。"]
        return Entry(headword=tok.surface, reading="みる", defs=[Definition("MonoA", body)])

    def search(self, pattern, _limit=30):
        from saitenka.panel import Definition, Entry

        li = [
            {"tag": "li", "content": [{"tag": "a", "href": "?query=食べる", "content": "食べる"}]}
        ]
        return Entry(
            headword=[pattern], defs=[Definition(f"検索 {pattern}", [{"tag": "ul", "content": li}])]
        )


def test_click_wildcard_link_navigates_base_to_search_results(monkeypatch):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    ipc = FakeIPC()
    r = Reader(ipc, dict_set=_WildcardDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("観る", "観る", "みる", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    # the wildcard cross-ref in the body (skip the header's per-kanji `kanji:` links)
    lb = next(b for b in r.tip.view.state.windowed.link_boxes() if not b.query.startswith("kanji:"))
    assert "*" in lb.query  # the cross-ref is a wildcard pattern
    sx, sy = r.tip.view.xy
    Driver(r, instant=False).move(
        sx + lb.x + lb.w / 2, sy + (lb.y - r.tip.view.scroll) + lb.h / 2
    ).click()
    # A wildcard cross-ref navigates the BASE tooltip to the search-results page, in place.
    assert r.hover_view().nested.state is None
    assert len(r.interaction.tip_nav.back) == 1
    results = [
        b for b in r.tip.view.state.windowed.link_boxes() if not b.query.startswith("kanji:")
    ]
    assert (
        results[0].query == "食べる"
    )  # the base now shows results, each drilling into an exact term


def test_external_link_is_not_a_clickable_region(monkeypatch):
    # an external source link (Bilingual 'JMdict') is styled blue but captures NO LinkBox → inert
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    ipc = FakeIPC()

    class _ExternalDS:
        def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
            from saitenka.panel import Definition, Entry

            body = [
                "出典 ",
                {"tag": "a", "href": "https://www.edrdg.org/x?q=1", "content": "JMdict"},
            ]
            return Entry(headword=tok.surface, reading="みる", defs=[Definition("Bilingual", body)])

    r = Reader(ipc, dict_set=_ExternalDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("観る", "観る", "みる", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    body_links = [
        b
        for b in r.hover_view().tip.state.windowed.link_boxes()
        if not b.query.startswith("kanji:")
    ]
    assert body_links == []  # external link → no clickable body region (header kanji links aside)


class _RubyLinkDS:
    """A def body whose cross-reference target carries furigana (a ruby'd <a>) — the 思し召し-in-考え
    case. It must still be a clickable link, not merely blue-styled hover-scan text."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        from saitenka.panel import Definition, Entry

        ref = {
            "tag": "a",
            "href": "?query=思し召し",
            "content": {
                "tag": "ruby",
                "content": [
                    {"tag": "rb", "content": "思し召し"},
                    {"tag": "rt", "content": "おぼしめし"},
                ],
            },
        }
        return Entry(
            headword=tok.surface,
            reading="かんがえ",
            defs=[Definition("MonoA", ["敬語は", ref, "。"])],
        )


def test_ruby_furigana_cross_reference_is_clickable(monkeypatch):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    ipc = FakeIPC()
    r = Reader(ipc, dict_set=_RubyLinkDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("考え", "考え", "かんがえ", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    links = r.tip.view.state.windowed.link_boxes()
    assert any(lb.query == "思し召し" for lb in links)  # the furigana'd cross-ref IS clickable


def test_nested_popup_shrinks_to_stay_above_inner_word():
    r = Reader(FakeIPC())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    margin = max(16, round(1080 * 0.05))  # reference-height margin (cap_for uses REF_H × ui_scale)
    # a TALL entry anchored to an inner word in the upper-middle: default would drop below (more room
    # below), but the nested popup shrinks its viewport to the room above and stays ABOVE the word.
    wy = 220
    view_h = nested_popup.nested_view_h(800, wy, osd_h=1080, max_frac=r.nested_max_frac)
    above_room = wy - nested_popup.TIP_GAP - margin
    assert view_h == above_room  # shrunk to fit above
    _, ty = tooltip_panel.place_panel(
        300, 100, wy, 40, view_h, scale=r.tip_scale.display, osd=r.osd
    )
    assert ty + view_h <= wy  # …so it sits entirely above the inner word


def test_nested_popup_drops_below_when_no_room_above():
    r = Reader(FakeIPC())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    wy = 90  # inner word near the very top → can't fit above
    view_h = nested_popup.nested_view_h(800, wy, osd_h=1080, max_frac=r.nested_max_frac)
    _, ty = tooltip_panel.place_panel(
        300, 100, wy, 40, view_h, scale=r.tip_scale.display, osd=r.osd
    )
    assert ty >= wy  # falls back to below (safe)


def test_hover_over_link_does_not_open_scan_popup(monkeypatch):
    # links are click-to-open, not hover-scan → scrolling/reading over a cross-ref doesn't clutter
    ipc = FakeIPC()
    r = _link_reader(ipc)
    r.scan_delay = 0.0  # would fire immediately if not suppressed
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    ui = _point_at_link(r)  # cursor on the link cell
    assert r.hover_view().nested.state is None  # hover did NOT open a scan popup over the link
    ui.click()  # …a click navigates the base in place (no floating popup)
    assert r.hover_view().nested.state is None and len(r.interaction.tip_nav.back) == 1


def test_scroll_resets_scan_dwell(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.scan_delay = 0.25
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    _hover_first_scan_cell(r)
    _fire_dwell(ipc, "scan-open")
    assert r.hover_view().scan_target is not None  # a scan target is settling
    r.tip.view.view_h = 20  # make the panel scrollable
    Driver(r, instant=False).wheel(1)  # scrolling the panel…
    assert r.hover_view().scan_target is None  # …restarts the dwell so no popup fires mid-scroll


def test_click_link_does_not_mine_or_speak(monkeypatch):
    # a link click must open the target, not fall through to mining / TTS
    ipc = FakeIPC()
    r = _link_reader(ipc)
    r.anki = object()
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    events = []
    monkeypatch.setattr(r, "mine_current", lambda: events.append("mine"))
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    _point_at_link(r).click()
    assert (
        events == [] and len(r.interaction.tip_nav.back) == 1
    )  # navigated the base, no mine/speak fallthrough


# --- copy: Shift+C (whole line) and right-click (word under cursor) + highlight flash ---------------


def test_copy_line_copies_all_lines(monkeypatch):
    from saitenka.app.tokenize import Token

    r = _scan_reader(FakeIPC())
    r.lines = [
        [Token("本命", "本命", "ほんめい", "名詞", 0, 2)],
        [Token("読む", "読む", "よむ", "動詞", 0, 2)],
    ]
    from saitenka.app import subtitle_adapter

    got = []
    monkeypatch.setattr(subtitle_adapter, "copy_clipboard", lambda s: got.append(s))
    r.copy_line()
    assert got == ["本命\n読む"]  # the whole cue, line by line


def test_right_click_copies_hovered_word_and_flashes(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    got = []
    monkeypatch.setattr(tooltip, "copy_clipboard", lambda s: got.append(s))
    tx, ty, tw, _th = r.tip.view.rect
    Driver(r, instant=False).move(tx + tw / 2, ty + 5).right_click()  # header, not a scan cell
    assert got and "本命" in got[0]  # copied the hovered word
    assert r.interaction.copy_pulse.overlay == C.TIP_ID


def test_right_click_on_nested_copies_inner_word(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    _hover_first_scan_cell(r)
    _fire_dwell(ipc, "scan-open")  # open the nested popup
    got = []
    monkeypatch.setattr(tooltip, "copy_clipboard", lambda s: got.append(s))
    nx, ny, nw, nh = r.tip.nest.rect
    Driver(r, instant=False).move(nx + nw / 2, ny + nh / 2).right_click()
    assert got and got[0].startswith("追")  # copied the inner scanned word
    assert r.interaction.copy_pulse.overlay == C.NESTED_ID


def test_flash_border_drawn_then_cleared(monkeypatch):
    """The pulse is retired by its own named deadline, not by a tick noticing a wall clock passed.
    Firing the timer is the whole act — nothing polls, so a poll loop would prove nothing."""
    import numpy as np

    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    monkeypatch.setattr(tooltip, "copy_clipboard", lambda _s: None)
    _hover_base_word(r)
    shots = []
    monkeypatch.setattr(
        r.ov, "show_bgra", lambda bgra, _x, _y, oid: shots.append((oid, bgra.copy()))
    )
    tx, ty, tw, _th = r.tip.view.rect
    Driver(r, instant=False).move(tx + tw / 2, ty + 5).right_click()
    oid, view = shots[-1]
    hl = np.array(tooltip_panel.FLASH_BGRA, np.uint8)
    assert oid == C.TIP_ID and (view[0] == hl).all()  # top border row is the highlight

    assert ipc.fire_runtime_timer("lifecycle:flash-expiry")  # redraw without the border

    _, view2 = shots[-1]
    assert not (view2[0] == hl).all()


def test_a_second_copy_flash_supersedes_the_first_deadline(monkeypatch):
    """Two copies in quick succession arm the pulse twice, and the first deadline is still out
    there. Without the revision fence it lands during the second pulse and cuts it short."""
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    monkeypatch.setattr(tooltip, "copy_clipboard", lambda _s: None)
    _hover_base_word(r)
    tx, ty, tw, _th = r.tip.view.rect
    ui = Driver(r, instant=False).move(tx + tw / 2, ty + 5).right_click()
    stale = ipc.timers["lifecycle:flash-expiry"]

    ui.right_click()  # arms a second pulse; the first deadline is now stale

    assert ipc.timers["lifecycle:flash-expiry"] != stale
    assert ipc.fire_runtime_timer("lifecycle:flash-expiry")
    assert (
        r.interaction.copy_pulse.overlay is None
    )  # the latest due event, and only it, retired the pulse


def test_closing_retires_a_pending_copy_flash(monkeypatch):
    """A deadline that outlives its session redraws a popup onto a torn-down surface."""
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    monkeypatch.setattr(tooltip, "copy_clipboard", lambda _s: None)
    _hover_base_word(r)
    tx, ty, tw, _th = r.tip.view.rect
    Driver(r, instant=False).move(tx + tw / 2, ty + 5).right_click()

    r.close()

    assert not r.schedule_flash_expiry()  # and nothing can arm a new one behind the close


# --- card preview: click-to-play audio, image zoom toggle, ✕ close, and the ⊕→✓ mined state --------


def _preview_reader(ipc, *, with_audio=True, with_image=True):
    from PIL import Image as PILImage

    from saitenka.app.card_preview import PreviewData

    r = Reader(ipc)
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    frame = PILImage.new("RGBA", (320, 180), (40, 70, 90, 255)) if with_image else None
    pv = PreviewData(
        "mined",
        "本",
        "ほん",
        ["本を読む"],
        "本",
        ["book"],
        frame,
        3.9 if with_audio else None,
        "Saitenka::Mining · Lapis",
    )
    miner_ui.show_preview(r.preview_ports, pv, "/tmp/a.mp3" if with_audio else None)
    return r


def _point_at(r, rect) -> Driver:
    """Move onto the centre of a preview-panel rect; return the driver so the click follows."""
    x, y, w, h = rect
    return Driver(r, instant=False).move(x + w / 2, y + h / 2)


def test_preview_does_not_autoplay(monkeypatch):
    played = []
    monkeypatch.setattr(miner_ui, "play_audio", lambda p: played.append(p))
    _preview_reader(FakeIPC())
    assert played == []  # showing the preview no longer autoplays


def test_no_mine_preview_suppresses_panel_and_toasts_instead(monkeypatch):
    from types import SimpleNamespace

    from saitenka.app.config import MiningOptions, ReaderOptions

    r = Reader(FakeIPC(), options=ReaderOptions(mining=MiningOptions(show_preview=False)))
    shown, toasts = [], []
    monkeypatch.setattr(miner_ui, "preview_mined", lambda *a, **_k: shown.append(a))
    monkeypatch.setattr(miner_ui, "preview_existing", lambda *a, **_k: shown.append(a))
    monkeypatch.setattr(r, "toast", lambda text, *_a: toasts.append(text))

    r._preview_mined(SimpleNamespace(expression="本命"), None, None)
    r._preview_existing(42, SimpleNamespace(expression="読む"), "exists")

    assert shown == []  # panel never rendered
    assert toasts == ["mined 本命", "already have 読む"]  # terse confirmation replaces it


def test_mine_preview_default_pops_the_panel(monkeypatch):
    from types import SimpleNamespace

    r = Reader(FakeIPC())  # default options → show_preview True
    calls = []
    monkeypatch.setattr(miner_ui, "preview_mined", lambda *a, **_k: calls.append(a))
    r._preview_mined(SimpleNamespace(expression="本命"), None, None)
    assert len(calls) == 1  # the verify-panel path is taken by default


def test_preview_audio_button_plays_on_click(monkeypatch):
    played = []
    monkeypatch.setattr(miner_ui, "play_audio", lambda p: played.append(p))
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    _point_at(r, r.interaction.preview_panel.audio_rect).click()
    assert played == ["/tmp/a.mp3"]  # ▶ button plays the mined clip


def test_preview_empty_click_plays_nothing(monkeypatch):
    played = []
    monkeypatch.setattr(miner_ui, "play_audio", lambda p: played.append(p))
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    px, py, _pw, ph = r.interaction.preview_panel.rect
    Driver(r, instant=False).move(px + 6, py + ph - 6).click()  # empty body
    assert played == []


def test_preview_image_click_toggles_zoom():
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    assert not r.interaction.preview.zoom
    _point_at(r, r.interaction.preview_panel.image_rect).click()
    assert r.interaction.preview.zoom  # click screenshot → enlarge
    # the (bigger) image moved — re-read its rect
    _point_at(r, r.interaction.preview_panel.image_rect).click()
    assert not r.interaction.preview.zoom  # click again → back


def test_preview_close_button_dismisses():
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    _point_at(r, r.interaction.preview_panel.close_rect).click()
    assert r.interaction.preview_panel.rect is None and not r.interaction.preview.open


def test_new_cue_dismisses_preview(monkeypatch):
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.set_subtitle("別の字幕")  # a new subtitle cue
    assert r.interaction.preview_panel.rect is None


def test_mark_mined_flips_hovered_tooltip_to_check(monkeypatch):
    from saitenka.app.lookup import card_for

    ipc = FakeIPC()
    r = _scan_reader(ipc)  # dict_set present
    r.anki = object()
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    assert r.hover_view().tip.key.mined is False  # not mined yet → ⊕
    r._mark_mined(card_for(r.tokens[0]).expression)
    assert r.hover_view().tip.key.mined is True  # tooltip rebuilt with ✓


def test_seed_mined_preloads_deck_expressions():
    # a word mined in a past session (already in the deck) should be pre-marked so ⊕ shows ✓
    class FakeAnki:
        def find_notes(self, _query):
            return [11, 22]

        def notes_info(self, _ids):
            return [
                {"fields": {"Expression": {"value": "奉書"}}},
                {"fields": {"Expression": {"value": "<b>通り</b>"}}},
            ]

    from saitenka.app.anki import MineConfig

    r = Reader(FakeIPC(), anki=FakeAnki(), mine_cfg=MineConfig())
    r._seed_mined()
    assert r.session.mined == {"奉書", "通り"}  # HTML stripped; both pre-marked


# --- N4: auto-reveal the translation on hover (opt-in) ---------------------------------------------


def _auto_trans_reader(ipc):
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    ipc.props["secondary-sub-text"] = "I want you to read this."
    r = Reader(ipc, dict_set=_FakeDS(), auto_translate=True)
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    return r


def test_auto_translate_shows_on_hover_and_hides_on_leave(monkeypatch):
    ipc = FakeIPC()
    r = _auto_trans_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    shown = []
    hidden = []
    monkeypatch.setattr(
        r.lifecycle_surfaces, "present", lambda _img, *_a, oid=0, **_kw: shown.append(oid)
    )
    monkeypatch.setattr(r.lifecycle_surfaces, "remove", lambda oid, **_kw: hidden.append(oid))
    ui = _hover_base_word(r)
    assert OverlayId.TRANS in shown  # hovering a word auto-revealed the translation
    assert r._trans_text == "I want you to read this."
    ui.leave()  # the cursor leaves the video window…
    assert _fire_dwell(ipc, "tooltip-hide")  # …and the tip lingers until its hide dwell is due
    assert OverlayId.TRANS in hidden  # leaving the word hid it again


def test_no_auto_translate_without_the_flag(monkeypatch):
    ipc = FakeIPC()
    ipc.props["secondary-sub-text"] = "hidden"
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = Reader(ipc, dict_set=_FakeDS())  # flag off (default)
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    monkeypatch.setattr(r, "renderer", NullRenderer())
    shown = []
    monkeypatch.setattr(r.ov, "show", lambda _img, *_a, oid=0, **_kw: shown.append(oid))
    _hover_base_word(r)
    assert OverlayId.TRANS not in shown  # translation stays on the manual `t` key


def test_manual_toggle_overrides_auto_and_persists(monkeypatch):
    ipc = FakeIPC()
    r = _auto_trans_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.toggle_translation()  # force it ON with `t`
    assert r.translate_on and r.translation_visible()
    r.retire_hover()  # …and it stays even with nothing hovered
    assert r.translation_visible()


# --- JLPT pill on the tooltip (same signal as the subtitle underline) ------------------------------


def _jlpt_scorer(mapping):
    from saitenka.app.scoring import Scorer
    from saitenka.app.wordlists import JlptDict, KnownWords

    return Scorer(known=KnownWords.from_set([]), jlpt=JlptDict(dict(mapping)))


def test_jlpt_pill_matches_underline_color():
    from saitenka.app.scoring import Palette
    from saitenka.app.tokenize import Token

    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=_jlpt_scorer({"本命": "N2", "ほんめい": "N2"}))
    tok = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
    pill = tooltip_panel.jlpt_pill(tok, r.scorer)
    assert pill is not None and pill.name == "JLPT" and pill.value == "N2"
    assert pill.color == tooltip_panel._darken(
        Palette().jlpt["N2"]
    )  # hue tied to the underline level color


def test_jlpt_pill_leads_the_frequency_row():
    from saitenka.app.tokenize import Token

    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=_jlpt_scorer({"本命": "N2"}))
    entry = tooltip_panel.entry_for_tok(
        Token("本命", "本命", "ほんめい", "名詞", 0, 2), None, dict_set=r.dict_set, scorer=r.scorer
    )
    assert entry.freqs and entry.freqs[0].name == "JLPT" and entry.freqs[0].value == "N2"


def test_no_jlpt_pill_without_level_or_scorer():
    from saitenka.app.tokenize import Token

    tok = Token("犬", "犬", "いぬ", "名詞", 0, 1)
    # word not in the JLPT dict → no pill, frequency row untouched
    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=_jlpt_scorer({"本命": "N2"}))
    assert tooltip_panel.jlpt_pill(tok, r.scorer) is None
    assert tooltip_panel.entry_for_tok(tok, None, dict_set=r.dict_set, scorer=r.scorer).freqs == []
    # no scorer at all → no pill (coloring is optional)
    assert tooltip_panel.jlpt_pill(tok, Reader(FakeIPC(), dict_set=_FakeDS()).scorer) is None


def test_rareness_pill_blends_ranks_across_freq_dicts(tmp_path):
    """The blended pill's rank is the harmonic mean of the word's rank across every loaded freq dict,
    and it leads the frequency row (before the per-dict pills)."""
    import dicthelp

    from saitenka.app.fsrs import harmonic_of, rareness_color
    from saitenka.app.tokenize import Token

    fa = dicthelp.meta_zip(tmp_path / "fa.zip", "FreqA", "freq", [["猫", {"frequency": 1000}]])
    fb = dicthelp.meta_zip(tmp_path / "fb.zip", "FreqB", "freq", [["猫", {"frequency": 2000}]])
    ds = dicthelp.load_set(freq_zips=[fa, fb])
    r = Reader(FakeIPC(), dict_set=ds)
    tok = Token("猫", "猫", "ねこ", "名詞", 0, 1)
    pill = tooltip_panel.rareness_pill(tok, r.dict_set)
    assert pill is not None and pill.name == "diff"
    assert pill.color == rareness_color(harmonic_of([1000.0, 2000.0]))  # ≈1333 → common (green)


def test_rareness_pill_excludes_occurrence_based_dicts(tmp_path):
    """Only rank-based dicts may be blended. An occurrence-based dict (its count converts to a dense
    per-corpus rank of 1) would crush the harmonic mean if included — it must be skipped, so the pill
    reflects the rank-based dict alone."""
    import dicthelp

    from saitenka.app.tokenize import Token

    rank_z = dicthelp.meta_zip(tmp_path / "r.zip", "RankF", "freq", [["猫", {"frequency": 1500}]])
    occ_z = dicthelp.meta_zip(
        tmp_path / "o.zip", "OccF", "freq", [["猫", 99999]], frequency_mode="occurrence-based"
    )
    ds = dicthelp.load_set(freq_zips=[rank_z, occ_z])
    r = Reader(FakeIPC(), dict_set=ds)
    pill = tooltip_panel.rareness_pill(Token("猫", "猫", "ねこ", "名詞", 0, 1), r.dict_set)
    assert pill is not None and pill.value == "1.5k"  # blend of {1500} alone, not pulled toward 1


def test_no_rareness_pill_when_word_absent_from_all_freq_dicts(tmp_path):
    import dicthelp

    from saitenka.app.tokenize import Token

    fa = dicthelp.meta_zip(tmp_path / "fc.zip", "FreqC", "freq", [["猫", {"frequency": 1000}]])
    ds = dicthelp.load_set(freq_zips=[fa])
    r = Reader(FakeIPC(), dict_set=ds)
    assert (
        tooltip_panel.rareness_pill(
            Token("存在しない語", "存在しない語", "", "名詞", 0, 6), r.dict_set
        )
        is None
    )
    # no freq sources at all → no pill
    assert (
        tooltip_panel.rareness_pill(
            Token("猫", "猫", "ねこ", "名詞", 0, 1), Reader(FakeIPC(), dict_set=_FakeDS()).dict_set
        )
        is None
    )


def test_no_jlpt_pill_for_function_words_even_on_reading_collision():
    """Particles/aux (は, ね) share a bare-kana reading with N1 kanji words in the JLPT map. The pill
    must gate on content POS like the underline does, so は (助詞) gets NO pill even though its reading
    is present at N1 — otherwise every は/ね is mislabelled N1."""
    from saitenka.app.tokenize import Token

    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=_jlpt_scorer({"は": "N1", "ね": "N1"}))
    assert (
        tooltip_panel.jlpt_pill(Token("は", "は", "は", "助詞", 0, 1), r.scorer) is None
    )  # particle → no pill
    assert tooltip_panel.jlpt_pill(Token("ね", "ね", "ね", "助詞", 0, 1), r.scorer) is None
    # a real content word whose reading legitimately maps still gets its pill
    assert tooltip_panel.jlpt_pill(Token("葉", "葉", "は", "名詞", 0, 1), r.scorer) is not None


def test_jlpt_pill_suppressed_when_disabled():
    from saitenka.app.tokenize import Token

    sc = _jlpt_scorer({"本命": "N2"})
    sc.enable_jlpt = False
    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=sc)
    assert (
        tooltip_panel.jlpt_pill(Token("本命", "本命", "ほんめい", "名詞", 0, 2), r.scorer) is None
    )


# --- mined-card metadata: hierarchical tags + structured MiscInfo (rearrange-friendly) -------------

VIDEO = "/x/[Erai-raws] Nippon Sangoku - 10 [1080p AMZN WEBRip HEVC EAC3][MultiSub][189B848D].mkv"


def test_tag_slug_is_anki_safe():
    assert Reader._tag_slug("Nippon Sangoku") == "Nippon_Sangoku"  # no spaces in Anki tags
    assert Reader._tag_slug("  a b  c ") == "a_b_c"


def test_mine_tags_carry_source_and_episode():
    # No Reader: `mine_tags` reads nothing but the path. It only needed one while a delegation stood
    # in front of it.
    tags = miner.mine_tags(VIDEO)
    assert tags == ["saitenka::mined", "saitenka::source::Nippon_Sangoku", "saitenka::ep::10"]
    assert miner.mine_tags(None) == ["saitenka::mined"]  # no video → just the origin tag


# --- Stage 3: hygiene batch -----------------------------------------------------------------------


def test_bottom_margin_no_dead_code():
    """bottom_margin must not have unreachable code — verify it returns correctly."""
    r = Reader(FakeIPC())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    result = r.bottom_margin
    assert isinstance(result, int)
    assert result == round(
        1080 * r.bottom_margin_frac
    )  # subtitle margin is OSD-native (osd=1080 here)


def test_panel_cache_lru_eviction_not_wholesale_clear():
    """_panel_cache must evict the OLDEST entry (LRU) at its limit, not clear everything.
    After overflow, the most-recently-used entry must still be present."""
    from saitenka.app.tokenize import Token
    from saitenka.panel import Definition, Entry

    class _CountDS:
        def __init__(self):
            self.calls = 0

        def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
            self.calls += 1
            return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])

    r = Reader(FakeIPC(), dict_set=_CountDS())
    r.osd = (1920, 1080)  # REFERENCE res → tooltip scale 1.0 (geometry == display px)
    r.sub_origin = (0, 0)

    from saitenka.app.subtitles import WordBox

    # Fill the cache to exactly the limit + 1 to trigger eviction.
    # We'll manually insert sentinel keys to test LRU behaviour. Fill exactly to the cap so the next
    # insert (the real tooltip below) triggers a single eviction of the oldest.
    sentinel = object()
    for i in range(r.panel_cache_max):
        r.tip.panel_cache.setdefault(f"key_{i}", sentinel)
    tok = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    r.tokens = [tok]
    Driver(r).move_to_word(0)
    # the most-recently inserted sentinel survives; the oldest (key_0) is evicted, not the whole cache.
    assert f"key_{r.panel_cache_max - 1}" in r.tip.panel_cache, (
        "LRU eviction removed recently-used entry"
    )
    assert "key_0" not in r.tip.panel_cache, "LRU eviction should have removed oldest entry"


def test_close_cleans_up_tmp_dir():
    """Reader.close() must remove the mkdtemp directory it created."""
    r = Reader(FakeIPC())
    tmp = r._tmp
    assert tmp.exists()
    r.close()
    assert not tmp.exists(), f"tmp dir {tmp} not cleaned up by close()"


def test_capture_media_failure_shows_toast(monkeypatch):
    """If both screenshot and audio fail, _capture_media must show a warn toast, not be silent."""
    from saitenka.app.anki import MineConfig

    ipc = FakeIPC()
    r = _reader_with_word(ipc)
    r.sub_text = "本命"
    # A deck to mine into: capture runs off the mining value, which a session without one never builds.
    r.anki, r.mine_cfg = object(), MineConfig()

    # Patch screenshot and clip_audio to always raise (capture lives in app/miner.py since 8d).
    import saitenka.app.miner as _M

    monkeypatch.setattr(_M, "screenshot", lambda *_a: (_ for _ in ()).throw(OSError("snap failed")))
    monkeypatch.setattr(_M, "clip_audio", lambda *_a: (_ for _ in ()).throw(OSError("clip failed")))
    toasts = []
    monkeypatch.setattr(
        r, "toast", lambda text, kind="ok", _seconds=2.8: toasts.append((text, kind))
    )

    pic, audio = r._capture_media("test_base", "/fake/video.mkv")
    assert pic == "" and audio == ""
    assert any(kind == "warn" for _, kind in toasts), f"no warn toast shown; got {toasts}"


def test_provenance_is_clean_anime_episode_timestamp():
    ipc = FakeIPC()
    ipc.props["time-pos"] = 607
    r = Reader(ipc)
    assert r._provenance(VIDEO) == "Nippon Sangoku · ep10 · 10:07"  # not the raw filename


# --- Stage 1: cue change while hovered leaves stale tooltip / stuck pause ----------------------


def test_cue_change_while_hovered_hides_tooltip_and_resets_state(monkeypatch):
    """When a new subtitle cue arrives while a tooltip is shown, set_subtitle must tear down the
    hover stack (TIP_ID hidden, _tip_rect/state/key cleared) so the next ⊕ click can't mine the
    old word, and so pause_on_tooltip does not stay stuck."""
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)  # open a tooltip on the first subtitle
    assert r.hover_view().tip.rect is not None  # tooltip is shown
    assert r.hover == 0

    # simulate a cue change while the tooltip is visible
    hidden = []
    # Returns a reply, not None: the fenced surface path reads `error` from it now, so a recorder
    # that answers nothing reads as a torn overlay rather than as a recorded hide.
    monkeypatch.setattr(r.ov, "hide", lambda oid: (hidden.append(oid), {"error": "success"})[1])
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.set_subtitle("別の字幕")

    assert C.TIP_ID in hidden  # tooltip was hidden
    assert r.hover_view().tip.rect is None  # _tip_rect reset
    assert r.hover_view().tip.state is None  # _tip_state reset
    assert r.hover_view().tip.key is None  # _tip_key reset
    assert r.hover == -1  # hover index reset


def test_cue_change_while_paused_by_tip_resumes_mpv(monkeypatch):
    """If pause_on_tooltip paused mpv when the tooltip opened, a cue change must resume it."""
    ipc = FakeIPC()
    ipc.props["pause"] = False
    r = _reader_with_word(ipc)  # pause_on_tooltip=True
    monkeypatch.setattr(r, "renderer", NullRenderer())
    Driver(r).move_to_word(0)  # opens tooltip and pauses mpv
    assert r.hover_view().paused

    # new cue arrives
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.set_subtitle("別の字幕")

    assert not r.hover_view().paused
    assert ("set_property", "pause", False) in ipc.commands


# --- Stage 2: P2 trio fixes -----------------------------------------------------------------------


def test_entry_for_does_not_mutate_cached_entry_jlpt_pill_dedup():
    """entry_for_tok must not mutate the lru_cached Entry returned by entry_for / dict_set.entry_for.
    Two calls with a JLPT-level token must yield exactly ONE pill each time, not accumulate.
    Uses a dict_set whose entry_for IS lru_cached (same object returned each call) to expose mutation."""
    from saitenka.app.tokenize import Token

    # A dict_set backed by a real lru_cache so the same Entry object is returned on repeated calls.
    from saitenka.panel import Definition
    from saitenka.panel import Entry as _Entry

    class _CachedDS:
        @functools.cache  # noqa: B019  # test-local fake, GC'd with the test — no leak risk
        def entry_for(self, surface, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
            return _Entry(headword=surface, defs=[Definition("D", ["x"])], freqs=[])

    r = Reader(
        FakeIPC(), dict_set=_CachedDS(), scorer=_jlpt_scorer({"本命": "N2", "ほんめい": "N2"})
    )
    tok = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
    # Call entry_for twice directly via entry_for_tok so the lru_cache is hit on the second call.
    e1 = tooltip_panel.entry_for_tok(tok, None, dict_set=r.dict_set, scorer=r.scorer)
    e2 = tooltip_panel.entry_for_tok(tok, None, dict_set=r.dict_set, scorer=r.scorer)
    jlpt_pills_1 = [f for f in e1.freqs if f.name == "JLPT"]
    jlpt_pills_2 = [f for f in e2.freqs if f.name == "JLPT"]
    assert len(jlpt_pills_1) == 1, f"first call: {len(jlpt_pills_1)} JLPT pills, want 1"
    assert len(jlpt_pills_2) == 1, f"second call: {len(jlpt_pills_2)} JLPT pills, want 1"


def test_prefetch_worker_receives_mined_flag_not_calls_card_for(monkeypatch):
    """_update_prefetch must pass the mined flag from the main thread so that prefetch workers
    never call _is_mined → card_for → jamdict from a worker thread."""
    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.sub_text = "本命"
    is_mined_calls_from_workers: list[str] = []

    original_is_mined = r._is_mined

    def tracked_is_mined(tok):
        import threading

        if threading.current_thread().name.startswith("saitenka-job-speculative-prefetch"):
            is_mined_calls_from_workers.append(tok.surface)
        return original_is_mined(tok)

    monkeypatch.setattr(r, "_is_mined", tracked_is_mined)
    submitted = []
    r.prefetch_state.workers = 8
    r.prefetch_state.submitter = lambda **kwargs: submitted.append(kwargs) or True
    r._update_prefetch()
    items = [entry["request"].item for entry in submitted]
    assert items, "nothing was queued"
    # Each queued item must carry the main-thread-evaluated mined flag (typed since Stage 8b)
    assert isinstance(items[0].mined, bool), f"queue item lacks the mined flag: {items[0]}"


def test_cue_change_nested_also_cleared(monkeypatch):
    """A cue change with a nested popup open must also clear NESTED_ID and _nest state."""
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    _hover_base_word(r)
    _hover_first_scan_cell(r)
    _fire_dwell(ipc, "scan-open")  # open the nested popup
    assert r.hover_view().nested.state is not None

    hidden = []
    monkeypatch.setattr(r.ov, "hide", lambda oid: (hidden.append(oid), {"error": "success"})[1])
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.set_subtitle("別の字幕")

    assert C.NESTED_ID in hidden or r.hover_view().nested.state is None  # nested cleared


# --- Stage 7c: event-driven property reads (observe_property instead of per-tick get_property) ----


def test_fakeipc_in_util_emits_property_change_events():
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.set_prop("sub-text", "本を読む")
    evs = ipc.drain_events()
    assert {"event": "property-change", "name": "sub-text", "data": "本を読む"} in [
        {k: e.get(k) for k in ("event", "name", "data")} for e in evs
    ]
    assert ipc.drain_events() == []  # drained


def test_start_observing_registers_and_seeds_initial_state(request):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    # Observer registration goes through the gateway, and run/attach install one immediately after
    # connecting — so a session without it is a configuration production never has. The fake used to
    # lack the port entirely, which sent `register_observer_set` down its no-gateway fallback and
    # made this pass against a path production never takes.
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    ipc.props["pause"] = True
    ipc.props["sub-text"] = "字幕"
    r = Reader(ipc)
    request.addfinalizer(
        r.close
    )  # LIFO: the reader goes down before the gateway it observes through
    r.start_observing()
    observed = {c[2] for c in ipc.commands if c and c[0] == "observe_property"}
    assert {
        "sub-text",
        "mouse-pos",
        "osd-dimensions",
        "pause",
        "secondary-sub-text",
        "sub-delay",
    } <= observed
    # initial state was read once at startup
    assert r.observed_property("pause") is True
    assert r.observed_property("sub-text") == "字幕"


def test_poll_tick_does_no_property_round_trips_once_observing(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props["sub-text"] = ""
    r = Reader(ipc, prefetch=False)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.start_observing()
    ipc.commands.clear()
    r.pump()
    gets = [
        c
        for c in ipc.commands
        if c
        and c[0] == "get_property"
        and c[1] in {"sub-text", "mouse-pos", "osd-dimensions", "pause", "secondary-sub-text"}
    ]
    assert gets == [], f"steady-state tick still does blocking property reads: {gets}"


def test_property_change_event_drives_subtitle_update(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    r = Reader(ipc, prefetch=False)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.start_observing()
    seen = []
    monkeypatch.setattr(r, "set_subtitle", lambda t: seen.append(t))
    ipc.set_prop("sub-text", "新しい字幕")
    r.pump()
    assert seen == ["新しい字幕"]  # the buffered event drove the update, no get_property


def test_cue_change_retires_interaction_before_later_command_in_same_batch(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props.update({"sub-text": "古い字幕", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()
    reader.set_subtitle("古い字幕")
    copied = []
    monkeypatch.setattr(reader, "copy_line", lambda: copied.append(reader.sub_text))
    ipc.events.extend(
        (
            {"event": "property-change", "name": "sub-text", "data": "新しい字幕"},
            {"event": "client-message", "args": [C.COPY_LINE_MSG]},
        )
    )

    reader._drain_events()

    # The command was rejected because the conflicting observation retired the cue first — that
    # rejection IS the ordering proof. The drain then settles the replacement in the same turn;
    # reconciliation used to wait for the next tick.
    assert copied == []
    assert reader.sub_text == "新しい字幕"


def test_cue_change_retires_subtitle_navigation_in_the_same_batch(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props.update({"sub-text": "古い字幕", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()
    reader.set_subtitle("古い字幕")
    navigated = []
    monkeypatch.setattr(reader, "_sub_nav", lambda delta: navigated.append(delta))
    ipc.events.extend(
        (
            {"event": "property-change", "name": "sub-text", "data": "新しい字幕"},
            {"event": "client-message", "args": [C.SUB_NEXT_MSG]},
        )
    )

    reader._drain_events()

    assert navigated == []  # the nav command was rejected against the retired cue
    assert reader.sub_text == "新しい字幕"  # and the replacement settled in the same drain


def test_a_replaced_source_revises_the_identity_of_the_same_cue_text():
    """Re-showing identical text after a source swap must not reuse the old cue identity."""
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props.update({"sub-text": "同じ字幕", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()
    reader.set_subtitle("同じ字幕")
    before = reader._current_cue_identity
    assert before is not None

    reader._replace_subtitle_source("/media/next.mkv", reason="test")

    assert reader._cue_retired is True
    reader.set_subtitle("同じ字幕")
    after = reader._current_cue_identity
    assert after is not None
    assert after != before
    assert after.normalized_text == before.normalized_text


def test_connection_loss_retires_cue_and_suspends_commands_and_settlement(monkeypatch):
    from util import FakeIPC as EventIPC

    from saitenka.runtime import (
        CommandHandled,
        CommandOutcome,
        CommandReason,
        ConnectionLost,
        UserCommand,
    )

    ipc = EventIPC()
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.set_subtitle("古い字幕")
    copied = []
    monkeypatch.setattr(reader, "copy_line", lambda: copied.append(reader.sub_text))
    reader._drain_event(ConnectionLost(0))
    reader._drain_event(UserCommand(C.COPY_LINE_MSG, command_id=7))
    assert reader.pump()

    assert copied == []
    # A disconnected turn stops before its post-drain settlement, so readiness is never marked.
    # (Was: the tick pipeline did not run. Same claim, now that there is no pipeline to not run.)
    assert reader._interactive_ready is False
    assert reader._cue_retired
    assert reader.tokens == [] and reader.boxes == []
    assert ipc.runtime_outcomes == [
        CommandHandled(
            C.COPY_LINE_MSG,
            None,
            CommandOutcome.REJECTED,
            command_id=7,
            reason=CommandReason.DISCONNECTED,
        )
    ]


def test_same_text_with_new_timing_installs_a_new_cue_identity():
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props.update({"sub-text": "同じ字幕", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()
    reader.set_subtitle("同じ字幕")
    previous = reader._current_cue_identity

    ipc.set_prop("sub-start", 3.0)
    reader.pump()

    assert reader._cue_retired is False
    assert reader._current_cue_identity != previous
    assert reader._current_cue_identity.observed_start == 3.0


def test_a_cue_cleared_by_the_reader_is_not_resurrected_by_the_next_observation():
    """Two writers, one fact. `set_subtitle` is the Reader-side writer (subtitle_modes clears the
    cue on a mode/track change) and `observe` is mpv's; reconciliation reads the projection. Both
    writers now reach it, per invariant 13 — before that the cleared cue came back on the next
    changed cue fact."""
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props.update({"sub-text": "猫を見る", "sid": 1, "sub-start": 1.0, "sub-end": 3.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()

    reader.set_subtitle("")  # what a language/track switch does
    assert reader.sub_text == ""

    reader._on_property_change({"name": "sub-end", "data": 9.5})
    reader._settle_cue_observation()

    assert reader.sub_text == ""


@pytest.mark.parametrize(("name", "value"), [("sid", 2), ("sub-start", 3.0), ("sub-end", 4.0)])
def test_reconnect_retires_same_text_cue_when_seeded_identity_changed(name, value):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props.update({"sub-text": "同じ字幕", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()
    reader.set_subtitle("同じ字幕")
    ipc.props[name] = value

    reader._on_ipc_reconnect()
    reader._on_property_change({"event": "property-change", "name": name, "data": value})

    assert reader._cue_retired is True
    assert reader.tokens == [] and reader.boxes == []


def test_property_change_invalidates_subtitle_geometry():
    from util import FakeIPC as EventIPC

    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.subtitles import (
        GeometryRequest,
        GeometrySnapshot,
        SubtitleEventId,
        SubtitleFrameId,
        SubtitleTrackId,
    )

    class Backend:
        def render(self, request: GeometryRequest) -> GeometrySnapshot:
            return GeometrySnapshot(
                request.generation,
                request.track_id,
                request.frame_id,
                request.timestamp_ms,
                request.variant,
                (),
            )

        def close(self) -> None:
            pass

    ipc = EventIPC()
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    r.subtitle_pipeline = SubtitleModeCoordinator(NullRenderer(), Backend())
    r.start_observing()
    track_id = SubtitleTrackId("track-1")
    event_id = SubtitleEventId(track_id, 1_000, 2_000, 0, 2)
    request = GeometryRequest(
        r.subtitle_pipeline.generation,
        track_id,
        SubtitleFrameId(track_id, (event_id,)),
        1_250,
        (1920, 1080),
        (1920, 1080),
        b"[Script Info]\n",
    )
    assert r.subtitle_pipeline.render(request) is not None
    assert r.subtitle_pipeline.current is not None

    ipc.set_prop("sub-text", "新しい字幕")
    r.pump()

    assert r.subtitle_pipeline.current is None


def test_property_change_event_drives_hover(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    r = Reader(ipc, prefetch=False)
    r.tokens = ["x"]
    seen = []
    monkeypatch.setattr(r, "set_hover", lambda i: (seen.append(i), setattr(r, "hover", i)))
    monkeypatch.setattr(r, "_hit", lambda x, y: 0 if (x < 10 and y < 10) else -1)
    r.start_observing()
    ipc.set_prop("mouse-pos", {"hover": True, "x": 5, "y": 5})
    for ev in ipc.drain_events():  # what pump's drain loop does
        if ev.get("event") == "property-change":
            r._on_property_change(ev)
    r._update_hover()
    assert seen == [0]  # hover driven purely by the observed event state


# --- Stage 8b: grouped options object (de-kwarg) + typed queue items ------------------------------


def test_reader_accepts_grouped_options_object():
    from saitenka.app.config import KeyOptions, ReaderOptions, TooltipOptions

    opts = ReaderOptions(
        keys=KeyOptions(mine_key="Ctrl+x", sub_prev_key="Alt+a"),
        tooltip=TooltipOptions(tip_max_frac=0.5, pause_on_tooltip=True),
        prefetch=False,
    )
    r = Reader(FakeIPC(), options=opts)
    assert r.keys.mine_key == "Ctrl+x"
    assert r.keys.sub_prev_key == "Alt+a"
    assert r.tip_max_frac == 0.5
    assert r.pause_on_tooltip is True
    assert r.prefetch is False


def test_reader_kwargs_still_work_and_map_onto_groups():
    # legacy exploded kwargs stay accepted (they build the options object internally)
    r = Reader(FakeIPC(), mine_key="Ctrl+z", tip_max_frac=0.4, auto_translate=True)
    assert r.keys.mine_key == "Ctrl+z" and r.tip_max_frac == 0.4 and r.auto_translate is True
    with pytest.raises(TypeError):
        Reader(FakeIPC(), not_a_knob=1)  # typo detection preserved


def test_prefetch_queue_items_are_typed_dataclasses():
    from saitenka.app.prefetch import PrefetchItem

    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.sub_text = "本命"
    submitted = []
    r.prefetch_state.workers = 8
    r.prefetch_state.submitter = lambda **kwargs: submitted.append(kwargs) or True
    r._update_prefetch()
    item = submitted[0]["request"].item
    assert isinstance(item, PrefetchItem)
    assert item.token.surface == "本命"
    assert item.inflected == "本命"
    assert isinstance(item.gen, int) and isinstance(item.mined, bool)


# --- controller split — popups.py (PopupView/Panel) + miner.py (Miner) ---------------------------


def test_popups_module_unifies_popup_view_state():
    from saitenka.app.popups import Panel, PopupView
    from saitenka.panel import Definition, Entry, panel_rows

    pv = PopupView()
    # the unified per-popup view state (nested popup; base tip keeps its own exploded state)
    assert pv.state is None and pv.scroll == 0 and pv.rect is None
    assert C._Nested is PopupView
    r = Reader(FakeIPC())
    assert isinstance(r.tip.nest, PopupView)
    # Panel wraps the windowed engine and is constructible from rows
    entry = Entry(
        headword="本命", reading="ほんめい", defs=[Definition("辞書", ["これは定義です。"])]
    )
    panel = Panel.from_rows(panel_rows(entry, 384), 384, "ほんめい")
    assert panel.reading == "ほんめい" and panel.width == 384 and panel.full_height > 0


def test_from_rows_band_cache_max_retains_more_of_a_tall_panel():
    from saitenka.app.popups import Panel
    from saitenka.panel import Definition, Entry, panel_rows

    # A tall, polysemous entry whose blocks far exceed one viewport, so eviction actually bites.
    def entry():
        return Entry(
            headword=["掛ける", {"tag": "rt", "content": "かける"}],
            defs=[Definition(f"辞書{i}", [f"意味{i}：とても長い説明文。" * 3]) for i in range(40)],
        )

    # cap=None keeps exactly the viewport±overscan; a generous cap retains the MRU bands past it.
    default = Panel.from_rows(panel_rows(entry(), 384), 384, "")
    capped = Panel.from_rows(panel_rows(entry(), 384), 384, "", band_cache_max=200)
    for scroll in range(0, 3000, 100):
        default.viewport(scroll, 300, overscan=60)
        capped.viewport(scroll, 300, overscan=60)
    assert capped.windowed.cached_blocks > default.windowed.cached_blocks


def test_miner_module_owns_the_mining_flow(monkeypatch):
    from saitenka.app import miner
    from saitenka.app.miner import tag_slug

    assert tag_slug("Nippon Sangoku") == "Nippon_Sangoku"
    ipc = FakeIPC()
    r = _reader_with_word(ipc)
    # Reader's mining API delegates to the module, handing it the cue it built (behaviour preserved)
    mined = []
    monkeypatch.setattr(
        miner, "mine_token", lambda p, tok, **_k: mined.append((p.cue.hover, tok.surface))
    )
    r.anki = object()
    r.mine_cfg = object()
    r.hover = 0
    r.mine_current()
    assert mined == [(0, "本命")]


def test_a_reader_with_no_deck_builds_no_mining_value(monkeypatch):
    """ "Is there anywhere to mine into" is decided once, by the property, instead of at every entry
    point — so an unconfigured session cannot reach the flow at all."""
    from saitenka.app import miner

    r = _reader_with_word(FakeIPC())
    r.anki = None
    monkeypatch.setattr(miner, "mine_token", lambda *_a, **_k: pytest.fail("mined with no deck"))

    assert r.miner_ports is None
    r.mine_current()  # must be a no-op, not an AttributeError on the missing client


def _accrual_reader(ipc, monkeypatch) -> tuple[Reader, list]:
    ipc.props.update({"osd-dimensions": {"w": 1280, "h": 720}, "pause": False, "path": "/a.mkv"})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    accrued: list = []
    monkeypatch.setattr(
        C.session_stats, "accrue", lambda recorder, **kw: accrued.append((recorder, kw))
    )
    return reader, accrued


def test_watch_time_accrues_on_the_pause_transition_not_on_a_tick(monkeypatch):
    """WP5.4: the segment a pause delimits is exactly what should be accrued, so an idle runtime
    does no work and a tickless one still measures correctly."""
    ipc = RuntimeFakeIPC()
    r, accrued = _accrual_reader(ipc, monkeypatch)
    r.start_observing()
    assert accrued == []  # observing alone is not a transition

    ipc.emit({"event": "property-change", "name": "pause", "data": True})
    r._drain_events()

    assert len(accrued) == 1
    r.close()


def test_an_uninterrupted_session_still_persists_on_its_own_deadline(monkeypatch):
    """A viewer who never pauses produces no transitions, so without a standing deadline everything
    would sit in memory until close and be lost to a crash. The due event re-arms itself."""
    ipc = RuntimeFakeIPC()
    r, accrued = _accrual_reader(ipc, monkeypatch)
    r.arm_session_persist(5.0)

    assert ipc.fire_runtime_timer("lifecycle:session-persist")
    assert len(accrued) == 1
    assert ipc.fire_runtime_timer("lifecycle:session-persist")  # re-armed itself

    assert len(accrued) == 2
    r.close()


def test_an_osd_resize_redraws_from_the_observation_alone():
    """WP5.4: `refresh_osd` re-detected, per tick, a change the projection already publishes. The
    resize is a `RenderSpaceChanged` now, with no tick anywhere in the trace."""
    ipc = RuntimeFakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    r.start_observing()
    assert r.osd == (1280, 720)

    ipc.emit({"event": "property-change", "name": "osd-dimensions", "data": {"w": 1920, "h": 1080}})
    r._drain_events()

    assert r.osd == (1920, 1080)
    r.close()


def test_a_sub_rendering_option_does_not_resize_anything():
    """The negative control. Every `options/sub-*` property is render space too, and treating one
    as a resize would redraw the whole chrome on a styling change that moved no window."""
    ipc = RuntimeFakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    r.start_observing()
    redraws = []
    r._redraw_after_resize = lambda: redraws.append(1)  # type: ignore[method-assign]

    ipc.emit({"event": "property-change", "name": "options/sub-scale", "data": 1.4})
    r._drain_events()

    assert redraws == []
    r.close()


def test_capability_probes_refresh_on_their_own_deadline(monkeypatch):
    """The tick asked TTL-gated probes 40x a second, almost always to be told "not yet". A deadline
    asks far less often and, unlike a tick, keeps asking in a runtime that has none."""
    ipc = RuntimeFakeIPC()
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    applied = []
    monkeypatch.setattr(r, "_apply_capabilities", lambda: applied.append(1))

    r.arm_capability_refresh(0.5)

    assert applied == []  # arming is not asking
    assert ipc.fire_runtime_timer("lifecycle:capability-refresh")
    assert len(applied) == 1
    assert ipc.fire_runtime_timer("lifecycle:capability-refresh")  # re-armed itself
    assert len(applied) == 2
    r.close()


def test_the_sidebar_follows_the_cue_where_the_cue_settles(monkeypatch):
    """The active row tracks the cue, so it re-follows at the settle boundary rather than being
    asked every tick whether the cue moved."""
    ipc = RuntimeFakeIPC()
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    follows = []
    monkeypatch.setattr(C.sidebar, "follow", follows.append)

    r._settle_cue_observation()

    # The view it followed, not the host: identity with `r.sidebar` says the settle boundary
    # reached this sidebar's own state rather than merely calling something once.
    assert [view.state for view in follows] == [r.sidebar]
    r.close()


def test_a_refused_seek_is_reported_rather_than_discarded(caplog):
    """The instant render already drew the target, so what the write owes is a terminal outcome:
    a seek that vanished into a discarded reply left the overlay showing a cue the video never
    reached, with nothing in the log to say so."""
    import logging

    from saitenka.app.subtitle_intents import SeekCue

    class RefusingIPC(FakeIPC):
        def command(self, *args):
            super().command(*args)
            if args and args[0] == "sub-seek":
                return {"error": "property unavailable"}
            return (
                {"data": self.props.get(args[1])} if args[0] == "get_property" else {"data": None}
            )

    ipc = RefusingIPC()
    r = Reader(ipc, prefetch=False, renderer=NullRenderer())
    with caplog.at_level(logging.WARNING, logger="saitenka.app.mpv_egress"):
        r.seek_cue(SeekCue(1, r.cue_revision))

    assert any("sub-seek" in record.getMessage() for record in caplog.records)
    r.close()


def test_anki_media_failures_are_absent_media_not_errors() -> None:
    """An optional integration being down is an ordinary state, not something to raise through a
    keypress — a preview missing its screenshot is still a preview."""
    from saitenka.app.miner_ui import media_image, media_tempfile

    class DownAnki:
        def retrieve_media(self, _name):
            raise OSError("connection refused")

    assert media_image(None, "shot.png") is None  # no Anki configured
    assert media_image(DownAnki(), "shot.png") is None  # Anki configured but down
    assert media_image(DownAnki(), "") is None  # nothing to fetch
    assert media_tempfile(DownAnki(), "clip.mp3", Path("/nonexistent")) is None


def test_sentence_lines_rejoins_each_tokenized_line() -> None:
    from saitenka.app.miner_ui import sentence_lines
    from saitenka.app.tokenize import Token

    def token(surface: str) -> Token:
        return Token(surface, surface, "", "名詞", 0, len(surface))

    assert sentence_lines([[token("猫"), token("を"), token("見る")], [token("犬")]]) == [
        "猫を見る",
        "犬",
    ]


def test_a_refused_interaction_command_is_reported_rather_than_lost(caplog):
    """What correlating these keybinds buys. A `keybind` that mpv rejects, or that the runtime
    refuses to admit, used to vanish into a discarded reply and read on screen as a dead shortcut
    with nothing in the log to find. The startup batch is deliberately still uncorrelated for a
    capacity reason `_register_keybinds` records — this covers the paths that are.
    """
    from saitenka.app.mpv_egress import send_correlated
    from saitenka.runtime import Owner

    class Refusing(FakeIPC):
        def submit_runtime_mpv(self, **_kwargs) -> bool:
            return False

    with caplog.at_level(logging.WARNING, logger="saitenka.app.mpv_egress"):
        send_correlated(
            Refusing(), "enable-mouse-section", "enable-section", "x", owner=Owner.INTERACTION
        )

    assert "enable-mouse-section" in caplog.text
    assert "not admitted" in caplog.text


def test_every_hover_pause_resume_takes_the_same_path(monkeypatch):
    """There were three writes for this one fact, and two reached mpv through a
    `getattr(ipc, "command_async", ipc.command)` probe — invisible to the direct-write gate, and a
    fake missing the port silently took the other branch. Both triggers must now be indistinguishable
    at the wire.
    """
    from saitenka.app import hover_intents
    from saitenka.app.hover_adapter import HoverAdapter

    def resume_via(act) -> list[tuple]:
        ipc = FakeIPC()
        ipc.props["pause"] = False
        reader = _reader_with_word(ipc)
        monkeypatch.setattr(reader, "renderer", NullRenderer())
        Driver(reader).move_to_word(0)
        assert reader.hover_view().paused
        before = len(ipc.commands)
        act(reader)
        assert not reader.hover_view().paused
        return [c for c in ipc.commands[before:] if c[:2] == ("set_property", "pause")]

    by_cue = resume_via(lambda r: r.set_subtitle("別の字幕"))
    by_reducer = resume_via(lambda r: HoverAdapter(r).apply(hover_intents.ResumePlayback()))

    assert by_cue == by_reducer == [("set_property", "pause", False)]


def _seeded_reader(request, *, latched, settled):
    """A Reader whose `self.osd` was latched pre-observe, then seeded from a settled `osd-dimensions`.

    Driven through `start_observing` and a real gateway rather than by assigning `osd`: the transient
    only reaches `self.osd` because `observed_property` falls through to a BLOCKING read before
    observing begins, and that fall-through IS the mechanism under test.
    """
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props["osd-dimensions"] = latched
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    r = Reader(ipc)
    request.addfinalizer(r.close)
    r.refresh_osd()  # the pre-observe blocking read, mid mpv fullscreen animation
    ipc.props["osd-dimensions"] = settled
    r.start_observing()
    return r


def test_seeding_folds_the_settled_osd_over_a_transient_pre_observe_read(request):
    """`self.osd` decides where every overlay lands; the geometry lays hit boxes out against the live
    `osd-dimensions`. A real report caught the blocking read returning 3642x2096 six milliseconds
    before mpv settled on 3024x1898.

    Seeding publishes no delta, so nothing re-drives `refresh_render_space` afterwards. Without a
    hand reconcile the transient stands for the whole session and every box is uniformly offset —
    which is why the shift was per-launch luck rather than per-episode.
    """
    r = _seeded_reader(request, latched={"w": 3642, "h": 2096}, settled={"w": 3024, "h": 1898})

    assert r.osd == (3024, 1898)


def test_seeding_keeps_the_osd_when_mpv_reports_nothing(request):
    """Negative control: the reconcile must not clobber a good size with a fallback. `refresh_osd`
    already declines an absent/zero report, and the seed must not route around that guard."""
    r = _seeded_reader(request, latched={"w": 1920, "h": 1080}, settled={})

    assert r.osd == (1920, 1080)
