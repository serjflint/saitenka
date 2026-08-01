"""Whole-episode subtitle sidebar behavior at the Reader seam."""

import pytest
from PIL import Image

from overlay.app import sidebar
from overlay.app.backlog import BacklogStore, Capture
from overlay.app.controller import Reader
from overlay.app.episode_analysis import analyze_cues
from overlay.app.scoring import Scorer
from overlay.app.sub_index import SubCue, SubIndex
from overlay.app.subtitles import (
    SidebarAction,
    SidebarHitBox,
    SidebarRender,
    SidebarRow,
    render_sidebar,
)
from overlay.app.wordlists import KnownWords


class FakeIPC:
    def __init__(self, props=None):
        self.props = props or {}
        self.commands = []

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}


class FakeOverlay:
    def __init__(self):
        self.shown = []
        self.hidden = []

    def show(self, image, x=0, y=0, oid=0):
        self.shown.append((image, x, y, oid))

    def hide(self, oid=0):
        self.hidden.append(oid)


def _reader(cue_count=20, *, active=0, props=None):
    ipc = FakeIPC(props)
    reader = Reader(ipc)
    reader.ov = FakeOverlay()
    cues = [SubCue(float(i), float(i) + 0.8, f"cue {i}") for i in range(cue_count)]
    reader._sub_index = SubIndex(cues)
    reader.sub_text = f"cue {active}"
    return reader, ipc


def _capture_render(monkeypatch):
    calls = []

    def render(rows, **kwargs):
        calls.append((rows, kwargs))
        return SidebarRender(Image.new("RGBA", (1, 1)), (), 1)

    monkeypatch.setattr(sidebar, "render_sidebar", render)
    return calls


def test_toggle_opens_centered_on_active_cue_without_pausing(monkeypatch):
    reader, ipc = _reader(active=12)
    calls = _capture_render(monkeypatch)

    sidebar.toggle(reader)

    assert reader._sidebar_open is True
    assert [row.value for row in calls[-1][0]] == list(range(8, 17))
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_active_row_uses_timing_to_disambiguate_repeated_text():
    reader, _ipc = _reader(props={"sub-start": 5.2, "time-pos": 5.3})
    reader._sub_index = SubIndex([SubCue(1.0, 2.0, "same line"), SubCue(5.0, 6.0, "same line")])
    reader.sub_text = "same line"

    assert sidebar._active_index(reader) == 1


def test_manual_scroll_holds_then_returns_to_active_cue(monkeypatch):
    reader, _ipc = _reader(active=10, props={"mouse-pos": {"x": 1000, "y": 100}})
    calls = _capture_render(monkeypatch)
    now = [10.0]
    monkeypatch.setattr(sidebar.time, "monotonic", lambda: now[0])
    sidebar.toggle(reader)
    reader._sidebar_rect = (900, 50, 360, 600)

    assert sidebar.scroll(reader, -3) is True
    held_scroll = reader._sidebar_scroll
    reader.sub_text = "cue 18"
    sidebar.update(reader)
    assert reader._sidebar_scroll == held_scroll

    before_expiry = len(calls)
    now[0] = 12.0
    sidebar.update(reader)
    assert reader._sidebar_scroll == 14
    assert len(calls) == before_expiry + 1


def test_clicking_cue_seeks_without_changing_pause_state(monkeypatch):
    reader, ipc = _reader(active=3)
    _capture_render(monkeypatch)
    reader._sidebar_open = True
    reader._sidebar_rect = (100, 100, 400, 500)
    reader._sidebar_hits = (SidebarHitBox("seek", 7, 0, 0, 200, 40),)

    assert sidebar.on_click(reader, 110, 110) is True
    assert ("set_property", "time-pos", 7.0) in ipc.commands
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


@pytest.mark.parametrize(
    ("kind", "method"), [("bookmark", "toggle_bookmark"), ("mine", "mine_current")]
)
def test_active_cue_actions_use_existing_reader_flows(kind, method, monkeypatch):
    reader, _ipc = _reader(active=3)
    _capture_render(monkeypatch)
    invoked = []
    monkeypatch.setattr(reader, method, lambda: invoked.append(method))
    reader._sidebar_open = True
    reader._sidebar_rect = (100, 100, 400, 500)
    reader._sidebar_hits = (SidebarHitBox(kind, 3, 0, 0, 40, 40),)

    sidebar.on_click(reader, 110, 110)

    assert invoked == [method]


def test_english_rows_are_plain_and_skip_japanese_analysis(monkeypatch):
    reader, _ipc = _reader(cue_count=1)
    reader.subtitle_language = "en"
    reader.scorer = object()
    monkeypatch.setattr(sidebar, "tokenize", lambda _text: (_ for _ in ()).throw(AssertionError))

    rows, total = sidebar._track_rows(reader, 0, 1, 0)

    assert total == 1
    assert rows[0].parts == (("cue 0", sidebar.PLAIN),)


