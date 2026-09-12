from __future__ import annotations

from concurrent.futures import Future
from typing import cast

import pytest
from runtime_behavior import BehaviorRecord, BehaviorTrace, CueState
from saitenka_tokenize.japanese import Token
from session_behavior_trace import SessionTrace, _visible_surfaces
from util import FakeIPC, await_ready, bare_gateway

from saitenka.app import subtitle_adapter
from saitenka.app.bindings import SUB_PICKER_MSG
from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.routes import install_session_reactor
from saitenka.app.subtitle_render import NativeVisibleRenderer, NullRenderer
from saitenka.app.subtitles import WordBox
from saitenka.app.token_cache import TokenizedCue
from saitenka.mpvio.ipc import IPCRequest
from saitenka.runtime import events


def _token(surface: str) -> Token:
    return Token(surface=surface, lemma=surface, reading="", pos="名詞", start=0, end=len(surface))


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


def _runtime_settled(reader) -> bool:
    loop = reader.graph.ipc.session_loop
    assert loop is not None
    snapshot = loop.mailbox.snapshot
    return (
        snapshot.normal == 0
        and snapshot.lifecycle == 0
        and snapshot.terminal == 0
        and snapshot.terminal_reserved == 0
        and snapshot.command_reserved == 0
        and reader.graph.lifecycle_surfaces.settled()
        and reader.graph.interaction_surfaces.settled()
    )


def test_first_command_precedes_readiness_and_cosmetic_clear(
    monkeypatch, request, make_session
) -> None:
    ipc = _AsyncHintIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    install_session_reactor(gateway)
    ipc.requests[0].future.set_result({"error": "success"})
    reader = make_session(ipc, infrastructure=SessionInfrastructure(renderer=NullRenderer()))
    request.addfinalizer(reader.close)  # LIFO: the reader goes down before its gateway
    reader.start()
    dispatched: list[bool] = []
    monkeypatch.setattr(
        reader.graph.picker,
        "open",
        lambda *_args, **_kwargs: dispatched.append(True),
    )
    ipc.emit({"event": "client-message", "args": [SUB_PICKER_MSG]})
    trace = SessionTrace(reader)

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


def test_changed_cue_retires_interaction_before_later_batch_command(
    monkeypatch, make_session
) -> None:
    ipc = FakeIPC()
    ipc.props.update({"sub-text": "old", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.graph.playback.start_session()
    reader.graph.cue.set_subtitle("old")
    # Tokens alongside the boxes, because that is the only pairing production can produce: a box
    # exists because a token was measured, and a draw withholds boxes a cue has no tokens for
    # (`paintable_boxes`). Setting geometry alone builds a state the runtime cannot reach, and the
    # assertion below then rests on it.
    reader.graph.subtitle_presentation.cue.install_tokenized(
        TokenizedCue(lines=[[_token("active")]], tokens=[_token("active")], styles=None)
    )
    reader.graph.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 10, 10, 20, 20)])
    copied: list[str] = []
    monkeypatch.setattr(subtitle_adapter, "copy_clipboard", lambda _text: copied.append("called"))
    trace = SessionTrace(reader)
    trace.observe("cue-installed", outcome="interactive")

    ipc.set_prop("sub-text", "new")
    ipc.emit({"event": "client-message", "args": ["saitenka-copy-line"]})
    # Reconciliation now runs at the drain's batch boundary rather than on the next tick, so the
    # conflict phase is observed from inside the drain — after every event in the batch was
    # processed against the retired cue, before the replacement settles. Same three phases, real
    # boundaries; snapshotting after the drain would only ever see the settled state.
    settle = reader.graph.cue.settle

    def traced_settle() -> None:
        trace.observe("cue-conflict", outcome="input-rejected")
        settle()

    monkeypatch.setattr(reader.graph.cue, "settle", traced_settle)
    reader.pump()
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


def _focus_writes(ipc) -> list[str]:
    from saitenka.app.subtitle_render import NATIVE_FOCUS_ID

    return [
        command[2]
        for command in ipc.commands
        if command and command[0] == "osd-overlay" and command[1] == NATIVE_FOCUS_ID
    ]


