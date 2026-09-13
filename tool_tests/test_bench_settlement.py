"""A benchmark must observe the requested navigation, not the old warm panel."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


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
