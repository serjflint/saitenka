"""Whole-episode subtitle sidebar behavior at the SessionController seam."""

import dataclasses

import pytest
import util
from driver import Driver
from PIL import Image
from saitenka_card import MineConfig
from saitenka_subtitles import Cue, CueIndex
from saitenka_wordstate import Scorer
from saitenka_wordstate.known import KnownWords
from session_builder import TestSession, build_session, install_profile_dependencies

from saitenka.app import backlog
from saitenka.app.backlog import BacklogStore, Capture
from saitenka.app.bindings import CLICK_MSG
from saitenka.app.features.analysis.episode_analysis import analyze_cues
from saitenka.app.features.mining import mine_intents
from saitenka.app.features.mining.mining_controller import MiningSpec, MiningTarget
from saitenka.app.features.sidebar import sidebar
from saitenka.app.scoring import Coloring
from saitenka.app.session import sidebar_coordination
from saitenka.app.subtitles import (
    SidebarAction,
    SidebarHitBox,
    SidebarRender,
    SidebarRow,
    render_sidebar,
)
from saitenka.runtime import events
from saitenka.runtime.events import SubtitleLanguageChanged, SubtitleTracksDiscovered


class FakeIPC(util.FakeIPC):
    def __init__(self, props=None):
        super().__init__()
        self.props.update(props or {})


def _reader(cue_count=20, *, active=0, props=None):
    ipc = FakeIPC(props)
    reader = build_session(ipc)
    cues = [Cue(float(i), float(i) + 0.8, f"cue {i}") for i in range(cue_count)]
    reader.graph.track_commands.navigation.current.sub_index = CueIndex(cues)
    reader.graph.playback.install_seed({"sub-text": f"cue {active}"})
    return reader, ipc


def _enable_mining(reader: TestSession) -> None:
    config = MineConfig()
    identity = reader.graph.mining.desired_spec.identity
    reader.graph.mining.select_mining_spec(
        MiningSpec(identity, {"deck": config.deck, "model": config.model})
    )
    assert reader.graph.mining.publish_mining_target(MiningTarget(identity, object(), config))
    reader.graph.mining.close_capability()


def _view(reader, **overrides):
    """The value production draws from, with the row-level facts a test pins stated explicitly."""
    return dataclasses.replace(reader.graph.sidebar.view(), **overrides)


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

    (sidebar.hide if reader.graph.sidebar.state.open else sidebar.show)(reader.graph.sidebar.view())

    assert reader.graph.sidebar.state.open is True
    assert [row.value for row in calls[-1][0]] == list(range(8, 17))
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_active_row_uses_timing_to_disambiguate_repeated_text():
    reader, _ipc = _reader(props={"sub-start": 5.2, "time-pos": 5.3})
    reader.graph.track_commands.navigation.current.sub_index = CueIndex(
        [Cue(1.0, 2.0, "same line"), Cue(5.0, 6.0, "same line")]
    )
    reader.graph.playback.install_seed({"sub-text": "same line"})

    assert (
        sidebar._active_index(
            reader.graph.track_commands.navigation.current.sub_index,
            reader.graph.playback.cue.text,
            sub_start=reader.graph.playback.query("sub-start"),
            time_pos=reader.graph.playback.query("time-pos"),
            preferred=reader.graph.track_commands.navigation.current.nav_idx,
        )
        == 1
    )


def test_manual_scroll_holds_then_returns_to_active_cue(monkeypatch):
    """The hold ends on its own deadline, and that deadline re-runs the follow itself. Waiting for
    the next `update` would leave the sidebar off-target for as long as the cue happened to last."""
    reader, ipc = _reader(active=10, props={"mouse-pos": {"x": 1000, "y": 100}})
    calls = _capture_render(monkeypatch)
    (sidebar.hide if reader.graph.sidebar.state.open else sidebar.show)(reader.graph.sidebar.view())
    reader.graph.sidebar.panel.rect = (900, 50, 360, 600)

    assert reader.graph.sidebar.scroll(reader.graph.interaction.wheel_step(), -3) is True
    held_scroll = reader.graph.sidebar.state.scroll
    reader.graph.playback.install_seed({"sub-text": "cue 18"})
    sidebar.follow(reader.graph.sidebar.view())
    assert reader.graph.sidebar.state.scroll == held_scroll

    before_expiry = len(calls)
    assert ipc.fire_runtime_timer("lifecycle:sidebar-manual-hold")

    assert reader.graph.sidebar.state.scroll == 14
    assert len(calls) == before_expiry + 1


