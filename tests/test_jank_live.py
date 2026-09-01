"""The live-jank harness's pure reduction seam (#32): cumulative mpv frame-drop counters → per-step
dropped/delayed frames. No mpv, no display — the humble-object part the real harness delegates to."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from saitenka.app.session import surfaces
from saitenka.app.session.factory import SessionServices

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

    class SessionController:
        osd = (1920, 1080)
        tip_scale = SimpleNamespace(ref_h=1080)

        def __init__(self):
            self.tip = SimpleNamespace(view=SimpleNamespace(scroll=0))
            self.tooltip_controller = SimpleNamespace(
                surface_state=lambda: self.tip,
                scale=lambda: SimpleNamespace(ref_h=1080),
                scroll_tip=lambda delta: setattr(
                    self.tip.view,
                    "scroll",
                    self.tip.view.scroll + delta,
                ),
            )

        def pump(self):
            return True

    reader = SessionController()
    mod._scroll_four(reader.tooltip_controller, reader.pump)
    # Through the shared conversion, not a second copy of the arithmetic: a hard-coded fraction
    # here would keep passing after the wheel's step changed.
    assert reader.tip.view.scroll == 4 * surfaces.tip_wheel_pixels(1080, 1)


def _stuck_controller(mod_state):
    """A controller whose wheel does nothing, carrying the tooltip state `_why_stuck` reports."""

    class SessionController:
        osd = (1920, 1080)
        tip_scale = SimpleNamespace(ref_h=1080)

        def __init__(self):
            self.tip = SimpleNamespace(
                view=SimpleNamespace(
                    scroll=0, desired_scroll=0, rect=(0, 0, 400, 300), **mod_state
                ),
                nest=SimpleNamespace(rect=None, scroll=0),
                last_mouse=(10.0, 20.0),
            )
            self.tooltip_controller = SimpleNamespace(
                surface_state=lambda: self.tip,
                scale=lambda: SimpleNamespace(ref_h=1080),
                scroll_tip=lambda _delta: None,
            )

        def pump(self):
            return True

    return SessionController()


def test_scroll_workload_rejects_a_non_scrollable_tooltip():
    mod = _jank_module()
    reader = _stuck_controller({"state": SimpleNamespace(full_height=1440), "view_h": 432})
    with pytest.raises(RuntimeError, match="did not advance"):
        mod._scroll_four(reader.tooltip_controller, reader.pump)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        # The three ways scroll_view refuses, each needing a different fix — so the message has to
        # tell them apart rather than say "did not advance" and leave the reader to guess.
        ({"state": None, "view_h": 432}, "state=MISSING"),
        ({"state": SimpleNamespace(full_height=200), "view_h": 432}, "scrollable=False"),
        ({"state": SimpleNamespace(full_height=1440), "view_h": 432}, "scrollable=True"),
    ],
)
def test_the_stuck_scroll_message_names_the_reason(state, expected):
    mod = _jank_module()
    reader = _stuck_controller(state)
    with pytest.raises(RuntimeError, match="did not advance") as excinfo:
        mod._scroll_four(reader.tooltip_controller, reader.pump)
    assert expected in str(excinfo.value)
    # Routing is the third possibility and is invisible from the base view alone: report the nested
    # popup too, or a wheel that landed on it reads identically to one that landed nowhere.
    assert "nest_rect=" in str(excinfo.value)


def test_the_harness_dictionary_makes_the_tooltip_scrollable_in_the_live_order(make_session):
    """The regression the live replicas kept hitting, without a display: the workload's own dictionary
    has to be in place *before* the cue resolves its entries.

    Driving the cue first and swapping afterwards is what the harness used to do, and it silently kept
    the one-line entries — the panel came out exactly its viewport's height, so the wheel had nothing to
    move and `_scroll_four` raised. Asserting the height relation rather than a pixel count: the point
    is that something is there to scroll, not how much.
    """
    from driver import Driver
    from util import FakeIPC, await_ready

    mod = _jank_module()
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    reader = make_session(ipc, services=SessionServices(dictionaries=mod.TallDS()))
    try:
        reader.start()
        reader.pump()
        reader.graph.cue.set_subtitle("門前の小僧習わぬ経を読む")
        word = next(
            i
            for i, t in enumerate(reader.graph.subtitle_presentation.cue.current.tokens)
            if reader.graph.profile.profile.tokenizer.is_content(t)
        )
        Driver(reader).move_to_word(
            word
        ).leave()  # resolve the cue's entries, as the live harness does
        for _ in range(4):
            reader.pump()

        Driver(reader).move_to_word(word)
        await_ready(
            lambda: reader.graph.tooltip.surface_state().view.state is not None,
            "tooltip panel was not published",
            pump=reader.pump,
        )
        view = reader.graph.tooltip.surface_state().view
        assert view.state is not None
        assert view.state.full_height > view.view_h, mod._why_stuck(reader.graph.tooltip)
        mod._scroll_four(reader.graph.tooltip, reader.pump)  # raises if the viewport did not move
        # Resolve the repaint target against a REAL graph. `test_live_latency_boundary_repaints_the
        # _overlay` hands `_present_overlay` a double, so it stayed green for the whole period the
        # harness asked the graph for a member that no longer existed — the AttributeError could only
        # surface in the weekly live job, behind a step that was failing for its own reasons.
        mod._present_overlay(reader.graph.lifecycle_surfaces)
    finally:
        reader.close()


def test_live_latency_boundary_repaints_the_overlay():
    mod = _jank_module()

    class Overlay:
        repaints = 0

        def repaint(self):
            self.repaints += 1

    class Graph:
        overlay = Overlay()

    class SessionController:
        graph = Graph()

    reader = SessionController()
    mod._present_overlay(reader.graph.overlay)
    assert reader.graph.overlay.repaints == 1
