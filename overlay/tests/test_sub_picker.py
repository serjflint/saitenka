"""Window 1 — the provider-agnostic subtitle-source download picker.

Behavioural, headless (no real mpv / no network): the panel is driven through a FakeIPC and the real
Reader, the candidate *lister* is a plain thunk (the CLI builds it from enabled_providers), listing
results are pushed onto the state queue (bypassing the off-thread search), and the download click is
asserted through the SubtitleCandidate.download thunk + the start_fetch seam (monkeypatched at its
source module, since sub_picker imports it at call time).
"""

from __future__ import annotations

import pytest
from overlay.app import sub_picker, subtitle_modes
from overlay.app.controller import Reader
from overlay.app.overlay_ids import OverlayId
from overlay.app.subselect import SubtitleCandidate


class FakeIPC:
    def __init__(self, **props):
        self.commands: list[tuple] = []
        self.props: dict = {"osd-dimensions": {"w": 1920, "h": 1080}, **props}

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        if args[0] == "set_property":
            self.props[args[1]] = args[2]
        return {"error": "success"}


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

    reader.sub_picker.results.put((candidates, [], None))
    sub_picker.update(reader)

    assert reader.sub_picker.loading is False
    assert reader.sub_picker.candidates == tuple(candidates)
    rows = sub_picker._rows(reader)
    assert [row.text for row in rows] == [c.name for c in candidates]
    assert rows[0].status == "jimaku · match"  # provider tag + resolution match
    assert rows[1].status == "tsukihime"
    assert all(row.click_kind == "picker-download" for row in rows)
    assert _picker_adds(ipc)  # the panel painted


def test_provider_warnings_are_shown_in_the_footer():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True

    reader.sub_picker.results.put(([_candidate("a.srt")], ["tsukihime: search truncated"], None))
    sub_picker.update(reader)

    assert reader.sub_picker.warnings == ("tsukihime: search truncated",)
    footer = sub_picker._footer(reader, total=1, shown=1)
    assert "tsukihime: search truncated" in footer


def test_listing_error_is_shown_and_leaves_no_candidates():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True
    reader.sub_picker.loading = True

    reader.sub_picker.results.put((None, None, "subtitle search failed: boom"))
    sub_picker.update(reader)

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
    sub_picker.redraw(reader)  # populates rect + per-row hitboxes

    fetches: list[tuple] = []
    monkeypatch.setattr(
        subtitle_modes, "start_fetch", lambda _r, do, **kw: fetches.append((do, kw))
    )

    rect = reader.sub_picker.rect
    assert rect is not None
    hit = next(h for h in reader.sub_picker.hits if h.kind == "picker-download" and h.value == 1)
    gx, gy = rect[0] + hit.x + hit.w / 2, rect[1] + hit.y + hit.h / 2

    assert sub_picker.on_click(reader, gx, gy) is True
    assert reader.sub_picker.open is False  # panel closes; the swap lands via apply_fetch_results
    assert ("overlay-remove", OverlayId.PICKER) in ipc.commands
    assert len(fetches) == 1
    do, kwargs = fetches[0]
    assert kwargs["replace"] is True
    assert do is chosen.download  # the picker runs exactly the chosen candidate's thunk

    do()
    assert ran == ["dl"]


def test_click_outside_the_panel_is_not_captured():
    reader, _ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.candidates = (_candidate("a.srt"),)
    reader.sub_picker.open = True
    sub_picker.redraw(reader)

    assert sub_picker.on_click(reader, 0, 0) is False


def test_scroll_only_fires_with_the_pointer_over_the_panel():
    reader, ipc = _reader(path="/v/ep.mkv")
    reader.configure_sub_picker(_lister([]))
    reader.sub_picker.open = True
    reader.sub_picker.candidates = tuple(_candidate(f"{i}.srt") for i in range(20))
    sub_picker.redraw(reader)
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
    sub_picker.redraw(reader)
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

    sub_picker.toggle(reader)

    assert reader.sub_picker.open is False
    assert ("overlay-remove", OverlayId.PICKER) in ipc.commands


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "—"), (512, "512 B"), (2048, "2 K"), (3 * 1024 * 1024, "3.0 M")],
)
def test_human_size(size, expected):
    assert sub_picker._human_size(size) == expected
