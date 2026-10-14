"""Click-driven surfaces are spanned (PR C): a sidebar click, a bookmark write, and a mined-store
write all touch the main thread (SQLite / a full redraw) but were BLIND — no span, so a report couldn't
tell whether a click stutters (the class of bug #293 fixed for the hover path, here for clicks). These
assert the span fires with its low-cardinality attribute, via the sanctioned traced-recorder seam
(monkeypatch ``otel_metrics.traced``), the same pattern as tests/test_osd_telemetry.py.
"""

from __future__ import annotations

import util
from util import record_spans

from saitenka.app import backlog
from saitenka.app import bindings as app_bindings
from saitenka.app.features.sidebar import sidebar
from saitenka.app.subtitles import SidebarHitBox
from saitenka.runtime import events


class _FakeIPC(util.FakeIPC):
    def __init__(self, props):
        super().__init__()
        self.props.update(props)


def _named(spans: list[dict], name: str) -> list[dict]:
    return [s["attrs"] for s in spans if s["name"] == name]


def test_sidebar_click_is_spanned_with_its_kind(monkeypatch, make_session):
    # A sidebar click emits a sidebar_click span tagged with the action kind — the click-latency signal.
    spans = record_spans(monkeypatch)
    monkeypatch.setattr(sidebar, "draw", lambda *_a: None)
    reader = make_session(_FakeIPC({}))
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (0, 0, 100, 100)
    reader.graph.sidebar.panel.hits = (
        SidebarHitBox(kind="bookmark", value=0, x=0, y=0, w=100, h=20),
    )
    # Inert actions instead of a stubbed internal: the port is the seam, so isolating the span
    # from what the click does no longer means knowing the private name that does it.
    inert = sidebar.SidebarActions(
        seek=lambda *_a: None, bookmark=lambda: None, mine=lambda: None, open_mined=lambda _n: None
    )

    assert sidebar.click(reader.graph.sidebar.view(), inert, 10, 10) is True
    (attrs,) = _named(spans, "sidebar_click")
    assert attrs["kind"] == "bookmark"


def test_sidebar_click_outside_a_hit_emits_no_span(monkeypatch, make_session):
    # A click inside the sidebar but on no hitbox is handled (returns True) WITHOUT a write/redraw span.
    spans = record_spans(monkeypatch)
    reader = make_session(_FakeIPC({}))
    reader.graph.sidebar.store.dispatch(
        events.SidebarShown(
            reader.graph.sidebar.view().active,
            reader.graph.sidebar.view().capacity,
        )
    )
    reader.graph.sidebar.panel.rect = (0, 0, 100, 100)
    reader.graph.sidebar.panel.hits = (
        SidebarHitBox(kind="bookmark", value=0, x=0, y=0, w=10, h=10),
    )

    assert (
        reader.graph.sidebar.on_click(reader.graph.interaction.click_target(), 50, 50) is True
    )  # inside the panel, off every hitbox
    assert _named(spans, "sidebar_click") == []


def test_bookmark_toggle_write_is_spanned(monkeypatch, tmp_path, make_session):
    # capture_current's durable backlog write (main-thread SQLite) is spanned backlog_write[op=toggle].
    spans = record_spans(monkeypatch)
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"v")
    reader = make_session(
        _FakeIPC({"path": str(video), "sub-start": 1.0, "sub-end": 3.0, "track-list": []})
    )
    reader.graph.playback.install_seed({"sub-text": "猫です"})
    reader.graph.history.replace_backlog(backlog.BacklogStore(tmp_path / "backlog.sqlite"))

    reader.command(app_bindings.BOOKMARK_MSG)
    (attrs,) = _named(spans, "backlog_write")
    assert attrs["op"] == "toggle"
