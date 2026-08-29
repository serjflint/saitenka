"""Window 1 — the provider-agnostic subtitle-source download picker.

Behavioural, headless (no real mpv / no network): the panel is driven through a FakeIPC and the real
SessionController, the candidate *lister* is a plain thunk (the CLI builds it from enabled_providers), listing
results are applied at the broker-completion seam, and the download click is asserted through the
SubtitleCandidate.download thunk + the start_fetch seam (monkeypatched at its source module, since
sub_picker imports it at call time).
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest
import util
from session_builder import build_session
from util import FakeIPC as RuntimeFakeIPC
from util import runtime_gateway

from saitenka.app import bindings as app_bindings
from saitenka.app import subtitle_modes
from saitenka.app.config import ReaderOptions
from saitenka.app.features.picker import sub_picker
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subselect import SubtitleCandidate
from saitenka.runtime import (
    EffectOutcome,
    Owner,
    events,
    picker,
)

if TYPE_CHECKING:
    from saitenka.app.session.controller import SessionController


class FakeIPC(util.FakeIPC):
    def __init__(self, **props):
        super().__init__()
        self.props.update({"osd-dimensions": {"w": 1920, "h": 1080}, **props})

    def command(self, *args):
        # mpv reflects a set back on the next read; the picker's own tests depend on it.
        if args and args[0] == "set_property":
            self.props[args[1]] = args[2]
        return super().command(*args)


def _reader(**props) -> tuple[SessionController, FakeIPC]:
    ipc = FakeIPC(**props)
    reader = build_session(ipc)
    reader.screen.osd = (1920, 1080)
    return reader, ipc


def _candidate(name: str, *, provider="jimaku", size=1000, match=False, download=None):
    return SubtitleCandidate(
        provider=provider,
        name=name,
        size=size,
        match=match,
        download=download or (lambda: ("/tmp/x.srt", "ok")),
    )


def _lister(candidates, warnings=()):
    return lambda _video: (list(candidates), list(warnings))


def _open(reader: SessionController) -> None:
    """Put the picker up the way production does: through the slice that owns "open"."""
    reader.picker_controller.store.dispatch(events.PickerOpened())


def _adopt(reader: SessionController, *, candidates=(), warnings=(), error=None) -> None:
    """Land a listing on the picker's current generation."""
    sub_picker.apply_listing(
        reader.picker_controller.store,
        reader.picker_controller.redraw,
        reader.picker_controller.state.generation,
        sub_picker.ListingResult(tuple(candidates), tuple(warnings), error),
    )


def _listed(reader: SessionController) -> sub_picker.ListingResult:
    return sub_picker.listing_of(reader.picker_controller.state)


def _close(reader: SessionController) -> None:
    sub_picker.close_picker(
        reader.picker_controller.store, reader.picker_controller.panel, reader.lifecycle_surfaces
    )


def _picker_adds(ipc: FakeIPC) -> list[tuple]:
    return [c for c in ipc.commands if c[:2] == ("overlay-add", OverlayId.PICKER)]


def _drain_until(reader: SessionController, predicate) -> None:
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        reader._drain_events()
        time.sleep(0.001)
    assert predicate()


def test_reopened_picker_publishes_current_listing_before_stale_worker_finishes():
    ipc = RuntimeFakeIPC()
    ipc.props.update({"path": "/v/ep01.mkv", "osd-dimensions": {"w": 1920, "h": 1080}})
    gateway = runtime_gateway(ipc)
    reader = build_session(ipc)
    reader.screen.osd = (1920, 1080)
    old_started = threading.Event()
    old_release = threading.Event()
    calls = 0

    def lister(_video):
        nonlocal calls
        calls += 1
        if calls == 1:
            old_started.set()
            assert old_release.wait(1)
            return [_candidate("old.ass")], []
        return [_candidate("current.ass")], []

    reader.picker_controller.configure_listing(lister)
    try:
        sub_picker.open_picker(
            reader.listing_ports,
            reader.playback_observation.query("path"),
            retire_hover=reader.tooltip_controller.retire_hover,
        )
        assert old_started.wait(1)
        _close(reader)
        sub_picker.open_picker(
            reader.listing_ports,
            reader.playback_observation.query("path"),
            retire_hover=reader.tooltip_controller.retire_hover,
        )
        _drain_until(reader, lambda: bool(_listed(reader).candidates))
        assert _listed(reader).candidates[0].name == "current.ass"

        old_release.set()
        _drain_until(reader, lambda: calls == 2)
        for _ in range(10):
            reader._drain_events()
        assert _listed(reader).candidates[0].name == "current.ass"
    finally:
        old_release.set()
        reader.close()
        gateway.close()


