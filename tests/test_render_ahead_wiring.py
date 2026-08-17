"""Scroll-ahead wiring: a scroll notch records a single newest-wins request that a prefetch worker
drains to warm the next blocks OFF the main thread. The block-level render_ahead correctness lives in
``test_windowed_prefetch.py``; here we pin the request slot, generation-cancellation, and that the
worker actually warms a real panel."""

from __future__ import annotations

import contextlib

from saitenka import otel_metrics
from saitenka.app import prefetch, tooltip
from saitenka.app.config import ReaderOptions
from saitenka.app.controller import Reader
from saitenka.app.popups import Panel
from saitenka.panel import Definition, Entry, panel_rows
from saitenka.render.banded import WindowedPanel

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
    r._tip_view.desired_scroll = 120
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
    prefetch.request_render_ahead(r, r._tip_view, 1)
    r._tip_scroll = 999
    r._tip_view.desired_scroll = 999
    prefetch.request_render_ahead(r, r._tip_view, -1)
    req = r._render_ahead_req
    assert req is not None
    assert (req.scroll, req.view_h, req.direction) == (999, 300, -1)  # newest scroll won


def test_engaged_request_survives_disabled_speculative_prefetch():
    r = _reader()
    r._tip_state = None
    prefetch.request_render_ahead(r, r._tip_view, 1)
    assert r._render_ahead_req is None

    r._tip_state = _RecordingPanel()  # type: ignore[assignment]
    r.prefetch = False
    prefetch.request_render_ahead(r, r._tip_view, 1)
    assert r._render_ahead_req is not None


def test_worker_drains_the_slot_and_warms_off_thread():
    r = _reader()
    panel = _RecordingPanel()
    r._tip_state = panel  # type: ignore[assignment]
    prefetch.request_render_ahead(r, r._tip_view, 1)

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
    prefetch.request_render_ahead(r, r._tip_view, 1)
    r._prefetch_gen += 1  # a line change / seek invalidates the in-flight request

    handled = prefetch._try_render_ahead(r)

    assert handled is True  # consumed (so the worker loops), but...
    assert panel.calls == []  # ...never rendered the stale panel


def test_prefetch_state_cancel_bumps_the_generation():
    """cancel() is the one place a line change / seek invalidates in-flight work: it bumps the
    generation (so the worker drops stale items) and hands back the fresh value for new enqueues."""
    st = prefetch.PrefetchState(head_queue_max=8)
    assert st.gen == 0
    first = st.cancel()
    assert first == 1 and st.gen == 1
    assert st.cancel() == 2  # each call advances → any item stamped with an older gen is stale


def test_worker_actually_warms_a_real_panel():
    r = _reader()
    r._tip_scroll = 0
    r._tip_state = _tall_panel()
    prefetch.request_render_ahead(r, r._tip_view, 1)

    prefetch._try_render_ahead(r)

    assert r._tip_state.windowed.cached_blocks > 0  # blocks warmed without any viewport() call


def test_render_ahead_failure_retires_the_scroll_intent(monkeypatch):
    class BrokenPanel(_RecordingPanel):
        def render_ahead(self, *_args, **_kwargs):
            raise RuntimeError("broken band")

    spans = []

    @contextlib.contextmanager
    def traced(name, **attrs):
        spans.append((name, attrs))
        yield None

    monkeypatch.setattr(otel_metrics, "traced", traced)
    r = _reader()
    panel = BrokenPanel()
    r._tip_state = panel  # type: ignore[assignment]
    r._tip_view.job_id = r._interaction_jobs.begin("scroll")
    prefetch.request_render_ahead(r, r._tip_view, 1)

    assert prefetch._try_render_ahead(r)
    tooltip.apply_render_ahead_failures(r)

    assert r._tip_view.desired_scroll == r._tip_view.scroll
    assert spans[-1][0] == "scroll_request"
    assert spans[-1][1]["outcome"] == "failed"


def test_old_failure_cannot_roll_back_a_new_scroll_to_the_same_coordinate() -> None:
    r = _reader()
    panel = _RecordingPanel()
    r._tip_state = panel  # type: ignore[assignment]
    r._tip_view.desired_scroll = 100
    old_job = r._interaction_jobs.begin("scroll")
    r._tip_view.job_id = old_job
    prefetch.request_render_ahead(r, r._tip_view, -1)

    r._tip_view.desired_scroll = 200
    r._tip_view.job_id = r._interaction_jobs.begin("scroll")
    r._tip_view.desired_scroll = 100
    current_job = r._interaction_jobs.begin("scroll")
    r._tip_view.job_id = current_job
    r._render_ahead_failures.put((r._prefetch_gen, panel, 100, old_job))

    tooltip.apply_render_ahead_failures(r)

    assert r._tip_view.desired_scroll == 100
    assert r._tip_view.job_id == current_job
