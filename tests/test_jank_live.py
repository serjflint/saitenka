"""The live-jank harness's pure reduction seam (#32): cumulative mpv frame-drop counters → per-step
dropped/delayed frames. No mpv, no display — the humble-object part the real harness delegates to."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

JANK_PATH = Path(__file__).resolve().parent.parent / "examples" / "jank_live.py"


def _jank_module():
    spec = importlib.util.spec_from_file_location("jank_live", JANK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _s(step, drop, delay, interaction_ms=0.0):
    return {"step": step, "drop": drop, "delay": delay, "interaction_ms": interaction_ms}


def test_reduce_computes_per_step_deltas_from_cumulative_counters():
    mod = _jank_module()
    out = mod.reduce_jank_samples(
        [_s("baseline", 0, 0), _s("hover", 2, 1, 4.0), _s("scroll", 7, 5, 9.0)]
    )
    assert out["steps"] == [
        {"step": "hover", "dropped": 2, "delayed": 1, "interaction_ms": 4.0},
        {"step": "scroll", "dropped": 5, "delayed": 4, "interaction_ms": 9.0},
    ]
    assert out["total_dropped"] == 7 and out["total_delayed"] == 5
    assert out["max_step_dropped"] == 5
    assert out["total_interaction_ms"] == 13.0 and out["max_interaction_ms"] == 9.0


def test_reduce_clamps_a_counter_reset_to_zero_never_negative():
    # A counter that goes backwards (mpv reset, or a build lacking the property recorded as 0) must not
    # report negative jank.
    mod = _jank_module()
    out = mod.reduce_jank_samples([_s("hover", 10, 10), _s("scroll", 3, 3)])
    assert out["steps"] == [{"step": "scroll", "dropped": 0, "delayed": 0, "interaction_ms": 0.0}]
    assert out["total_dropped"] == 0


def test_reduce_handles_too_few_samples():
    mod = _jank_module()
    out = mod.reduce_jank_samples([_s("baseline", 0, 0)])  # nothing to diff
    assert out["steps"] == []
    assert out["total_dropped"] == 0 and out["max_step_dropped"] == 0


def test_to_bench_json_keeps_the_frame_sentinel_and_trends_live_latency():
    mod = _jank_module()
    out = mod.to_bench_json(
        {
            "total_dropped": 3,
            "total_delayed": 1,
            "steps": [
                {"step": "hover", "interaction_ms": 12.5},
                {"step": "scroll", "interaction_ms": 30.0},
            ],
        }
    )
    assert [e["name"] for e in out] == [
        "live jank: total dropped frames",
        "live jank: total delayed frames",
        "live: hover interaction latency",
        "live: four-scroll interaction latency",
    ]
    assert [e["unit"] for e in out] == ["frames", "frames", "ms", "ms"]
    assert [e["value"] for e in out] == [3, 1, 12.5, 30.0]


def test_scroll_workload_requires_the_tooltip_viewport_to_advance():
    mod = _jank_module()

    class Reader:
        osd = (1920, 1080)

        def __init__(self):
            self.tip = SimpleNamespace(view=SimpleNamespace(scroll=0))

        def _scroll_tip(self, delta):
            self.tip.view.scroll += delta

        def pump(self):
            return True

    reader = Reader()
    mod._scroll_four(reader)
    assert reader.tip.view.scroll == 4 * round(1080 * 0.12)


def test_scroll_workload_rejects_a_non_scrollable_tooltip():
    mod = _jank_module()

    class Reader:
        osd = (1920, 1080)

        def __init__(self):
            self.tip = SimpleNamespace(view=SimpleNamespace(scroll=0))

        def _scroll_tip(self, _delta):
            pass

        def pump(self):
            return True

    with pytest.raises(RuntimeError, match="did not advance"):
        mod._scroll_four(Reader())


def test_live_latency_boundary_repaints_the_overlay():
    mod = _jank_module()

    class Overlay:
        repaints = 0

        def repaint(self):
            self.repaints += 1

    class Reader:
        ov = Overlay()

    reader = Reader()
    mod._present_overlay(reader)
    assert reader.ov.repaints == 1