def _native_with_color_up(make_session):
    """A session whose native focus slot carries a payload — the state every cue change starts
    from once a colored line has been on screen."""
    ipc = _VisibilityIPC()
    ipc.props.update({"sid": 2, "sub-visibility": False})
    renderer = NativeVisibleRenderer()
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(renderer=renderer),
        options=ReaderOptions().with_overrides(prefetch=False),
    )
    presentation = reader.graph.subtitle_presentation
    reader.graph.playback.install_seed({"sub-text": "active"})
    presentation.pipeline.cue_changed(presentation.target(), nonempty=True)
    await_ready(
        lambda: _runtime_settled(reader), "native subtitle setup did not settle", pump=reader.pump
    )
    presentation.cue.install_tokenized(
        TokenizedCue(lines=[[_token("active")]], tokens=[_token("active")], styles=None)
    )
    presentation.cue.replace_geometry(boxes=[WordBox(0, 10, 10, 20, 20)])
    renderer.use_native(presentation.target())
    reader.graph.tooltip.select(0)
    presentation.pipeline.draw_current(presentation.target())
    await_ready(
        lambda: bool(_visible_surfaces(ipc.commands)),
        "native subtitle surface did not settle",
        pump=reader.pump,
    )
    return reader, ipc, presentation, renderer


def test_a_cue_change_whose_first_draw_has_nothing_to_show_does_not_remove_before_it_paints(
    make_session,
) -> None:
    """Inside one cue change the first draw often has boxes and no styles yet, and the styled draw
    follows in the same call. A removal between them was a second command for mpv, and a frame
    composited in the gap showed the line white. The removal is owed only if nothing replaces it."""
    reader, ipc, presentation, renderer = _native_with_color_up(make_session)
    target = presentation.target()
    before = len(_focus_writes(ipc))

    presentation.pipeline.cue_changed(target, nonempty=True)
    renderer.use_native(target)
    reader.graph.tooltip.retire_selection()
    presentation.cue.replace_geometry(boxes=[])
    presentation.pipeline.draw_current(target)  # nothing to show yet
    presentation.cue.replace_geometry(boxes=[WordBox(0, 30, 10, 20, 20)])  # a new place
    reader.graph.tooltip.select(0)
    presentation.pipeline.draw_current(target)  # now there is
    presentation.pipeline.flush_focus(target)

    assert _focus_writes(ipc)[before:] == ["ass-events"]
    reader.close()


def test_a_cue_change_whose_payload_is_already_up_writes_nothing_and_keeps_it(make_session) -> None:
    """The same bytes again — a re-observation of one cue — must neither be rewritten nor
    removed: the deferred removal is cancelled by the match, not paid at the flush."""
    reader, ipc, presentation, renderer = _native_with_color_up(make_session)
    target = presentation.target()
    before = len(_focus_writes(ipc))

    presentation.pipeline.cue_changed(target, nonempty=True)
    renderer.use_native(target)
    reader.graph.tooltip.retire_selection()
    presentation.cue.replace_geometry(boxes=[])
    presentation.pipeline.draw_current(target)
    presentation.cue.replace_geometry(boxes=[WordBox(0, 10, 10, 20, 20)])  # the same place
    reader.graph.tooltip.select(0)
    presentation.pipeline.draw_current(target)
    presentation.pipeline.flush_focus(target)

    assert _focus_writes(ipc)[before:] == []
    reader.close()


def test_a_cue_change_that_never_paints_still_takes_the_previous_color_down(make_session) -> None:
    """The control: deferring the removal is only safe because the flush pays it."""
    reader, ipc, presentation, renderer = _native_with_color_up(make_session)
    target = presentation.target()
    before = len(_focus_writes(ipc))

    reader.graph.playback.dispatch(events.CueTextReplaced("next"))  # another line, as production
    presentation.pipeline.cue_changed(target, nonempty=True)
    renderer.use_native(target)
    reader.graph.tooltip.retire_selection()
    presentation.cue.replace_geometry(boxes=[])
    presentation.pipeline.draw_current(target)
    presentation.pipeline.flush_focus(target)

    assert _focus_writes(ipc)[before:] == ["none"]
    reader.close()