def test_clicking_cue_seeks_without_changing_pause_state(monkeypatch):
    reader, ipc = _reader(active=3)
    _capture_render(monkeypatch)
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (100, 100, 400, 500)
    reader.graph.sidebar.panel.hits = (SidebarHitBox("seek", 7, 0, 0, 200, 40),)

    assert reader.graph.sidebar.on_click(reader.graph.interaction.click_target(), 110, 110) is True
    assert ("set_property", "time-pos", 7.0) in ipc.commands
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


@pytest.mark.parametrize(
    ("kind", "command"),
    [
        ("bookmark", mine_intents.MineCommand.BOOKMARK_CUE),
        ("mine", mine_intents.MineCommand.WORD),
    ],
)
def test_active_cue_actions_use_registered_mining_commands(kind, command, monkeypatch):
    reader, _ipc = _reader(active=3)
    _capture_render(monkeypatch)
    invoked = []
    monkeypatch.setattr(reader.graph.stateless_commands, "run", invoked.append)
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (100, 100, 400, 500)
    reader.graph.sidebar.panel.hits = (SidebarHitBox(kind, 3, 0, 0, 40, 40),)

    reader.graph.sidebar.on_click(reader.graph.interaction.click_target(), 110, 110)

    assert invoked == [command]


@pytest.mark.parametrize(
    ("kind", "command"),
    [
        ("bookmark", mine_intents.MineCommand.BOOKMARK_CUE),
        ("mine", mine_intents.MineCommand.WORD),
    ],
)
def test_active_cue_action_still_fires_when_the_active_cue_drifted(kind, command, monkeypatch):
    """#252: B/+ render only on the active row, so re-gating the click on `hit.value == active` dropped
    it silently when playback advanced a cue between redraw and click. The click must fire regardless of
    the clicked row's value vs the now-active index — parity with the ungated Alt+b."""
    reader, _ipc = _reader(active=9)  # the live cue has moved on since the row was drawn
    _capture_render(monkeypatch)
    invoked = []
    monkeypatch.setattr(reader.graph.stateless_commands, "run", invoked.append)
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (100, 100, 400, 500)
    reader.graph.sidebar.panel.hits = (
        SidebarHitBox(kind, 3, 0, 0, 40, 40),
    )  # row 3, no longer active

    reader.graph.sidebar.on_click(reader.graph.interaction.click_target(), 110, 110)

    assert invoked == [command]  # not the old silent no-op


def test_sidebar_cue_action_is_not_routed_after_cue_retirement(monkeypatch):
    reader, ipc = _reader(active=3)
    _capture_render(monkeypatch)
    invoked = []
    monkeypatch.setattr(reader.graph.stateless_commands, "run", invoked.append)
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (100, 100, 400, 500)
    reader.graph.sidebar.panel.hits = (SidebarHitBox("bookmark", 3, 0, 0, 40, 40),)
    ipc.props["mouse-pos"] = {"hover": True, "x": 110, "y": 110}
    reader.graph.cue.set_subtitle("cue 3")
    reader.graph.cue.retire("cue-text")

    reader.command(CLICK_MSG)

    assert invoked == []


def test_sidebar_bookmark_and_keybind_route_to_the_same_flow(monkeypatch):
    """The sidebar B button and Alt+b resolve to the same registered command policy."""
    from saitenka.app.bindings import BOOKMARK_MSG

    reader, _ipc = _reader(
        active=3,
        props={"path": "/video.mkv", "sub-start": 3.0, "sub-end": 3.8},
    )
    _capture_render(monkeypatch)
    invoked = []
    monkeypatch.setattr(backlog, "capture_current", lambda _ports: invoked.append("toggle"))

    reader.command(BOOKMARK_MSG)  # the Alt+b path
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (100, 100, 400, 500)
    reader.graph.sidebar.panel.hits = (SidebarHitBox("bookmark", 3, 0, 0, 40, 40),)
    reader.graph.sidebar.on_click(
        reader.graph.interaction.click_target(), 110, 110
    )  # the sidebar-button path

    assert invoked == ["toggle", "toggle"]  # one flow, two entry points


def test_english_rows_are_plain_and_skip_japanese_analysis(monkeypatch):
    reader, _ipc = _reader(cue_count=1)
    reader.graph.track_commands.declare(SubtitleLanguageChanged("en"))
    install_profile_dependencies(reader, scorer=object())
    monkeypatch.setattr(
        reader.graph.profile.profile.tokenizer,
        "tokenize",
        lambda _text: (_ for _ in ()).throw(AssertionError),
    )

    rows, total = sidebar._track_rows(_view(reader, active=0), 0, 1)

    assert total == 1
    assert rows[0].parts == (("cue 0", sidebar.PLAIN),)


