"""Window 1 — the provider-agnostic subtitle-source download picker.

Behavioural, headless (no real mpv / no network): the panel is driven through a FakeIPC and the real
Reader, the candidate *lister* is a plain thunk (the CLI builds it from enabled_providers), listing
results are applied at the broker-completion seam, and the download click is asserted through the
SubtitleCandidate.download thunk + the start_fetch seam (monkeypatched at its source module, since
sub_picker imports it at call time).
"""

from __future__ import annotations

import threading
import time

import pytest
from util import FakeIPC as RuntimeFakeIPC
from util import runtime_gateway

from saitenka.app import sub_picker, subtitle_modes
from saitenka.app.bindings import HELP_CLOSE_MSG, SUB_PICKER_MSG
from saitenka.app.controller import Reader
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subselect import SubtitleCandidate
from saitenka.runtime import (
    CommandHandled,
    CommandOutcome,
    CommandReason,
    EffectOutcome,
    Owner,
)


class FakeIPC:
    def __init__(self, **props):
        self.commands: list[tuple] = []
        self.events: list[dict] = []
        self.runtime_outcomes: list[CommandHandled] = []
        self.props: dict = {"osd-dimensions": {"w": 1920, "h": 1080}, **props}

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        if args[0] == "set_property":
            self.props[args[1]] = args[2]
        return {"error": "success"}

    def drain_events(self, *_args, **_kwargs) -> list[dict]:
        events, self.events = self.events, []
        return events

    def publish_legacy_command_outcome(self, outcome: CommandHandled) -> None:
        self.runtime_outcomes.append(outcome)


def _reader(**props) -> tuple[Reader, FakeIPC]:
    ipc = FakeIPC(**props)
    reader = Reader(ipc)
    reader.osd = (1920, 1080)
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


def _picker_adds(ipc: FakeIPC) -> list[tuple]:
    return [c for c in ipc.commands if c[:2] == ("overlay-add", OverlayId.PICKER)]


def _drain_until(reader: Reader, predicate) -> None:
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        reader._drain_events()
        time.sleep(0.001)
    assert predicate()


def test_reopened_picker_publishes_current_listing_before_stale_worker_finishes():
    ipc = RuntimeFakeIPC()
    ipc.props.update({"path": "/v/ep01.mkv", "osd-dimensions": {"w": 1920, "h": 1080}})
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc)
    reader.osd = (1920, 1080)
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

    reader.configure_sub_picker(lister)
    try:
        sub_picker.open_picker(reader)
        assert old_started.wait(1)
        sub_picker.close_picker(reader)
        sub_picker.open_picker(reader)
        _drain_until(reader, lambda: bool(reader.sub_picker.candidates))
        assert reader.sub_picker.candidates[0].name == "current.ass"

        old_release.set()
        _drain_until(reader, lambda: calls == 2)
        for _ in range(10):
            reader._drain_events()
        assert reader.sub_picker.candidates[0].name == "current.ass"
    finally:
        old_release.set()
        reader.close()
        gateway.close()


def test_subtitle_picker_lane_rejects_work_beyond_its_bound():
    ipc = RuntimeFakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc)
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
    submitter = reader._sub_picker_submit
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
    reader = Reader(ipc)
    reader.osd = (1920, 1080)
    started = threading.Event()
    release = threading.Event()

    def lister(_video):
        started.set()
        assert release.wait(1)
        return [_candidate("old.ass")], []

    reader.configure_sub_picker(lister)
    try:
        sub_picker.open_picker(reader)
        assert started.wait(1)
        reader.rebind_episode()
        assert reader.sub_picker.open is False

        release.set()
        for _ in range(10):
            reader._drain_events()
        assert reader.sub_picker.candidates == ()
    finally:
        release.set()
        reader.close()
        gateway.close()


def test_toggle_without_a_provider_configured_warns_and_stays_closed():
    reader, ipc = _reader(path="/v/ep01.mkv")

    reader.toggle_sub_picker()

    assert reader.sub_picker.open is False
    assert not _picker_adds(ipc)