def test_subtitle_picker_lane_rejects_work_beyond_its_bound():
    ipc = RuntimeFakeIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(ipc)
    release = threading.Event()
    started = [threading.Event(), threading.Event()]
    start_lock = threading.Lock()
    start_index = 0

    def lister(_video):
        nonlocal start_index
        with start_lock:
            index = start_index
            start_index += 1
        started[index].set()
        assert release.wait(1)
        return (), ()

    request = sub_picker.ListingRequest(lister, "/v/ep01.mkv")
    submitter = reader.picker_controller.submitter
    assert submitter is not None
    outcomes = []
    try:
        accepted = [
            submitter(
                owner=Owner.SUBTITLE,
                identity=index,
                lane="subtitle-picker",
                request=request,
                on_finished=outcomes.append,
            )
            for index in range(3)
        ]
        assert started[0].wait(1) and started[1].wait(1)
        assert accepted == [True, True, False]
        assert outcomes[-1].outcome is EffectOutcome.REJECTED
    finally:
        release.set()
        reader.close()
        gateway.close()


def test_episode_rebind_closes_loading_picker_and_rejects_old_listing():
    ipc = RuntimeFakeIPC()
    ipc.props.update({"path": "/v/ep01.mkv", "osd-dimensions": {"w": 1920, "h": 1080}})
    gateway = runtime_gateway(ipc)
    reader = build_session(ipc)
    reader.screen.osd = (1920, 1080)
    started = threading.Event()
    release = threading.Event()

    def lister(_video):
        started.set()
        assert release.wait(1)
        return [_candidate("old.ass")], []

    reader.picker_controller.configure_listing(lister)
    try:
        sub_picker.open_picker(
            reader.listing_ports,
            reader.playback_observation.query("path"),
            retire_hover=reader.tooltip_controller.retire_hover,
        )
        assert started.wait(1)
        reader.reslot_ports.rebind_episode()
        assert reader.picker_controller.state.open is False

        release.set()
        for _ in range(10):
            reader._drain_events()
        assert _listed(reader).candidates == ()
    finally:
        release.set()
        reader.close()
        gateway.close()


def test_toggle_without_a_provider_configured_warns_and_stays_closed():
    reader, ipc = _reader(path="/v/ep01.mkv")

    reader.command_runtime.handle(app_bindings.SUB_PICKER_MSG)

    assert reader.picker_controller.state.open is False
    assert not _picker_adds(ipc)


def test_open_lists_candidates_across_providers_and_renders_rows(monkeypatch):
    reader, ipc = _reader(path="/v/[Grp] Show - 01 [1080p].mkv")
    candidates = [
        _candidate("Show 01 [1080p].srt", provider="jimaku", size=2000, match=True),
        _candidate("Show - 01 (Release).ass", provider="tsukihime", size=0),
    ]
    reader.picker_controller.configure_listing(_lister(candidates))
    monkeypatch.setattr(
        sub_picker, "_start_listing", lambda _v, _ports: None
    )  # no real search thread

    sub_picker.open_picker(
        reader.listing_ports,
        reader.playback_observation.query("path"),
        retire_hover=reader.tooltip_controller.retire_hover,
    )
    assert reader.picker_controller.state.open and reader.picker_controller.state.loading

    _adopt(reader, candidates=candidates)

    assert reader.picker_controller.state.loading is False
    assert _listed(reader).candidates == tuple(candidates)
    rows = sub_picker._rows(reader.picker_controller.state)
    assert [row.text for row in rows] == [c.name for c in candidates]
    assert rows[0].status == "jimaku · srt · match"  # provider · format · resolution-match
    assert rows[1].status == "tsukihime · ass"
    assert all(row.click_kind == "picker-download" for row in rows)
    assert _picker_adds(ipc)  # the panel painted


def test_provider_warnings_are_shown_in_the_footer():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.picker_controller.configure_listing(_lister([]))
    _open(reader)

    _adopt(
        reader,
        candidates=(_candidate("a.srt"),),
        warnings=("tsukihime: search truncated",),
    )

    assert _listed(reader).warnings == ("tsukihime: search truncated",)
    footer = sub_picker._footer(
        reader.picker_controller.state, ReaderOptions().keys.sub_picker_key, 1, 1
    )
    assert "tsukihime: search truncated" in footer


def test_listing_error_is_shown_and_leaves_no_candidates():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.picker_controller.configure_listing(_lister([]))
    _open(reader)

    _adopt(reader, error="subtitle search failed: boom")

    assert _listed(reader).error == "subtitle search failed: boom"
    assert _listed(reader).candidates == ()
    assert sub_picker._message(reader.picker_controller.state) == "subtitle search failed: boom"


