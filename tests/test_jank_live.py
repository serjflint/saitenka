"""The live-jank harness's pure reduction seam (#32): cumulative mpv frame-drop counters → per-step
dropped/delayed frames. No mpv, no display — the humble-object part the real harness delegates to."""

import importlib.util
from pathlib import Path

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
        {"total_dropped": 3, "max_interaction_ms": 12.5, "total_interaction_ms": 30.0}
    )
    assert [e["name"] for e in out] == [
        "live jank: total dropped frames",
        "live: worst interaction latency",
        "live: scripted interaction latency",
    ]
    assert [e["unit"] for e in out] == ["frames", "ms", "ms"]
    assert [e["value"] for e in out] == [3, 12.5, 30.0]