def test_open_lists_candidates_across_providers_and_renders_rows(monkeypatch):
    reader, ipc = _reader(path="/v/[Grp] Show - 01 [1080p].mkv")
    candidates = [
        _candidate("Show 01 [1080p].srt", provider="jimaku", size=2000, match=True),
        _candidate("Show - 01 (Release).ass", provider="tsukihime", size=0),
    ]
    reader.configure_sub_picker(_lister(candidates))
    monkeypatch.setattr(sub_picker, "_start_listing", lambda _r, _v: None)  # no real search thread

    sub_picker.open_picker(reader)
    assert reader.sub_picker.open and reader.sub_picker.loading

    sub_picker.apply_listing(
        reader, reader.sub_picker.generation, sub_picker.ListingResult(tuple(candidates), ())
    )

    assert reader.sub_picker.loading is False
    assert reader.sub_picker.candidates == tuple(candidates)
    rows = sub_picker._rows(reader.sub_picker)
    assert [row.text for row in rows] == [c.name for c in candidates]
    assert rows[0].status == "jimaku · srt · match"  # provider · format · resolution-match
    assert rows[1].status == "tsukihime · ass"
    assert all(row.click_kind == "picker-download" for row in rows)
    assert _picker_adds(ipc)  # the panel painted


def test_provider_warnings_are_shown_in_the_footer():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True

    sub_picker.apply_listing(
        reader,
        reader.sub_picker.generation,
        sub_picker.ListingResult((_candidate("a.srt"),), ("tsukihime: search truncated",)),
    )

    assert reader.sub_picker.warnings == ("tsukihime: search truncated",)
    footer = sub_picker._footer(reader.sub_picker, reader.sub_picker_key, 1, 1)
    assert "tsukihime: search truncated" in footer


def test_listing_error_is_shown_and_leaves_no_candidates():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True
    reader.sub_picker.loading = True

    sub_picker.apply_listing(
        reader,
        reader.sub_picker.generation,
        sub_picker.ListingResult((), (), "subtitle search failed: boom"),
    )

    assert reader.sub_picker.error == "subtitle search failed: boom"
    assert reader.sub_picker.candidates == ()
    assert sub_picker._message(reader.sub_picker) == "subtitle search failed: boom"


def test_clicking_a_row_runs_that_candidates_download_and_closes(monkeypatch):
    reader, ipc = _reader(path="/v/ep01.mkv")
    reader.configure_sub_picker(_lister([]))
    ran: list[str] = []
    chosen = _candidate(
        "[Nekomoe] Show - 01 [WebRip].srt",
        download=lambda: ran.append("dl") or ("/tmp/x.srt", "ok"),
    )
    reader.sub_picker.candidates = (_candidate("other.ass"), chosen)
    reader.sub_picker.open = True
    reader.redraw_sub_picker()  # populates rect + per-row hitboxes

    fetches: list[tuple] = []
    monkeypatch.setattr(
        subtitle_modes, "start_fetch", lambda _r, do, **kw: fetches.append((do, kw))
    )

    rect = reader.sub_picker.rect
    assert rect is not None
    hit = next(h for h in reader.sub_picker.hits if h.kind == "picker-download" and h.value == 1)
    gx, gy = rect[0] + hit.x + hit.w / 2, rect[1] + hit.y + hit.h / 2

    assert sub_picker.on_click(reader, gx, gy) is True
    assert reader.sub_picker.open is False  # panel closes; the swap lands from broker completion
    assert ("overlay-remove", OverlayId.PICKER) in ipc.commands
    assert len(fetches) == 1
    do, kwargs = fetches[0]
    assert kwargs["force_select"] is True  # an explicit pick takes over now, even from English
    assert do is chosen.download  # the picker runs exactly the chosen candidate's thunk

    do()
    assert ran == ["dl"]


def test_click_outside_the_panel_is_not_captured():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.candidates = (_candidate("a.srt"),)
    reader.sub_picker.open = True
    reader.redraw_sub_picker()

    assert sub_picker.on_click(reader, 0, 0) is False


def test_scroll_only_fires_with_the_pointer_over_the_panel():
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True
    reader.sub_picker.candidates = tuple(_candidate(f"{i}.srt") for i in range(20))
    reader.redraw_sub_picker()
    rect = reader.sub_picker.rect
    assert rect is not None

    ipc.props["mouse-pos"] = {"x": 0, "y": 0}
    assert sub_picker.scroll(reader, 1) is False
    assert reader.sub_picker.scroll == 0

    ipc.props["mouse-pos"] = {"x": rect[0] + 5, "y": rect[1] + 5}
    assert sub_picker.scroll(reader, 1) is True
    assert reader.sub_picker.scroll == sub_picker.ROWS_PER_WHEEL_STEP