def test_rows_use_shared_episode_analysis_when_ready():
    reader, _ipc = _reader(cue_count=1)
    reader.graph.track_commands.navigation.current.sub_index = CueIndex(
        [Cue(0.0, 1.0, "私は本を読む。")]
    )
    reader.graph.playback.install_seed({"sub-text": "私は本を読む。"})
    install_profile_dependencies(
        reader, scorer=Coloring(Scorer(known=KnownWords.from_set(["私", "本"])))
    )
    analysis = analyze_cues(
        list(reader.graph.track_commands.navigation.current.sub_index.cues),
        reader.graph.profile.scorer,
        reader.graph.profile.profile.tokenizer,
    )

    rows, _total = sidebar._track_rows(_view(reader, active=0, analysis=analysis), 0, 1)

    assert rows[0].status == "N+1"


def test_track_change_clears_stale_analysis_before_sidebar_redraw(monkeypatch, make_session):
    ipc = FakeIPC()
    gateway = util.session_gateway(ipc)
    reader = make_session(ipc)
    try:
        reader.graph.track_commands.declare(
            SubtitleTracksDiscovered(1, reader.graph.track_commands.current().en_sid)
        )
        install_profile_dependencies(
            reader, scorer=Coloring(Scorer(known=KnownWords.from_set(["私", "本"])))
        )
        reader.graph.track_commands.navigation.current.sub_index = CueIndex(
            [Cue(0.0, 1.0, "私は本を読む。")]
        )
        reader.graph.playback.install_seed({"sub-text": "私は本を読む。"})
        reader.graph.analysis_commands.set_open(open=True)
        util.await_ready(
            lambda: reader.graph.analysis.settled,
            "analysis result was not published",
            pump=reader.pump,
        )
        assert reader.graph.analysis.result is not None
        reader.graph.sidebar.store.dispatch(
            events.SidebarShown(
                reader.graph.sidebar.view().active,
                reader.graph.sidebar.view().capacity,
            )
        )
        reader.graph.profile.begin_loading()
        calls = _capture_render(monkeypatch)
        monkeypatch.setattr(
            "saitenka.app.subnav.load_index",
            lambda _path: CueIndex([Cue(0.0, 1.0, "猫です。")]),
        )

        reader.graph.subtitle_navigation.load_index("new-track.srt")

        assert calls[-1][0][0].text == "猫です。"
        assert calls[-1][0][0].status is None
    finally:
        reader.close()
        gateway.close()


def test_sidebar_hover_suppresses_tooltip_without_pausing(monkeypatch):
    reader, ipc = _reader(props={"mouse-pos": {"x": 110, "y": 110}})
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (100, 100, 400, 500)
    monkeypatch.setattr(
        "saitenka.app.features.tooltip.tooltip.update_hover",
        lambda _reader: (_ for _ in ()).throw(AssertionError("tooltip reached")),
    )

    Driver(reader, instant=False).move(110, 110)

    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)


def test_backlog_candidate_hides_cue_text_until_explicit_relink(tmp_path, monkeypatch):
    original = tmp_path / "[Group] Show - 01 [1080p].mkv"
    renamed = tmp_path / "Show S01E01 remux.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(renamed)})
    store = BacklogStore(tmp_path / "backlog.sqlite")
    entry = store.toggle_capture(
        Capture(str(original), 0.0, 0.8, jp_text="秘密の字幕", en_text="secret subtitle")
    )
    reader.graph.history.replace_backlog(store)

    rows = sidebar._summary_rows(reader.graph.sidebar.view())

    candidate = next(row for row in rows if row.actions)
    assert candidate.actions == (SidebarAction("✓", "relink", entry.media_id),)
    assert "秘密の字幕" not in "".join(row.text for row in rows)
    assert store.entries_for_path(renamed) == []

    _capture_render(monkeypatch)
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (0, 0, 400, 500)
    reader.graph.sidebar.panel.hits = (SidebarHitBox("relink", entry.media_id, 0, 0, 100, 40),)
    reader.graph.sidebar.on_click(reader.graph.interaction.click_target(), 10, 10)

    assert store.entries_for_path(renamed) == [entry]
    assert store.media(entry.media_id).original_basename == original.name
    matched_rows = sidebar._summary_rows(reader.graph.sidebar.view())
    expanded = next(row for row in matched_rows if row.click_kind == "backlog-seek")
    assert (expanded.text, expanded.status) == ("秘密の字幕", "open")

    reader.graph.sidebar.panel.rect = (0, 0, 400, 500)
    reader.graph.sidebar.panel.hits = (SidebarHitBox("backlog-seek", entry.id, 0, 0, 100, 40),)
    reader.graph.sidebar.on_click(reader.graph.interaction.click_target(), 10, 10)
    assert ("set_property", "time-pos", 0.0) in reader.graph.ipc.commands


