"""Click-driven surfaces are spanned (PR C): a sidebar click, a bookmark write, and a mined-store
write all touch the main thread (SQLite / a full redraw) but were BLIND — no span, so a report couldn't
tell whether a click stutters (the class of bug #293 fixed for the hover path, here for clicks). These
assert the span fires with its low-cardinality attribute, via the sanctioned traced-recorder seam
(monkeypatch ``otel_metrics.traced``), the same pattern as tests/test_osd_telemetry.py.
"""

from __future__ import annotations

import util
from session_builder import build_session
from util import record_spans

from saitenka.app import backlog
from saitenka.app.features.sidebar import sidebar
from saitenka.app.session.factory import SessionServices
from saitenka.app.subtitles import SidebarHitBox
from saitenka.runtime import events


class _FakeIPC(util.FakeIPC):
    def __init__(self, props):
        super().__init__()
        self.props.update(props)


def _named(spans: list[dict], name: str) -> list[dict]:
    return [s["attrs"] for s in spans if s["name"] == name]


def test_sidebar_click_is_spanned_with_its_kind(monkeypatch):
    # A sidebar click emits a sidebar_click span tagged with the action kind — the click-latency signal.
    spans = record_spans(monkeypatch)
    monkeypatch.setattr(sidebar, "draw", lambda *_a: None)
    reader = build_session(_FakeIPC({}))
    reader.sidebar_controller.store.dispatch(
        events.SidebarShown(
            reader.sidebar_controller.view().active, reader.sidebar_controller.view().capacity
        )
    )
    reader.sidebar_controller.panel.rect = (0, 0, 100, 100)
    reader.sidebar_controller.panel.hits = (
        SidebarHitBox(kind="bookmark", value=0, x=0, y=0, w=100, h=20),
    )
    # Inert actions instead of a stubbed internal: the port is the seam, so isolating the span
    # from what the click does no longer means knowing the private name that does it.
    inert = sidebar.SidebarActions(
        seek=lambda *_a: None, bookmark=lambda: None, mine=lambda: None, open_mined=lambda _n: None
    )

    assert sidebar.click(reader.sidebar_controller.view(), inert, 10, 10) is True
    (attrs,) = _named(spans, "sidebar_click")
    assert attrs["kind"] == "bookmark"


def test_sidebar_click_outside_a_hit_emits_no_span(monkeypatch):
    # A click inside the sidebar but on no hitbox is handled (returns True) WITHOUT a write/redraw span.
    spans = record_spans(monkeypatch)
    reader = build_session(_FakeIPC({}))
    reader.sidebar_controller.store.dispatch(
        events.SidebarShown(
            reader.sidebar_controller.view().active, reader.sidebar_controller.view().capacity
        )
    )
    reader.sidebar_controller.panel.rect = (0, 0, 100, 100)
    reader.sidebar_controller.panel.hits = (
        SidebarHitBox(kind="bookmark", value=0, x=0, y=0, w=10, h=10),
    )

    assert (
        reader.sidebar_controller.on_click(reader.interaction.click_target(), 50, 50) is True
    )  # inside the panel, off every hitbox
    assert _named(spans, "sidebar_click") == []


def test_bookmark_toggle_write_is_spanned(monkeypatch, tmp_path):
    # capture_current's durable backlog write (main-thread SQLite) is spanned backlog_write[op=toggle].
    spans = record_spans(monkeypatch)
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"v")
    reader = build_session(
        _FakeIPC({"path": str(video), "sub-start": 1.0, "sub-end": 3.0, "track-list": []})
    )
    reader.playback_observation.install_seed({"sub-text": "猫です"})
    reader.history.replace_backlog(backlog.BacklogStore(tmp_path / "backlog.sqlite"))

    backlog.capture_current(reader.capture_ports)
    (attrs,) = _named(spans, "backlog_write")
    assert attrs["op"] == "toggle"


def test_mined_store_write_is_spanned(monkeypatch, tmp_path):
    # The #253 mined-card link write (main-thread SQLite) is spanned mined_store_write.
    from types import SimpleNamespace

    from saitenka.app.anki import MineConfig
    from saitenka.app.features.mining import mined_store, miner

    spans = record_spans(monkeypatch)
    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")
    reader = build_session(
        _FakeIPC({"sub-start": 1.0, "sub-end": 3.0}),
        services=SessionServices(
            anki=SimpleNamespace(),
            mining=MineConfig(deck="Mining"),
        ),
    )
    card = SimpleNamespace(expression="猫", reading="ねこ")

    ports = reader.mining_controller._operation()  # transaction write under test
    assert ports is not None
    miner._persist_mined(ports, note_id=42, card=card, video="/x/Show - 01.mkv")
    assert len(_named(spans, "mined_store_write")) == 1
