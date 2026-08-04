"""Scroll-ahead wiring: a scroll notch records a single newest-wins request that a prefetch worker
drains to warm the next blocks OFF the main thread. The block-level render_ahead correctness lives in
``test_windowed_prefetch.py``; here we pin the request slot, generation-cancellation, and that the
worker actually warms a real panel."""

from __future__ import annotations

from overlay.app import prefetch
from overlay.app.config import ReaderOptions
from overlay.app.controller import Reader
from overlay.app.popups import Panel
from overlay.panel import Definition, Entry, panel_rows
from overlay.render.banded import WindowedPanel

WIDTH = 384


class _FakeIPC:
    def __init__(self):
        self.props = {}

    def command(self, *_args):
        return {"data": None}

    def pump(self):
        pass

    def drain_events(self):
        return []


class _RecordingPanel:
    """Stands in for a tooltip Panel — records the render_ahead call instead of rasterising."""

    def __init__(self):
        self.calls = []

    def render_ahead(self, scroll, view_h, *, direction, should_cancel, scale=1.0):
        self.calls.append((scroll, view_h, direction, should_cancel(), scale))
        return len(self.calls)


def _reader() -> Reader:
    r = Reader(_FakeIPC(), options=ReaderOptions(prefetch=True))
    r._tip_view_h = 300
    r._tip_scroll = 120
    return r


def _tall_panel() -> Panel:
    entry = Entry(
        headword="本命", defs=[Definition(f"辞書{i}", ["長い定義。" * 40]) for i in range(12)]
    )
    # render_block_fn=None keeps render_ahead inline (no process pool) for a hermetic test.
    return Panel(WindowedPanel(panel_rows(entry, WIDTH), WIDTH, render_block_fn=None), "ほんめい")


def test_scroll_records_the_newest_request_only():
    r = _reader()
    r._tip_state = _RecordingPanel()  # type: ignore[assignment]  # only the slot fields are read
    prefetch.request_render_ahead(r, 1)
    r._tip_scroll = 999
    prefetch.request_render_ahead(r, -1)
    req = r._render_ahead_req
    assert req is not None
    assert (req.scroll, req.view_h, req.direction) == (999, 300, -1)  # newest scroll won


def test_no_request_without_a_tooltip_or_when_prefetch_off():
    r = _reader()
    r._tip_state = None
    prefetch.request_render_ahead(r, 1)
    assert r._render_ahead_req is None

    r._tip_state = _RecordingPanel()  # type: ignore[assignment]
    r.prefetch = False
    prefetch.request_render_ahead(r, 1)
    assert r._render_ahead_req is None


def test_worker_drains_the_slot_and_warms_off_thread():
    r = _reader()
    panel = _RecordingPanel()
    r._tip_state = panel  # type: ignore[assignment]
    prefetch.request_render_ahead(r, 1)

    handled = prefetch._try_render_ahead(r)

    assert handled is True
    assert r._render_ahead_req is None  # slot drained
    # warmed at the scroll pos, not cancelled, at the (bucketed) display scale — native bands (one panel)
    assert panel.calls == [(120, 300, 1, False, r._raster_scale)]


def test_empty_slot_is_not_handled():
    assert prefetch._try_render_ahead(_reader()) is False


def test_stale_request_from_a_word_switch_is_dropped():
    r = _reader()
    panel = _RecordingPanel()
    r._tip_state = panel  # type: ignore[assignment]
    prefetch.request_render_ahead(r, 1)
    r._prefetch_gen += 1  # a line change / seek invalidates the in-flight request

    handled = prefetch._try_render_ahead(r)

    assert handled is True  # consumed (so the worker loops), but...
    assert panel.calls == []  # ...never rendered the stale panel


def test_worker_actually_warms_a_real_panel():
    r = _reader()
    r._tip_scroll = 0
    r._tip_state = _tall_panel()
    prefetch.request_render_ahead(r, 1)

    prefetch._try_render_ahead(r)

    assert r._tip_state.windowed.cached_blocks > 0  # blocks warmed without any viewport() call