def test_a_cue_re_observed_in_halves_keeps_the_color_it_already_has(make_session) -> None:
    """A seek pre-arms the target's color; mpv then reports that cue as text first and rows a turn
    later. The text half has nothing to measure against, so geometry degrades to pending and the
    draw has nothing to show — and both used to take the cue's own color down, to be rewritten
    when the rows landed. The same line, colored, white, colored again."""
    reader, ipc, presentation, renderer = _native_with_color_up(make_session)
    target = presentation.target()
    before = len(_focus_writes(ipc))

    presentation.pipeline.cue_changed(target, nonempty=True)
    renderer.use_native(target)
    reader.graph.tooltip.retire_selection()
    presentation.cue.replace_geometry(boxes=[])
    renderer.degrade_geometry(target)  # rows not observed yet: pending, not gone
    presentation.pipeline.draw_current(target)
    presentation.pipeline.flush_focus(target)
    assert _focus_writes(ipc)[before:] == []

    presentation.cue.replace_geometry(boxes=[WordBox(0, 10, 10, 20, 20)])  # the rows land
    reader.graph.tooltip.select(0)
    presentation.pipeline.draw_current(target)

    assert _focus_writes(ipc)[before:] == []  # what is up is what the cue wants
    reader.close()


def test_a_removal_that_never_left_the_process_is_still_owed(make_session, monkeypatch) -> None:
    """A focus write the runtime refuses synchronously (disconnected) has not changed the slot.
    Believing it had left the previous cue's color painted until the next reconnect."""
    reader, ipc, presentation, _renderer = _native_with_color_up(make_session)
    target = presentation.target()
    before = len(_focus_writes(ipc))
    submit = ipc.submit_runtime_mpv
    monkeypatch.setattr(ipc, "submit_runtime_mpv", lambda **_kwargs: False)
    presentation.pipeline.clear(target.surfaces, target.ipc)  # refused before it left
    monkeypatch.setattr(ipc, "submit_runtime_mpv", submit)

    presentation.pipeline.clear(target.surfaces, target.ipc)

    assert _focus_writes(ipc)[before:] == ["none"]
    reader.close()


def test_native_geometry_degradation_changes_hits_not_pixel_owner(make_session) -> None:
    ipc = _VisibilityIPC()
    ipc.props.update({"sid": 2, "sub-visibility": False})
    renderer = NativeVisibleRenderer()
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=renderer,
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.graph.playback.install_seed({"sub-text": "active"})
    reader.graph.subtitle_presentation.pipeline.cue_changed(
        reader.graph.subtitle_presentation.target(), nonempty=True
    )
    await_ready(
        lambda: _runtime_settled(reader),
        "native subtitle setup did not settle",
        pump=reader.pump,
    )
    trace = SessionTrace(reader)
    trace.observe("native-cue", outcome="pixels-established")

    # Tokens alongside the boxes, because that is the only pairing production can produce: a box
    # exists because a token was measured, and a draw withholds boxes a cue has no tokens for
    # (`paintable_boxes`). Setting geometry alone builds a state the runtime cannot reach, and the
    # assertion below then rests on it.
    reader.graph.subtitle_presentation.cue.install_tokenized(
        TokenizedCue(lines=[[_token("active")]], tokens=[_token("active")], styles=None)
    )
    reader.graph.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 10, 10, 20, 20)])
    renderer.use_native(reader.graph.subtitle_presentation.target())
    reader.graph.tooltip.select(0)
    reader.graph.subtitle_presentation.pipeline.draw_current(
        reader.graph.subtitle_presentation.target()
    )
    await_ready(
        lambda: bool(_visible_surfaces(ipc.commands)),
        "native subtitle surface did not settle after the geometry became interactive",
        pump=reader.pump,
    )
    trace.observe("geometry-ready", outcome="interaction-ready")
    reader.graph.subtitle_presentation.cue.replace_geometry(boxes=[])
    renderer.degrade_geometry(reader.graph.subtitle_presentation.target())
    await_ready(
        lambda: not _visible_surfaces(ipc.commands),
        "degraded subtitle surface did not settle",
        pump=reader.pump,
    )
    trace.observe("geometry-miss", outcome="interaction-only-degraded")
    # Tokens alongside the boxes, because that is the only pairing production can produce: a box
    # exists because a token was measured, and a draw withholds boxes a cue has no tokens for
    # (`paintable_boxes`). Setting geometry alone builds a state the runtime cannot reach, and the
    # assertion below then rests on it.
    reader.graph.subtitle_presentation.cue.install_tokenized(
        TokenizedCue(lines=[[_token("active")]], tokens=[_token("active")], styles=None)
    )
    reader.graph.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 10, 10, 20, 20)])
    renderer.use_native(reader.graph.subtitle_presentation.target())
    reader.graph.subtitle_presentation.pipeline.draw_current(
        reader.graph.subtitle_presentation.target()
    )
    await_ready(
        lambda: bool(_visible_surfaces(ipc.commands)),
        "recovered subtitle surface did not settle",
        pump=reader.pump,
    )
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
