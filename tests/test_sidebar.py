"""Whole-episode subtitle sidebar behavior at the Reader seam."""

import pytest
import util
from PIL import Image

from saitenka.app import sidebar
from saitenka.app.backlog import BacklogStore, Capture
from saitenka.app.controller import Reader
from saitenka.app.episode_analysis import analyze_cues
from saitenka.app.scoring import Scorer
from saitenka.app.subtitles import (
    SidebarAction,
    SidebarHitBox,
    SidebarRender,
    SidebarRow,
    render_sidebar,
)
from saitenka.app.wordlists import KnownWords
from saitenka.subtitles import Cue, CueIndex


class FakeIPC(util.FakeIPC):
    def __init__(self, props=None):
        super().__init__()
        self.props.update(props or {})


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
    cues = [Cue(float(i), float(i) + 0.8, f"cue {i}") for i in range(cue_count)]
    reader._sub_index = CueIndex(cues)
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

    sidebar.set_open(reader, open=not reader.sidebar.open)

    assert reader.sidebar.open is True
    assert [row.value for row in calls[-1][0]] == list(range(8, 17))
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_active_row_uses_timing_to_disambiguate_repeated_text():
    reader, _ipc = _reader(props={"sub-start": 5.2, "time-pos": 5.3})
    reader._sub_index = CueIndex([Cue(1.0, 2.0, "same line"), Cue(5.0, 6.0, "same line")])
    reader.sub_text = "same line"

    assert sidebar._active_index(reader) == 1


def test_manual_scroll_holds_then_returns_to_active_cue(monkeypatch):
    """The hold ends on its own deadline, and that deadline re-runs the follow itself. Waiting for
    the next `update` would leave the sidebar off-target for as long as the cue happened to last."""
    reader, ipc = _reader(active=10, props={"mouse-pos": {"x": 1000, "y": 100}})
    calls = _capture_render(monkeypatch)
    sidebar.set_open(reader, open=not reader.sidebar.open)
    reader.sidebar.rect = (900, 50, 360, 600)

    assert sidebar.scroll(reader, -3) is True
    held_scroll = reader.sidebar.scroll
    reader.sub_text = "cue 18"
    sidebar.update(reader)
    assert reader.sidebar.scroll == held_scroll

    before_expiry = len(calls)
    assert ipc.fire_runtime_timer("lifecycle:sidebar-manual-hold")

    assert reader.sidebar.scroll == 14
    assert len(calls) == before_expiry + 1


def test_clicking_cue_seeks_without_changing_pause_state(monkeypatch):
    reader, ipc = _reader(active=3)
    _capture_render(monkeypatch)
    reader.sidebar.open = True
    reader.sidebar.rect = (100, 100, 400, 500)
    reader.sidebar.hits = (SidebarHitBox("seek", 7, 0, 0, 200, 40),)

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
    reader.sidebar.open = True
    reader.sidebar.rect = (100, 100, 400, 500)
    reader.sidebar.hits = (SidebarHitBox(kind, 3, 0, 0, 40, 40),)

    sidebar.on_click(reader, 110, 110)

    assert invoked == [method]


@pytest.mark.parametrize(
    ("kind", "method"), [("bookmark", "toggle_bookmark"), ("mine", "mine_current")]
)
def test_active_cue_action_still_fires_when_the_active_cue_drifted(kind, method, monkeypatch):
    """#252: B/+ render only on the active row, so re-gating the click on `hit.value == active` dropped
    it silently when playback advanced a cue between redraw and click. The click must fire regardless of
    the clicked row's value vs the now-active index — parity with the ungated Alt+b."""
    reader, _ipc = _reader(active=9)  # the live cue has moved on since the row was drawn
    _capture_render(monkeypatch)
    invoked = []
    monkeypatch.setattr(reader, method, lambda: invoked.append(method))
    reader.sidebar.open = True
    reader.sidebar.rect = (100, 100, 400, 500)
    reader.sidebar.hits = (SidebarHitBox(kind, 3, 0, 0, 40, 40),)  # row 3, no longer active

    sidebar.on_click(reader, 110, 110)

    assert invoked == [method]  # not the old silent no-op


