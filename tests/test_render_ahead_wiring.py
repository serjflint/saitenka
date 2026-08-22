"""Scroll-ahead broker wiring and newest-wins publication."""

from __future__ import annotations

import contextlib
import threading

import pytest
import util
from util import ManualRenderAheadSubmitter

from saitenka import otel_metrics
from saitenka.app import prefetch, tooltip_panel, tooltip_raster
from saitenka.app.config import ReaderOptions
from saitenka.app.controller import Reader
from saitenka.app.popups import Panel
from saitenka.panel import Definition, Entry, panel_rows
from saitenka.render.banded import WindowedPanel
from saitenka.runtime import EffectOutcome

WIDTH = 384


class _FakeIPC(util.FakeIPC):
    pass


class _RecordingPanel:
    """Stands in for a tooltip Panel — records the render_ahead call instead of rasterising."""

    def __init__(self):
        self.calls = []

    def viewport(self, scroll, view_h, *, scale=1.0):
        self.calls.append(("viewport", scroll, view_h, scale))

    def warm_viewport(self, scroll, view_h):
        self.calls.append(("warm_viewport", scroll, view_h))

    def render_ahead(self, scroll, view_h, *, direction, should_cancel, scale=1.0):
        self.calls.append((scroll, view_h, direction, should_cancel(), scale))
        return len(self.calls)


def _reader() -> Reader:
    r = Reader(_FakeIPC(), options=ReaderOptions(prefetch=True))
    r._render_ahead_submit = ManualRenderAheadSubmitter()
    r.tip.view.view_h = 300
    r.tip.view.scroll = 120
    r.tip.view.desired_scroll = 120
    return r


def _tall_panel() -> Panel:
    entry = Entry(
        headword="本命", defs=[Definition(f"辞書{i}", ["長い定義。" * 40]) for i in range(12)]
    )
    # render_block_fn=None keeps render_ahead inline (no process pool) for a hermetic test.
    return Panel(WindowedPanel(panel_rows(entry, WIDTH), WIDTH, render_block_fn=None), "ほんめい")


def test_scroll_keeps_one_newest_pending_request():
    r = _reader()
    r.tip.view.state = _RecordingPanel()  # type: ignore[assignment]  # only the slot fields are read
    r._request_render_ahead(r.tip.view, 1)
    r.tip.view.scroll = 999
    r.tip.view.desired_scroll = 999
    r._request_render_ahead(r.tip.view, -1)
    pending = r._render_ahead.pending
    assert pending is not None
    req = pending[1]
    assert (req.scroll, req.view_h, req.direction) == (999, 300, -1)  # newest scroll won


def test_newest_pending_request_runs_once_after_inflight_completion():
    r = _reader()
    panel = _RecordingPanel()
    r.tip.view.state = panel  # type: ignore[assignment]
    r._request_render_ahead(r.tip.view, 1)
    r.tip.view.desired_scroll = 999
    r._request_render_ahead(r.tip.view, -1)

    r._render_ahead_submit.finish()
    assert len(r._render_ahead_submit.calls) == 1
    r.tip.view.scroll = 999
    r._render_ahead_submit.finish()

    assert [call[0] for call in panel.calls if isinstance(call[0], int)] == [999]
    assert r._render_ahead_submit.calls == []


def test_running_stale_request_observes_supersession_before_newest_runs():
    entered = threading.Event()
    release = threading.Event()
    cancelled = []

    class BlockingPanel(_RecordingPanel):
        def render_ahead(
            self, _scroll, _view_h, *, direction: int, should_cancel, scale: float = 1.0
        ):
            _ = direction, scale
            entered.set()
            release.wait(1)
            cancelled.append(should_cancel())

    r = _reader()
    old = BlockingPanel()
    new = _RecordingPanel()
    r.tip.view.state = old  # type: ignore[assignment]
    r._request_render_ahead(r.tip.view, 1)
    worker = threading.Thread(target=r._render_ahead_submit.finish)
    worker.start()
    assert entered.wait(1)

    r.tip.view.state = new  # type: ignore[assignment]
    r._request_render_ahead(r.tip.view, -1)
    release.set()
    worker.join(1)
    r._render_ahead_submit.finish()

    assert cancelled == [True]
    assert [call[0] for call in new.calls if isinstance(call[0], int)] == [120]