def test_suppress_hover_only_over_the_panel():
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True
    reader.sub_picker.candidates = (_candidate("a.srt"),)
    reader.redraw_sub_picker()
    rect = reader.sub_picker.rect
    assert rect is not None

    ipc.props["mouse-pos"] = {"x": rect[0] + 5, "y": rect[1] + 5}
    assert sub_picker.suppress_hover(reader) is True

    ipc.props["mouse-pos"] = {"x": 0, "y": 0}
    assert sub_picker.suppress_hover(reader) is False


def test_open_picker_wants_the_forced_mouse_section():
    """An open picker must own clicks/wheel (forced section) so they don't fall through to mpv or a
    rival script — same occlusion contract as the tooltip/sidebar."""
    reader, _ipc = _reader(path="/v/ep.mkv")
    assert reader._wants_mouse_capture() is False

    reader.sub_picker.open = True
    assert reader._wants_mouse_capture() is True


def test_toggle_closes_an_open_picker():
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True

    sub_picker.close_picker(reader) if reader.sub_picker.open else sub_picker.open_picker(reader)

    assert reader.sub_picker.open is False
    assert ("overlay-remove", OverlayId.PICKER) in ipc.commands


def test_queued_picker_presses_open_once_after_startup(monkeypatch):
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    monkeypatch.setattr(sub_picker, "_start_listing", lambda _r, _v: None)
    ipc.events = [
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
    ]

    reader._drain_events()

    assert reader.sub_picker.open
    assert len(_picker_adds(ipc)) == 1
    assert ipc.runtime_outcomes[-1] == CommandHandled(
        SUB_PICKER_MSG,
        Owner.INTERACTION,
        CommandOutcome.SUPPRESSED,
        reason=CommandReason.LEGACY_REPEAT,
    )


def test_picker_press_after_help_close_is_not_coalesced(monkeypatch):
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    monkeypatch.setattr(sub_picker, "_start_listing", lambda _r, _v: None)
    reader._help_open = True
    ipc.events = [
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
        {"event": "client-message", "args": [HELP_CLOSE_MSG]},
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
    ]

    reader._drain_events()

    assert not reader._help_open
    assert reader.sub_picker.open


def test_file_load_separates_picker_toggle_batches(monkeypatch):
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    monkeypatch.setattr(sub_picker, "_start_listing", lambda _r, _v: None)
    ipc.events = [
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
        {"event": "file-loaded"},
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
    ]

    reader._drain_events()

    assert not reader.sub_picker.open
    assert ("overlay-remove", OverlayId.PICKER) in ipc.commands


def test_property_change_does_not_split_startup_picker_presses(monkeypatch):
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    monkeypatch.setattr(sub_picker, "_start_listing", lambda _r, _v: None)
    ipc.events = [
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
        {"event": "property-change", "name": "time-pos", "data": 1.0},
        {"event": "client-message", "args": [SUB_PICKER_MSG]},
    ]

    reader._drain_events()

    assert reader.sub_picker.open
    assert len(_picker_adds(ipc)) == 1


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "—"), (512, "512 B"), (2048, "2 K"), (3 * 1024 * 1024, "3.0 M")],
)
def test_human_size(size, expected):
    assert sub_picker._human_size(size) == expected


def test_the_panel_is_bounded_by_the_screen_it_is_drawn_on() -> None:
    """Every dimension is derived from the OSD, which is exactly the arithmetic that stops tracking
    a resize unnoticed. Checkable at any size now that it takes no session."""
    from saitenka.app.sub_picker import PickerState, picker_panel

    state = PickerState()
    state.open = True
    state.candidates = (_candidate("a.srt"),)

    for osd in ((1280, 720), (1920, 1080), (3024, 1898)):
        _rendered, x, y, width, height = picker_panel(state, osd=osd, scale=1.0, close_key="Alt+p")

        assert x >= 0 and x + width <= osd[0], f"{osd}: runs off horizontally"
        assert y >= 0 and y + height <= osd[1], f"{osd}: runs off vertically"


def test_the_footer_reports_the_visible_slice_of_a_scrolled_list() -> None:
    """The user's only cue that the list continues past the panel."""
    from saitenka.app.sub_picker import PickerState, _footer

    state = PickerState()
    state.scroll = 4

    assert _footer(state, "Alt+p", total=20, shown=6).startswith("5–10 / 20")


def test_provider_warnings_replace_the_position_readout() -> None:
    """A warning is the more useful thing to say in the same space, and losing it to a row count
    would leave a partial listing looking complete."""
    from saitenka.app.sub_picker import PickerState, _footer

    state = PickerState()
    state.warnings = ("jimaku timed out",)

    assert "jimaku timed out" in _footer(state, "Alt+p", total=20, shown=6)