def test_clicking_a_row_runs_that_candidates_download_and_closes(monkeypatch):
    reader, ipc = _reader(path="/v/ep01.mkv")
    reader.picker_controller.configure_listing(_lister([]))
    ran: list[str] = []
    chosen = _candidate(
        "[Nekomoe] Show - 01 [WebRip].srt",
        download=lambda: ran.append("dl") or ("/tmp/x.srt", "ok"),
    )
    _open(reader)
    _adopt(reader, candidates=(_candidate("other.ass"), chosen))
    reader.picker_controller.redraw()  # populates rect + per-row hitboxes

    fetches: list[tuple] = []
    monkeypatch.setattr(
        subtitle_modes, "start_fetch", lambda _submit, _get, do, **kw: fetches.append((do, kw))
    )

    panel = reader.picker_controller.panel
    rect = panel.rect
    assert rect is not None
    hit = next(h for h in panel.hits if h.kind == "picker-download" and h.value == 1)
    gx, gy = rect[0] + hit.x + hit.w / 2, rect[1] + hit.y + hit.h / 2

    assert reader.picker_controller.on_click(reader.interaction.click_target(), gx, gy) is True
    assert (
        reader.picker_controller.state.open is False
    )  # panel closes; the swap lands from broker completion
    assert ("overlay-remove", OverlayId.PICKER) in ipc.commands
    assert len(fetches) == 1
    do, kwargs = fetches[0]
    assert kwargs["force_select"] is True  # an explicit pick takes over now, even from English
    assert do is chosen.download  # the picker runs exactly the chosen candidate's thunk

    do()
    assert ran == ["dl"]


def test_click_outside_the_panel_is_not_captured():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.picker_controller.configure_listing(_lister([]))
    _open(reader)
    _adopt(reader, candidates=(_candidate("a.srt"),))
    reader.picker_controller.redraw()

    assert reader.picker_controller.on_click(reader.interaction.click_target(), 0, 0) is False


def test_scroll_only_fires_with_the_pointer_over_the_panel():
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.picker_controller.configure_listing(_lister([]))
    _open(reader)
    _adopt(reader, candidates=tuple(_candidate(f"{i}.srt") for i in range(20)))
    reader.picker_controller.redraw()
    rect = reader.picker_controller.panel.rect
    assert rect is not None

    ipc.props["mouse-pos"] = {"hover": True, "x": 0, "y": 0}
    assert reader.picker_controller.scroll(reader.interaction.wheel_step(), 1) is False
    assert reader.picker_controller.state.scroll == 0

    ipc.props["mouse-pos"] = {"hover": True, "x": rect[0] + 5, "y": rect[1] + 5}
    assert reader.picker_controller.scroll(reader.interaction.wheel_step(), 1) is True
    assert reader.picker_controller.state.scroll == picker.ROWS_PER_WHEEL_STEP


def test_suppress_hover_only_over_the_panel():
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.picker_controller.configure_listing(_lister([]))
    _open(reader)
    _adopt(reader, candidates=(_candidate("a.srt"),))
    reader.picker_controller.redraw()
    rect = reader.picker_controller.panel.rect
    assert rect is not None

    ipc.props["mouse-pos"] = {"hover": True, "x": rect[0] + 5, "y": rect[1] + 5}
    assert reader.picker_controller.suppress_hover(reader.interaction.hover_suppression()) is True

    ipc.props["mouse-pos"] = {"hover": True, "x": 0, "y": 0}
    assert reader.picker_controller.suppress_hover(reader.interaction.hover_suppression()) is False


def test_open_picker_wants_the_forced_mouse_section():
    """An open picker must own clicks/wheel (forced section) so they don't fall through to mpv or a
    rival script — same occlusion contract as the tooltip/sidebar."""
    reader, _ipc = _reader(path="/v/ep.mkv")
    assert reader.interaction.router.wants_mouse_capture() is False

    _open(reader)
    assert reader.interaction.router.wants_mouse_capture() is True


def test_toggle_closes_an_open_picker():
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.picker_controller.configure_listing(_lister([]))
    _open(reader)

    _close(reader) if reader.picker_controller.state.open else sub_picker.open_picker(
        reader.listing_ports,
        reader.playback_observation.query("path"),
        retire_hover=reader.tooltip_controller.retire_hover,
    )

    assert reader.picker_controller.state.open is False
    assert ("overlay-remove", OverlayId.PICKER) in ipc.commands


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "—"), (512, "512 B"), (2048, "2 K"), (3 * 1024 * 1024, "3.0 M")],
)
def test_human_size(size, expected):
    assert sub_picker._human_size(size) == expected