def test_render_ahead_survives_disabled_speculative_prefetch():
    r = _reader()
    r.tip.view.state = None
    assert not r._request_render_ahead(r.tip.view, 1)

    r.tip.view.state = _RecordingPanel()  # type: ignore[assignment]
    r.prefetch = False
    assert r._request_render_ahead(r.tip.view, 1)


def test_broker_completion_warms_the_requested_viewport():
    r = _reader()
    panel = _RecordingPanel()
    r.tip.view.state = panel  # type: ignore[assignment]
    r._request_render_ahead(r.tip.view, 1)
    r._render_ahead_submit.finish()
    # the landing viewport first (what gates publication), then the lookahead past it — the latter at
    # the (bucketed) display scale, not cancelled: native bands, one panel
    assert panel.calls == [
        ("warm_viewport", 120, 300),
        (120, 300, 1, False, r.tip_scale.raster),
    ]


def test_stale_completion_from_a_word_switch_is_not_published():
    r = _reader()
    panel = _RecordingPanel()
    r.tip.view.state = panel  # type: ignore[assignment]
    r._request_render_ahead(r.tip.view, 1)
    r.prefetch_state.gen += 1
    r._cancel_render_ahead()
    before = r.tip.view.scroll
    r._render_ahead_submit.finish()
    assert r.tip.view.scroll == before
    assert panel.calls == []


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
    r.tip.view.scroll = 0
    r.tip.view.state = _tall_panel()
    r._request_render_ahead(r.tip.view, 1)
    r._render_ahead_submit.finish()

    assert r.tip.view.state.windowed.cached_blocks > 0  # blocks warmed without any viewport() call


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
    r.tip.view.state = panel  # type: ignore[assignment]
    r.tip.view.job_id = r.tip.jobs.begin("scroll")
    r._request_render_ahead(r.tip.view, 1)
    r._render_ahead_submit.finish(outcome=EffectOutcome.FAILED, run=False)

    assert r.tip.view.desired_scroll == r.tip.view.scroll
    assert spans[-1][0] == "scroll_request"
    assert spans[-1][1]["outcome"] == "failed"


def test_old_failure_cannot_roll_back_a_new_scroll_to_the_same_coordinate() -> None:
    r = _reader()
    panel = _RecordingPanel()
    r.tip.view.state = panel  # type: ignore[assignment]
    r.tip.view.desired_scroll = 100
    old_job = r.tip.jobs.begin("scroll")
    r.tip.view.job_id = old_job
    r._request_render_ahead(r.tip.view, -1)

    r.tip.view.desired_scroll = 200
    r.tip.view.job_id = r.tip.jobs.begin("scroll")
    r.tip.view.desired_scroll = 100
    current_job = r.tip.jobs.begin("scroll")
    r.tip.view.job_id = current_job
    r._render_ahead_submit.finish(outcome=EffectOutcome.FAILED, run=False)

    assert r.tip.view.desired_scroll == 100
    assert r.tip.view.job_id == current_job


def test_close_rejects_new_work_and_quarantines_late_completion() -> None:
    r = _reader()
    panel = _RecordingPanel()
    r.tip.view.state = panel  # type: ignore[assignment]
    r._request_render_ahead(r.tip.view, 1)
    before = r.tip.view.scroll

    tooltip_raster.close(r._render_ahead)
    r._render_ahead_submit.finish()

    assert r.tip.view.scroll == before
    assert not r._request_render_ahead(r.tip.view, -1)


def test_a_successful_terminal_sweeps_every_view_for_a_crisp_upgrade(monkeypatch):
    """The interaction tick used to sweep both popups for a soft→crisp upgrade; the render-ahead
    terminal does it now.

    Warming is keyed by panel and scale, so the job raised for one view can leave the other's
    viewport warm too. Sweeping only the view the job named would leave that one soft until some
    later scroll happened to ask again — which is exactly what the tick was covering.
    """
    from saitenka.app import tooltip_panel

    r = _reader()
    r.tip.view.state = _RecordingPanel()  # type: ignore[assignment]
    swept: list = []
    monkeypatch.setattr(
        tooltip_panel, "apply_pending_crisp", lambda _r, view: swept.append(id(view))
    )

    r._request_render_ahead(r.tip.view, 1)
    r._render_ahead_submit.finish()

    assert swept == [id(r.tip.view), id(r.tip.nest)]