def test_rows_use_shared_episode_analysis_when_ready():
    reader, _ipc = _reader(cue_count=1)
    reader._sub_index = SubIndex([SubCue(0.0, 1.0, "私は本を読む。")])
    reader.sub_text = "私は本を読む。"
    reader.scorer = Scorer(known=KnownWords.from_set(["私", "本"]))
    reader._episode_analysis = analyze_cues(list(reader._sub_index.cues), reader.scorer)

    rows, _total = sidebar._track_rows(reader, 0, 1, 0)

    assert rows[0].status == "N+1"


def test_track_change_clears_stale_analysis_before_sidebar_redraw(monkeypatch):
    reader, _ipc = _reader(cue_count=1)
    reader.jp_sid = 1
    reader.scorer = Scorer(known=KnownWords.from_set(["私", "本"]))
    reader._sub_index = SubIndex([SubCue(0.0, 1.0, "私は本を読む。")])
    reader._episode_analysis = analyze_cues(list(reader._sub_index.cues), reader.scorer)
    reader._sidebar_open = True
    reader._loading = True
    calls = _capture_render(monkeypatch)
    monkeypatch.setattr(
        "overlay.app.subnav.load_index",
        lambda _path: SubIndex([SubCue(0.0, 1.0, "猫です。")]),
    )

    reader.load_sub_index("new-track.srt")

    assert calls[-1][0][0].text == "猫です。"
    assert calls[-1][0][0].status is None


def test_sidebar_hover_suppresses_tooltip_without_pausing(monkeypatch):
    reader, ipc = _reader(props={"mouse-pos": {"x": 110, "y": 110}})
    reader._sidebar_open = True
    reader._sidebar_rect = (100, 100, 400, 500)
    monkeypatch.setattr(
        "overlay.app.tooltip.update_hover",
        lambda _reader: (_ for _ in ()).throw(AssertionError("tooltip reached")),
    )

    reader._update_hover()

    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_backlog_candidate_hides_cue_text_until_explicit_relink(tmp_path, monkeypatch):
    original = tmp_path / "[Group] Show - 01 [1080p].mkv"
    renamed = tmp_path / "Show S01E01 remux.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(renamed)})
    store = BacklogStore(tmp_path / "backlog.sqlite")
    entry = store.toggle_capture(
        Capture(str(original), 0.0, 0.8, jp_text="秘密の字幕", en_text="secret subtitle")
    )
    reader._backlog_store = store

    rows = sidebar._summary_rows(reader)

    candidate = next(row for row in rows if row.actions)
    assert candidate.actions == (SidebarAction("✓", "relink", entry.media_id),)
    assert "秘密の字幕" not in "".join(row.text for row in rows)
    assert store.entries_for_path(renamed) == []

    _capture_render(monkeypatch)
    reader._sidebar_open = True
    reader._sidebar_rect = (0, 0, 400, 500)
    reader._sidebar_hits = (SidebarHitBox("relink", entry.media_id, 0, 0, 100, 40),)
    sidebar.on_click(reader, 10, 10)

    assert store.entries_for_path(renamed) == [entry]
    assert store.media(entry.media_id).original_basename == original.name
    matched_rows = sidebar._summary_rows(reader)
    expanded = next(row for row in matched_rows if row.click_kind == "backlog-seek")
    assert (expanded.text, expanded.status) == ("秘密の字幕", "open")

    reader._sidebar_rect = (0, 0, 400, 500)
    reader._sidebar_hits = (SidebarHitBox("backlog-seek", entry.id, 0, 0, 100, 40),)
    sidebar.on_click(reader, 10, 10)
    assert ("set_property", "time-pos", 0.0) in reader.ipc.commands


def test_mining_marks_matching_backlog_cue_without_creating_a_store(tmp_path, monkeypatch):
    video = tmp_path / "Show - 01.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    reader._sidebar_open = True
    store = BacklogStore(tmp_path / "backlog.sqlite")
    entry = store.toggle_capture(Capture(str(video), 0.0, 0.8, jp_text="cue 0"))
    reader._backlog_store = store
    _capture_render(monkeypatch)

    sidebar.mark_active_mined(reader)

    assert store.entry(entry.id).status == "mined"


def test_renderer_windows_rows_and_bounds_hitboxes():
    rows = [
        SidebarRow(
            value=i,
            timestamp=f"00:{i:02d}",
            text=f"cue {i}",
            click_kind="seek",
            actions=(SidebarAction("B", "bookmark", i),),
        )
        for i in range(10)
    ]

    rendered = render_sidebar(rows, width=400, height=180, view="track", total=10, first=0)

    assert rendered.row_capacity == 1
    assert [(hit.kind, hit.value) for hit in rendered.hitboxes] == [
        ("view:track", 0),
        ("view:backlog", 0),
        ("bookmark", 0),
        ("seek", 0),
    ]
    assert all(
        0 <= hit.x < rendered.image.width
        and 0 <= hit.y < rendered.image.height
        and hit.x + hit.w <= rendered.image.width
        and hit.y + hit.h <= rendered.image.height
        for hit in rendered.hitboxes
    )