def test_sidebar_bookmark_and_keybind_route_to_the_same_flow(monkeypatch):
    """Parity pin: the sidebar B button and the Alt+b keybind both funnel into ``toggle_bookmark`` — so
    the sidebar path can't silently diverge from the keybind again."""
    from saitenka.app.bindings import BOOKMARK_MSG

    reader, _ipc = _reader(active=3)
    _capture_render(monkeypatch)
    invoked = []
    monkeypatch.setattr(reader, "toggle_bookmark", lambda: invoked.append("toggle"))

    reader._handle(BOOKMARK_MSG)  # the Alt+b path
    reader.sidebar.open = True
    reader.sidebar.rect = (100, 100, 400, 500)
    reader.sidebar.hits = (SidebarHitBox("bookmark", 3, 0, 0, 40, 40),)
    sidebar.on_click(reader, 110, 110)  # the sidebar-button path

    assert invoked == ["toggle", "toggle"]  # one flow, two entry points


def test_english_rows_are_plain_and_skip_japanese_analysis(monkeypatch):
    reader, _ipc = _reader(cue_count=1)
    reader.subtitle_language = "en"
    reader.scorer = object()
    monkeypatch.setattr(
        reader.tokenizer, "tokenize", lambda _text: (_ for _ in ()).throw(AssertionError)
    )

    rows, total = sidebar._track_rows(reader, 0, 1, 0)

    assert total == 1
    assert rows[0].parts == (("cue 0", sidebar.PLAIN),)


def test_rows_use_shared_episode_analysis_when_ready():
    reader, _ipc = _reader(cue_count=1)
    reader._sub_index = CueIndex([Cue(0.0, 1.0, "私は本を読む。")])
    reader.sub_text = "私は本を読む。"
    reader.scorer = Scorer(known=KnownWords.from_set(["私", "本"]))
    reader.analysis.current = analyze_cues(
        list(reader._sub_index.cues), reader.scorer, reader.tokenizer
    )

    rows, _total = sidebar._track_rows(reader, 0, 1, 0)

    assert rows[0].status == "N+1"


def test_track_change_clears_stale_analysis_before_sidebar_redraw(monkeypatch):
    reader, _ipc = _reader(cue_count=1)
    reader.jp_sid = 1
    reader.scorer = Scorer(known=KnownWords.from_set(["私", "本"]))
    reader._sub_index = CueIndex([Cue(0.0, 1.0, "私は本を読む。")])
    reader.analysis.current = analyze_cues(
        list(reader._sub_index.cues), reader.scorer, reader.tokenizer
    )
    reader.sidebar.open = True
    reader._loading = True
    calls = _capture_render(monkeypatch)
    monkeypatch.setattr(
        "saitenka.app.subnav.load_index",
        lambda _path: CueIndex([Cue(0.0, 1.0, "猫です。")]),
    )

    reader.load_sub_index("new-track.srt")

    assert calls[-1][0][0].text == "猫です。"
    assert calls[-1][0][0].status is None