def test_a_failed_terminal_sweeps_nothing(monkeypatch):
    """Nothing warmed, so there is nothing to upgrade — and the failed job still has to report its
    own outcome rather than being swallowed by a sweep."""
    from saitenka.app import tooltip_panel

    r = _reader()
    r.tip.view.state = _RecordingPanel()  # type: ignore[assignment]
    swept: list = []
    monkeypatch.setattr(
        tooltip_panel, "apply_pending_crisp", lambda _r, view: swept.append(id(view))
    )

    r._request_render_ahead(r.tip.view, 1)
    r._render_ahead_submit.finish(outcome=EffectOutcome.FAILED, run=False)

    assert swept == []


def test_a_wheel_burst_reaches_the_viewport_it_scrolled_to():
    """Three notches with no paint between them: the wheel gets where it asked to go, and the pixels
    follow. Live this stalled — the worker warmed only the bands PAST each landing, so the gate that
    lets a scroll be published never opened for the one it landed on."""
    r = _reader()
    r.tip.view.scroll = 0
    r.tip.view.desired_scroll = 0
    r.tip.view.state = _tall_panel()

    for _ in range(3):
        tooltip_panel.scroll_view(r.tip_ports, r.tip.view, 150)
    while r._render_ahead_submit.calls:
        r._render_ahead_submit.finish()

    assert (r.tip.view.desired_scroll, r.tip.view.scroll) == (450, 450)


@pytest.mark.parametrize("notch", [90, 150, 400])
def test_scrolling_down_and_back_up_returns_to_where_it_started(notch):
    """Round-trip: N notches down then N back up lands on the original viewport, whatever the notch.

    Direction-agnostic and position-agnostic, so it holds wherever the warm/cold boundary happens to
    fall — the thing a fixed-offset assertion cannot say. `notch=400` overshoots the panel and comes
    back off the clamp, which is where an off-by-a-step in the deferred publish would show.
    """
    r = _reader()
    r.tip.view.scroll = 0
    r.tip.view.desired_scroll = 0
    r.tip.view.state = _tall_panel()

    def burst(delta):
        for _ in range(4):
            tooltip_panel.scroll_view(r.tip_ports, r.tip.view, delta)
            while r._render_ahead_submit.calls:
                r._render_ahead_submit.finish()
            r._settle_interaction()

    burst(notch)
    assert r.tip.view.scroll > 0  # a negative control that never moved would round-trip too
    burst(-notch)

    assert r.tip.view.scroll == 0


def test_a_notch_landing_mid_raster_still_leaves_the_destination_warm():
    """The native raster has no internal cancel point and costs ~13x the destination warm, so a
    burst supersedes every job during it. Warming the destination first is what keeps publication
    reachable — behind the raster, a burst starves the bands it is waiting on and no reordering of
    the *gate* can help."""
    superseded = threading.Event()
    reached: list[tuple] = []

    class SupersedingPanel(_RecordingPanel):
        def viewport(self, scroll, view_h, *, scale=1.0):
            reached.append(("native", scroll, view_h, scale))
            superseded.set()  # a newer notch arrives mid-raster

        def warm_viewport(self, scroll, view_h):
            reached.append(("destination", scroll, view_h))

        def render_ahead(self, scroll, view_h, **_kwargs):
            reached.append(("lookahead", scroll, view_h))

    request = tooltip_raster.RenderAheadRequest(SupersedingPanel(), 900, 300, 1, 1.75, superseded)
    tooltip_raster.run_render_ahead(request, threading.Event())

    stages = [call[0] for call in reached]
    assert stages[0] == "destination"
    assert "native" in stages  # the crisp warm still gets its turn when nothing supersedes it
    assert reached[0][1:] == (900, 300)  # ...at the scroll the job was raised for


def test_the_turn_publishes_a_scroll_no_completion_claimed():
    """A burst supersedes its own jobs, so a completion carrying the newest scroll's identity is not
    something the popup may wait for: the wheel can stop on a notch whose job was already cancelled.
    Warmth belongs to the panel, not to a job, so the turn asks."""
    r = _reader()
    r.tip.view.scroll = 0
    r.tip.view.state = _tall_panel()
    r.tip.view.state.warm_viewport(300, r.tip.view.view_h)
    r.tip.view.desired_scroll = 300  # warm and wanted, but no job will ever report it

    r._settle_interaction()

    assert r.tip.view.scroll == 300
