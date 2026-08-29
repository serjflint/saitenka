"""Deterministic session-replay scenarios across the layout-backend axis (test-coverage-plan Phase 4).

The state machine (``test_tooltip_statemachine``) drives the real controller through *random* sequences
at the default backend; this is its complement — a handful of PINNED cue→hover→scroll→navigate→back→
nested→resize cadences (a regression-stable "scenario SOURCE", not a new oracle), run through the SAME
render↔hit-test agreement oracle after every step, and — the Phase-A2/B point — swept over every layout
backend. So the Rust ``taffy`` engine is exercised end to end through the whole controller→panel→hit
path under a realistic multi-step session, not just in isolation (``test_layout_backend``).

The DSL is a flat list of ``(action, arg)`` steps applied to a fresh reader; ``_assert_agrees`` (the
Phase-1 inverse-transform + one-panel oracle, reused verbatim) fires after each. Deterministic — no
Hypothesis — so a failure names the exact scenario + backend + step, and re-runs identically.
"""

from __future__ import annotations

import pytest
import util

# Reuse the real controller fixture + the agreement oracle the state machine already proves has teeth
# (its negative control, test_the_agreement_oracle_has_teeth) — Phase 4 is a scenario source, not a new
# oracle, so it asserts through exactly that check.
from test_tooltip_statemachine import _NAV_QUERY, _assert_agrees, _fresh_reader

from saitenka.app.features.tooltip import nested_popup, tooltip, tooltip_panel

Step = tuple[str, object]

# Each scenario is a legal cadence (preconditions respected: scroll/navigate/back/nested only after a
# hover). Together they cover base scroll, deep navigate+back to the root, a nested popup, and live
# resizes across the crisp scales — the transitions where a stale scroll/scale/panel would drift.
SCENARIOS: dict[str, list[Step]] = {
    "scroll_base": [("hover", 0), ("scroll", 300), ("scroll", -150), ("scroll", 500)],
    "navigate_deep": [
        ("hover", 1),
        ("navigate", None),
        ("navigate", None),
        ("back", None),
        ("scroll", 200),
        ("back", None),
        ("back", None),  # one past the root → caller would close; model/impl must stay coherent
    ],
    "nested_then_resize": [
        ("hover", 2),
        ("open_nested", None),
        ("scroll", 120),
        ("resize", 1.76),
    ],
    "resize_sweep": [
        ("hover", 0),
        ("resize", 1.5),
        ("scroll", 260),
        ("resize", 2.0),
        ("navigate", None),
        ("resize", 1.76),
    ],
}


def _apply(reader, action: str, arg: object) -> None:
    # `hover` and `scroll` enter below the input seam, for the reasons the state machine's own rules
    # give: the fixture's tooltip covers words 1–2, and a scroll offset is what the oracle is about.
    reader = reader.turn
    if action == "hover":
        reader.tooltip_controller.show_tooltip(int(arg))  # type: ignore[arg-type]
    elif action == "scroll":
        reader.tooltip_controller.scroll_tip(int(arg))  # type: ignore[arg-type]
    elif action == "navigate":
        tooltip.navigate_tip(
            reader.tooltip_controller.tip_ports, reader.tooltip_controller.panel_ports, _NAV_QUERY
        )
    elif action == "back":
        tooltip.tip_back(reader.tooltip_controller.tip_ports)
    elif action == "open_nested":
        tok = reader.subtitle_presentation.cue.current.tokens[0]
        nested_popup.open_nested(
            reader.tooltip_controller.tip_ports,
            reader.tooltip_controller.panel_ports,
            tok,
            tok.surface,
            nested_popup.Anchor(200.0, 200.0, 40.0),
        )
    elif action == "resize":
        scale = float(arg)  # type: ignore[arg-type]
        reader.screen.osd = (
            round(1920 * scale),
            round(1080 * scale),
        )  # live → changes tip_scale.raster
        tooltip_panel.render_view(
            reader.tooltip_controller.tip_ports, reader.tooltip_controller.surface_state().view
        )
    else:  # pragma: no cover - guards a typo in a scenario table
        raise AssertionError(f"unknown action {action!r}")


@pytest.mark.integration
@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    ("backend_name", "backend"),
    util.layout_backends(),
    ids=[n for n, _ in util.layout_backends()],
)
@pytest.mark.parametrize("scenario", list(SCENARIOS), ids=list(SCENARIOS))
def test_session_replay_keeps_the_seam_under_each_backend(scenario, backend_name, backend):
    reader = _fresh_reader()
    reader.turn.tooltip_controller.visual.backend = (
        backend  # every Panel.from_rows now builds via this engine
    )
    reader.turn.tooltip_controller.visual.backend_name = backend_name
    for action, arg in SCENARIOS[scenario]:
        _apply(reader, action, arg)
        # The render↔hit-test agreement holds after every transition, base panel and (when open) nested.
        _assert_agrees(reader, nested=False)
        if reader.turn.tooltip_controller.surface_state().nest.state is not None:
            _assert_agrees(reader, nested=True)
