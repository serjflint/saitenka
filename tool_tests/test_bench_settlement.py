"""A benchmark must observe the requested navigation, not the old warm panel."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _benchmark(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "examples/bench_responsiveness.py"
    spec = importlib.util.spec_from_file_location("bench_settlement", path)
    assert spec and spec.loader
    bench = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, bench)
    spec.loader.exec_module(bench)
    return bench


def test_benchmark_player_property_write_is_observable_through_both_ports(monkeypatch):
    bench = _benchmark(monkeypatch)
    ipc = bench.FakeIPC()

    ipc.command_async("set_property", "sub-visibility", False)

    assert ipc.query("sub-visibility") is False
    assert ipc.command("get_property", "sub-visibility") == {"error": "success", "data": False}


@pytest.mark.timeout(5)
def test_timeline_waits_for_base_panel_before_counting_nested_work(monkeypatch):
    bench = _benchmark(monkeypatch)
    ipc, gateway = bench._runtime_ipc()
    reader = bench.create_session_controller(
        ipc, services=bench.SessionServices(dictionaries=bench._SyntheticDS())
    )
    try:
        reader.pump()
        reader.graph.screen.osd = bench.OSD
        reader.live.start()
        bench._set_video_engagement(reader, True)
        reader.graph.playback.observe("sub-text", "猫を見る")
        reader.graph.cue.settle()
        bench._hover_word(reader, 0)

        census = bench._timeline_interact(reader)

        assert census["base_attempted"] == census["base_settled"] == 1
        assert census["skipped_no_panel"] == 0
        assert (
            census["nested_attempted"]
            == census["nested_exercised"]
            == census["nested_settled"]
            == 1
        )
    finally:
        reader.close()
        gateway.close()


@pytest.mark.parametrize("published", [False, True])
def test_navigation_settlement_requires_a_new_panel(monkeypatch, published):
    path = Path(__file__).resolve().parents[1] / "examples/bench_responsiveness.py"
    spec = importlib.util.spec_from_file_location("bench_settlement", path)
    assert spec and spec.loader
    bench = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, bench)
    spec.loader.exec_module(bench)
    old = object()
    view = SimpleNamespace(
        state=old, rect=(0, 0, 10, 10), crisp_pending=False, desired_scroll=0, scroll=0
    )
    states = iter([old, object() if published else old, old])

    def pump():
        view.state = next(states)

    reader = SimpleNamespace(
        pump=pump, graph=SimpleNamespace(tooltip=SimpleNamespace(publish_pending=lambda: None))
    )
    ticks = iter([0, 0.1, 0.2, 0.3])
    monkeypatch.setattr(
        bench, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _seconds: None)
    )

    assert bench._settle_interaction(reader, view, 0.25, previous_panel=old) is published