def test_the_panel_is_bounded_by_the_screen_it_is_drawn_on() -> None:
    """Every dimension is derived from the OSD, which is exactly the arithmetic that stops tracking
    a resize unnoticed. Checkable at any size now that it takes no session."""
    from saitenka.app.features.picker.sub_picker import ListingResult, picker_panel
    from saitenka.runtime.picker import PickerState

    state = PickerState(open=True, listing=ListingResult((_candidate("a.srt"),), ()))

    for osd in ((1280, 720), (1920, 1080), (3024, 1898)):
        _rendered, x, y, width, height = picker_panel(state, osd=osd, scale=1.0, close_key="Alt+p")

        assert x >= 0 and x + width <= osd[0], f"{osd}: runs off horizontally"
        assert y >= 0 and y + height <= osd[1], f"{osd}: runs off vertically"


def test_the_footer_reports_the_visible_slice_of_a_scrolled_list() -> None:
    """The user's only cue that the list continues past the panel."""
    from saitenka.app.features.picker.sub_picker import _footer
    from saitenka.runtime.picker import PickerState

    assert _footer(PickerState(scroll=4), "Alt+p", total=20, shown=6).startswith("5–10 / 20")


def test_provider_warnings_replace_the_position_readout() -> None:
    """A warning is the more useful thing to say in the same space, and losing it to a row count
    would leave a partial listing looking complete."""
    from saitenka.app.features.picker.sub_picker import ListingResult, _footer
    from saitenka.runtime.picker import PickerState

    state = PickerState(listing=ListingResult((), ("jimaku timed out",)))

    assert "jimaku timed out" in _footer(state, "Alt+p", total=20, shown=6)


def _state(*, open_=True, generation=3):
    return picker.PickerState(open=open_, generation=generation, loading=True)


def _listing(*, candidates=("a",)):
    return sub_picker.ListingResult(error=None, candidates=tuple(candidates), warnings=())


def test_a_listing_for_the_open_generation_is_installed():
    turn = picker.listed(_state(), 3, _listing())

    assert turn.decisions == (picker.ListingAdopted(),)
    assert sub_picker.listing_of(turn.state).candidates == ("a",)
    assert turn.state.loading is False


def test_a_listing_for_a_superseded_generation_is_dropped():
    """Close-then-reopen while a listing is in flight. The result belongs to the closed picker, and
    installing it would repopulate the one the user has since reopened with the old file's tracks."""
    state = _state(generation=4)

    turn = picker.listed(state, 3, _listing())

    assert turn == picker.PickerTurn(state)  # untouched, not merely un-redrawn
    assert turn.state.loading is True  # still waiting on its own listing, not silently marked done


def test_a_listing_arriving_after_the_picker_closed_is_dropped():
    state = _state(open_=False)

    assert picker.listed(state, 3, _listing()) == picker.PickerTurn(state)


def test_retiring_the_picker_bumps_the_generation_that_makes_a_listing_stale():
    turn = picker.retired(_state(generation=3))

    assert turn.decisions == (picker.PickerRetired(),)
    assert turn.state.generation == 4
    assert picker.listed(turn.state, 3, _listing()).decisions == ()


def test_retiring_an_already_closed_picker_decides_nothing():
    """Published so the caller can skip the surface removal — and the generation must not drift, or
    a second close would invalidate a listing the reopened picker legitimately asked for."""
    state = _state(open_=False, generation=3)

    assert picker.retired(state) == picker.PickerTurn(state)


def test_opening_starts_a_generation_that_no_listing_in_flight_can_claim():
    """The bump is the whole reason the machine holds a number: a result for the picker that was up
    a moment ago must not repopulate the one that is up now."""
    before = _state(generation=3)

    turn = picker.opened(before)

    assert turn.state == picker.PickerState(open=True, generation=4, loading=True)
    assert picker.listed(turn.state, 3, _listing()).decisions == ()


def test_a_wheel_notch_stops_at_both_ends_rather_than_wrapping():
    """A list that jumped from the last row back to the first on one more notch reads as a lost
    scroll position, not as a feature."""
    assert picker.clamp_scroll(0, -1, 10) == 0
    assert picker.clamp_scroll(9, 1, 10) == 9
    assert picker.clamp_scroll(0, 1, 10) == picker.ROWS_PER_WHEEL_STEP


def test_an_empty_listing_has_nowhere_to_scroll():
    """Before a listing lands there are no rows; scrolling to a positive offset would render blank."""
    assert picker.clamp_scroll(0, 1, 0) == 0
    assert picker.clamp_scroll(0, 1, 1) == 0
