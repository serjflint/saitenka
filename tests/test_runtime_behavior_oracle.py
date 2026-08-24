from __future__ import annotations

from concurrent.futures import Future
from typing import cast

import pytest
from legacy_session_controller_behavior import LegacyReaderTrace
from runtime_behavior import BehaviorRecord, BehaviorTrace, CueState
from util import FakeIPC, runtime_gateway

from saitenka.app.bindings import SUB_PICKER_MSG
from saitenka.app.session_controller import SessionController
from saitenka.app.session_routes import install_session_reactor
from saitenka.app.subtitle_render import NativeVisibleRenderer, NullRenderer
from saitenka.app.subtitles import WordBox
from saitenka.mpvio.ipc import IPCRequest


class _VisibilityIPC(FakeIPC):
    def command(self, *args):
        if args[:2] == ("set_property", "sub-visibility"):
            self.props["sub-visibility"] = args[2]
        return super().command(*args)


class _AsyncHintIPC(FakeIPC):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[IPCRequest] = []

    def command_async(self, *args, expected_connection_epoch=None):
        del expected_connection_epoch
        request = IPCRequest(len(self.requests), 0, Future())
        self.commands.append(args)
        self.requests.append(request)
        return request


def test_first_command_precedes_readiness_and_cosmetic_clear(monkeypatch, request) -> None:
    ipc = _AsyncHintIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    install_session_reactor(gateway)
    ipc.requests[0].future.set_result({"error": "success"})
    reader = SessionController(ipc, renderer=NullRenderer())
    request.addfinalizer(reader.close)  # LIFO: the reader goes down before its gateway
    dispatched: list[bool] = []
    monkeypatch.setattr(reader, "toggle_sub_picker", lambda: dispatched.append(True))
    ipc.emit({"event": "client-message", "args": [SUB_PICKER_MSG]})
    trace = LegacyReaderTrace(reader)

    assert reader.pump()
    trace.observe("first-input", outcome="dispatched-before-ready-clear")
    assert reader.pump()
    trace.observe("next-turn", outcome="clear-reply-not-required")

    assert dispatched == [True]
    assert trace.records() == (
        {
            "event": "first-input",
            "cue": "none",
            "pixels": "none",
            "interaction": "unavailable",
            "surfaces": "none",
            "lifecycle": "open",
            "outcome": "dispatched-before-ready-clear",
        },
        {
            "event": "next-turn",
            "cue": "none",
            "pixels": "none",
            "interaction": "unavailable",
            "surfaces": "none",
            "lifecycle": "open",
            "outcome": "clear-reply-not-required",
        },
    )
    reader.close()


def test_changed_cue_retires_interaction_before_later_batch_command(monkeypatch) -> None:
    ipc = FakeIPC()
    ipc.props.update({"sub-text": "old", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = SessionController(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()
    reader.set_subtitle("old")
    reader.boxes = [WordBox(0, 10, 10, 20, 20)]
    copied: list[str] = []
    monkeypatch.setattr(reader, "copy_line", lambda: copied.append("called"))
    trace = LegacyReaderTrace(reader)
    trace.observe("cue-installed", outcome="interactive")

    ipc.events.extend(
        (
            {"event": "property-change", "name": "sub-text", "data": "new"},
            {"event": "client-message", "args": ["saitenka-copy-line"]},
        )
    )
    # Reconciliation now runs at the drain's batch boundary rather than on the next tick, so the
    # conflict phase is observed from inside the drain — after every event in the batch was
    # processed against the retired cue, before the replacement settles. Same three phases, real
    # boundaries; snapshotting after the drain would only ever see the settled state.
    settle = reader._settle_cue_observation

    def traced_settle() -> None:
        trace.observe("cue-conflict", outcome="input-rejected")
        settle()

    monkeypatch.setattr(reader, "_settle_cue_observation", traced_settle)
    reader._drain_events()
    trace.observe("cue-reconciled", outcome="replacement-active")

    assert copied == []
    assert trace.records() == (
        {
            "event": "cue-installed",
            "cue": "active",
            "pixels": "none",
            "interaction": "ready",
            "surfaces": "none",
            "lifecycle": "open",
            "outcome": "interactive",
        },
        {
            "event": "cue-conflict",
            "cue": "retired",
            "pixels": "none",
            "interaction": "unavailable",
            "surfaces": "none",
            "lifecycle": "open",
            "outcome": "input-rejected",
        },
        {
            "event": "cue-reconciled",
            "cue": "active",
            "pixels": "none",
            "interaction": "unavailable",
            "surfaces": "none",
            "lifecycle": "open",
            "outcome": "replacement-active",
        },
    )
    reader.close()


def test_native_geometry_degradation_changes_hits_not_pixel_owner() -> None:
    ipc = _VisibilityIPC()
    ipc.props.update({"sid": 2, "sub-visibility": False})
    renderer = NativeVisibleRenderer()
    reader = SessionController(ipc, prefetch=False, renderer=renderer)
    reader.sub_text = "active"
    reader.subtitle_pipeline.cue_changed(reader.subtitle_target(), nonempty=True)
    trace = LegacyReaderTrace(reader)
    trace.observe("native-cue", outcome="pixels-established")

    reader.boxes = [WordBox(0, 10, 10, 20, 20)]
    renderer.use_native(reader.subtitle_target())
    reader.hover = 0
    reader.subtitle_pipeline.draw_current(reader.subtitle_target())
    trace.observe("geometry-ready", outcome="interaction-ready")
    reader.boxes = []
    renderer.degrade_geometry(reader.subtitle_target())
    trace.observe("geometry-miss", outcome="interaction-only-degraded")
    reader.boxes = [WordBox(0, 10, 10, 20, 20)]
    renderer.use_native(reader.subtitle_target())
    reader.subtitle_pipeline.draw_current(reader.subtitle_target())
    trace.observe("geometry-recovered", outcome="interaction-ready")

    assert [record["pixels"] for record in trace.records()] == [
        "native",
        "native",
        "native",
        "native",
    ]
    assert [record["interaction"] for record in trace.records()] == [
        "unavailable",
        "hovered",
        "unavailable",
        "hovered",
    ]
    assert [record["surfaces"] for record in trace.records()] == [
        "none",
        "present",
        "none",
        "present",
    ]
    reader.close()
    trace.observe("close", outcome="presentation-retired")
    assert trace.records()[-1] == {
        "event": "close",
        "cue": "active",
        "pixels": "none",
        "interaction": "hovered",
        "surfaces": "none",
        "lifecycle": "closed",
        "outcome": "presentation-retired",
    }


def test_behavior_trace_rejects_unbounded_user_text() -> None:
    trace = BehaviorTrace()

    with pytest.raises(ValueError, match="text-free vocabulary"):
        trace.append(
            BehaviorRecord(
                event="猫を見る",
                cue="none",
                pixels="none",
                interaction="unavailable",
                surfaces="none",
                lifecycle="open",
                outcome="interactive",
            )
        )


def test_behavior_trace_rejects_user_text_in_state_fields() -> None:
    trace = BehaviorTrace()

    with pytest.raises(ValueError, match="text-free vocabulary"):
        trace.append(
            BehaviorRecord(
                event="close",
                cue=cast("CueState", "猫を見る"),
                pixels="none",
                interaction="unavailable",
                surfaces="none",
                lifecycle="closed",
                outcome="presentation-retired",
            )
        )