def test_mining_marks_matching_backlog_cue_without_creating_a_store(tmp_path, monkeypatch):
    video = tmp_path / "Show - 01.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    store = BacklogStore(tmp_path / "backlog.sqlite")
    entry = store.toggle_capture(Capture(str(video), 0.0, 0.8, jp_text="cue 0"))
    reader.graph.history.replace_backlog(store)
    _capture_render(monkeypatch)

    sidebar.mine_active(reader.graph.sidebar.view())

    assert store.entry(entry.id).status == "mined"


def test_mine_tab_lists_this_episodes_mined_cards(tmp_path, monkeypatch):
    from saitenka.app.features.mining import mined_store

    video = tmp_path / "Show - 03.mkv"
    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    store = reader.graph.mining.store
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
    rows = sidebar._mine_rows(reader.graph.sidebar.view())

    assert [(row.value, row.click_kind, row.status) for row in rows] == [
        (111, "mine-open", "mined"),
        (222, "mine-open", "mined"),
    ]
    assert "本" in rows[0].text and "ほん" in rows[0].text
    assert rows[0].active is True  # its cue span matches the active cue (0.0–0.8)


def test_mine_tab_does_not_materialise_an_empty_store(tmp_path, monkeypatch):
    from saitenka.app.features.mining import mined_store

    video = tmp_path / "Show - 03.mkv"
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "absent.sqlite")

    assert sidebar._mine_rows(reader.graph.sidebar.view()) == []
    assert reader.graph.mining.store_exists is False
    assert not (tmp_path / "absent.sqlite").exists()


def test_clicking_a_mine_row_seeks_to_its_cue_offline(tmp_path, monkeypatch):
    from saitenka.app.features.mining import mined_store

    video = tmp_path / "Show - 03.mkv"
    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")
    reader, ipc = _reader(cue_count=1, props={"path": str(video)})
    store = reader.graph.mining.store
    store.record(
        note_id=111,
        video_path=str(video),
        cue_start=0.0,
        cue_end=0.8,
        expression="本",
        reading="ほん",
    )
    _capture_render(monkeypatch)
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.store.dispatch(events.SidebarViewSelected("mine"))
    reader.graph.sidebar.panel.rect = (0, 0, 400, 500)
    reader.graph.sidebar.panel.hits = (SidebarHitBox("mine-open", 111, 0, 0, 200, 40),)

    reader.graph.sidebar.on_click(reader.graph.interaction.click_target(), 10, 10)

    assert ("set_property", "time-pos", 0.0) in ipc.commands  # seeks even with Anki down


def test_clicking_a_mine_row_opens_the_card_preview_when_anki_is_up(tmp_path, monkeypatch):
    from saitenka.app.features.mining import mined_store
    from saitenka.app.features.preview import miner_ui

    video = tmp_path / "Show - 03.mkv"
    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")
    reader, _ipc = _reader(cue_count=1, props={"path": str(video)})
    store = reader.graph.mining.store
    store.record(
        note_id=111,
        video_path=str(video),
        cue_start=0.0,
        cue_end=0.8,
        expression="本",
        reading="ほん",
    )
    _enable_mining(reader)
    opened = []
    monkeypatch.setattr(
        miner_ui,
        "preview_existing",
        lambda _ports, _src, nid, card, status: opened.append((nid, card.expression, status)),
    )

    sidebar_coordination.open_mined(
        reader.graph.sidebar.view(),
        reader.graph.interaction.sidebar_actions(),
        reader.graph.preview_commands.ports(),
        reader.graph.preview_commands.card_source(),
        111,
    )

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
    from saitenka.app.features.sidebar.sidebar import _capacity

    assert _capacity((1920, 1080), 1.0) > _capacity((1920, 720), 1.0)
    assert _capacity((1920, 1080), 2.0) < _capacity((1920, 1080), 1.0)
    assert _capacity((640, 200), 1.0) >= 1  # never zero, however small the screen


def test_cue_colours_are_recomputed_when_the_known_set_changes() -> None:
    """The cache key carries the scorer's identity, not just the text: the same line scores
    differently once the known-word set does, and a text-only key would serve the old colours."""
    from saitenka.app.features.sidebar.sidebar import _cue_parts

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
    from saitenka.app.features.sidebar.sidebar import _entry_text

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
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )

    sidebar.draw(reader.graph.sidebar.view())

    assert reader.graph.history.backlog is None