def test_sidebar_hover_suppresses_tooltip_without_pausing(monkeypatch):
    reader, ipc = _reader(props={"mouse-pos": {"x": 110, "y": 110}})
    reader.sidebar.open = True
    reader.sidebar.rect = (100, 100, 400, 500)
    monkeypatch.setattr(
        "saitenka.app.tooltip.update_hover",
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
    reader.sidebar.open = True
    reader.sidebar.rect = (0, 0, 400, 500)
    reader.sidebar.hits = (SidebarHitBox("relink", entry.media_id, 0, 0, 100, 40),)
    sidebar.on_click(reader, 10, 10)

    assert store.entries_for_path(renamed) == [entry]
    assert store.media(entry.media_id).original_basename == original.name
    matched_rows = sidebar._summary_rows(reader)
    expanded = next(row for row in matched_rows if row.click_kind == "backlog-seek")
    assert (expanded.text, expanded.status) == ("秘密の字幕", "open")

    reader.sidebar.rect = (0, 0, 400, 500)
    reader.sidebar.hits = (SidebarHitBox("backlog-seek", entry.id, 0, 0, 100, 40),)
    sidebar.on_click(reader, 10, 10)
    assert ("set_property", "time-pos", 0.0) in reader.ipc.commands


def test_mining_marks_matching_backlog_cue_without_creating_a_store(tmp_path, monkeypatch):
    video = tmp_path / "Show - 01.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    reader.sidebar.open = True
    store = BacklogStore(tmp_path / "backlog.sqlite")
    entry = store.toggle_capture(Capture(str(video), 0.0, 0.8, jp_text="cue 0"))
    reader._backlog_store = store
    _capture_render(monkeypatch)

    sidebar.mark_active_mined(reader)

    assert store.entry(entry.id).status == "mined"


def test_mine_tab_lists_this_episodes_mined_cards(tmp_path):
    from saitenka.app.mined_store import MinedCardStore

    video = tmp_path / "Show - 03.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    store = MinedCardStore(tmp_path / "mined.sqlite")
    store.record(
        note_id=111,
        video_path=str(video),
        cue_start=0.0,
        cue_end=0.8,
        expression="本",
        reading="ほん",
    )
    store.record(
        note_id=222,
        video_path=str(video),
        cue_start=1.0,
        cue_end=1.5,
        expression="猫",
        reading="ねこ",
    )
    store.record(  # a sibling episode must not leak in
        note_id=333,
        video_path=str(tmp_path / "Show - 04.mkv"),
        cue_start=0.0,
        cue_end=0.8,
        expression="犬",
        reading="いぬ",
    )
    reader._mined_store = store

    rows = sidebar._mine_rows(reader)

    assert [(row.value, row.click_kind, row.status) for row in rows] == [
        (111, "mine-open", "mined"),
        (222, "mine-open", "mined"),
    ]
    assert "本" in rows[0].text and "ほん" in rows[0].text
    assert rows[0].active is True  # its cue span matches the active cue (0.0–0.8)


def test_mine_tab_does_not_materialise_an_empty_store(tmp_path, monkeypatch):
    from saitenka.app import mined_store

    video = tmp_path / "Show - 03.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "absent.sqlite")

    assert sidebar._mine_rows(reader) == []
    assert reader._mined_store is None
    assert not (tmp_path / "absent.sqlite").exists()


def test_clicking_a_mine_row_seeks_to_its_cue_offline(tmp_path, monkeypatch):
    from saitenka.app.mined_store import MinedCardStore

    video = tmp_path / "Show - 03.mkv"
    reader, ipc = _reader(cue_count=1, props={"path": str(video)})
    store = MinedCardStore(tmp_path / "mined.sqlite")
    store.record(
        note_id=111,
        video_path=str(video),
        cue_start=0.0,
        cue_end=0.8,
        expression="本",
        reading="ほん",
    )
    reader._mined_store = store
    _capture_render(monkeypatch)
    reader.sidebar.open = True
    reader.sidebar.view = "mine"
    reader.sidebar.rect = (0, 0, 400, 500)
    reader.sidebar.hits = (SidebarHitBox("mine-open", 111, 0, 0, 200, 40),)

    sidebar.on_click(reader, 10, 10)

    assert ("set_property", "time-pos", 0.0) in ipc.commands  # seeks even with Anki down


def test_clicking_a_mine_row_opens_the_card_preview_when_anki_is_up(tmp_path, monkeypatch):
    from saitenka.app import miner_ui
    from saitenka.app.mined_store import MinedCardStore

    video = tmp_path / "Show - 03.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    store = MinedCardStore(tmp_path / "mined.sqlite")
    store.record(
        note_id=111,
        video_path=str(video),
        cue_start=0.0,
        cue_end=0.8,
        expression="本",
        reading="ほん",
    )
    reader._mined_store = store
    reader.anki = object()
    reader.mine_cfg = object()
    opened = []
    monkeypatch.setattr(
        miner_ui,
        "preview_existing",
        lambda _r, nid, card, status: opened.append((nid, card.expression, status)),
    )

    sidebar._open_mined(reader, 111)

    assert opened == [(111, "本", "exists")]


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
        ("view:mine", 0),
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


def test_ui_scale_increases_sidebar_rows_and_reduces_capacity():
    rows = [SidebarRow(value=i, timestamp="00:00", text=f"cue {i}") for i in range(20)]

    normal = render_sidebar(rows, width=620, height=600, view="track", total=20, first=0)
    enlarged = render_sidebar(
        rows, width=930, height=600, view="track", total=20, first=0, scale=1.5
    )

    assert enlarged.image.width > normal.image.width
    assert enlarged.row_capacity < normal.row_capacity


def test_row_capacity_follows_the_screen_and_the_chrome_scale() -> None:
    """A taller panel fits more rows; a larger chrome scale fits fewer of them in the same space.
    Both are needed, and checking it takes no session now."""
    from saitenka.app.sidebar import _capacity

    assert _capacity((1920, 1080), 1.0) > _capacity((1920, 720), 1.0)
    assert _capacity((1920, 1080), 2.0) < _capacity((1920, 1080), 1.0)
    assert _capacity((640, 200), 1.0) >= 1  # never zero, however small the screen


def test_cue_colours_are_recomputed_when_the_known_set_changes() -> None:
    """The cache key carries the scorer's identity, not just the text: the same line scores
    differently once the known-word set does, and a text-only key would serve the old colours."""
    from saitenka.app.sidebar import _cue_parts

    class Scorer:
        def __init__(self, colour):
            self.colour = colour

        def score_line(self, tokens):
            return [type("S", (), {"color": self.colour})() for _ in tokens]

    class Tok:
        def tokenize(self, line):
            return [type("T", (), {"surface": line})()]

    cache: dict = {}
    cue = Cue(0.0, 1.0, "猫")
    first = _cue_parts(cache, 0, cue, language="jp", scorer=Scorer((1, 1, 1, 1)), tokenizer=Tok())
    second = _cue_parts(cache, 0, cue, language="jp", scorer=Scorer((9, 9, 9, 9)), tokenizer=Tok())

    assert first != second


def test_a_bookmark_falls_back_to_the_other_language() -> None:
    """A capture may only have one side, and showing a blank row would read as a broken bookmark."""
    from saitenka.app.backlog import BacklogEntry
    from saitenka.app.sidebar import _entry_text

    only_jp = BacklogEntry(
        id=1,
        media_id=1,
        cue_start=0.0,
        cue_end=1.0,
        jp_text="猫を見る",
        en_text="",
        subtitle_track={},
        hovered_surface=None,
        hovered_lemma=None,
        status="open",
        created_at="",
        updated_at="",
    )

    assert _entry_text(only_jp, "en") == "猫を見る"
    assert _entry_text(only_jp, "jp") == "猫を見る"


def test_the_track_tab_does_not_open_a_backlog_for_a_session_with_no_video() -> None:
    """Opening the tab must not materialise an empty store — it creates a file on disk.

    `_cue_statuses` used to reach the host and guard `not video` itself. It takes a store now, so
    the laziness moved to the caller, and a caller that builds the store before checking would open
    a backlog for every video-less session. The guard is easy to lose and invisible when lost: the
    statuses come back empty either way, and only the on-disk side effect differs.
    """
    reader, _ipc = _reader(props={"path": ""})
    reader.sidebar.open = True

    sidebar.redraw(reader)

    assert reader._backlog_store is None
