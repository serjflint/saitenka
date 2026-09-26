"""Alt+o hands the subtitles back to mpv until it is pressed again, on the legacy renderer."""

import logging

import pytest
from util import FakeIPC, await_ready

from saitenka.app import bindings
from saitenka.app.config import ReaderOptions
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import SubtitleRenderer


def _session(make_session):
    ipc = FakeIPC()
    ipc.props.update(
        {"sub-text": "", "sid": 1, "sub-visibility": True, "osd-dimensions": {"w": 1280, "h": 720}}
    )
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(renderer=SubtitleRenderer()),
        options=ReaderOptions().with_overrides(prefetch=False),
    )
    reader.graph.playback.start_session()
    return reader, ipc


def _sub_adds(reader, commands) -> list[tuple]:
    slot = reader.graph.overlay.physical_oid(OverlayId.SUB)
    return [c for c in commands if c[:2] == ("overlay-add", slot)]


def _visibility_writes(commands) -> list[bool]:
    return [c[2] for c in commands if c[:2] == ("set_property", "sub-visibility")]


def _show_cue(reader, ipc, text: str) -> None:
    before = len(ipc.commands)
    ipc.emit({"event": "property-change", "name": "sub-text", "data": text})
    await_ready(
        lambda: bool(_sub_adds(reader, ipc.commands[before:])),
        f"cue {text!r} was not drawn",
        pump=reader.pump,
    )


def _settle(reader) -> None:
    await_ready(
        reader.graph.lifecycle_surfaces.settled, "surfaces did not settle", pump=reader.pump
    )


@pytest.mark.timeout(5)
def test_a_cue_after_hiding_draws_nothing_and_leaves_mpv_subtitles_visible(make_session):
    reader, ipc = _session(make_session)
    _show_cue(reader, ipc, "一つ目")
    reader.command(bindings.OVERLAY_TOGGLE_MSG)
    hidden_at = len(ipc.commands)

    ipc.emit({"event": "property-change", "name": "sub-text", "data": "二つ目"})
    reader.pump()
    _settle(reader)

    after = ipc.commands[hidden_at:]
    assert _sub_adds(reader, after) == []
    assert False not in _visibility_writes(after)


@pytest.mark.timeout(5)
def test_showing_again_redraws_instead_of_restoring_the_pre_hide_raster(make_session):
    reader, ipc = _session(make_session)
    _show_cue(reader, ipc, "一つ目")
    pre_hide_frames = {add[4] for add in _sub_adds(reader, ipc.commands)}
    reader.command(bindings.OVERLAY_TOGGLE_MSG)
    ipc.emit({"event": "property-change", "name": "sub-text", "data": "二つ目"})
    reader.pump()
    _settle(reader)
    shown_at = len(ipc.commands)

    reader.command(bindings.OVERLAY_TOGGLE_MSG)
    _settle(reader)

    after = ipc.commands[shown_at:]
    frames = [add[4] for add in _sub_adds(reader, after)]
    assert frames, "the current cue was not redrawn on show"
    # Re-issuing the overlay's retained state would put the pre-hide cue back up.
    assert pre_hide_frames.isdisjoint(frames)
    assert _visibility_writes(after)[-1] is False


def test_toggling_logs_the_direction_it_went(make_session, caplog):
    reader, _ipc = _session(make_session)

    with caplog.at_level(logging.INFO, logger="saitenka.app.session.builder"):
        reader.command(bindings.OVERLAY_TOGGLE_MSG)
        reader.command(bindings.OVERLAY_TOGGLE_MSG)

    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("overlay ")] == [
        "overlay hidden",
        "overlay shown",
    ]
