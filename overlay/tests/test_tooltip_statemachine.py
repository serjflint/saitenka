"""Model-vs-impl state machine for the tooltip session (test-coverage-plan Phase 3 / gap G4).

`test_scale_boundary` proves the render↔hit-test agreement oracle for a SINGLE action (show, navigate).
The regressions that motivated the whole seam rewrite, though, emerged from INTERACTION across a session —
a native-vs-reference panel drift that only bit while scrolling, a navigated view that combined badly with
a key-gated feature. This drives the REAL controller through arbitrary hover/scroll/navigate/back/nested/
resize sequences and asserts, after every step, that:

  * the model matches the impl — base shown ⇔ `_tip_state`, nav-depth ⇔ `len(_tip_nav)`, nested ⇔ `_nest`;
  * the agreement oracle holds for the current view(s) — every drawn scan cell / link box round-trips
    through the real `_scan_hit` / `_tip_link_hit` / `_nest_link_hit` at the displayed centre.

The oracle is the acceptance test of the one-panel rewrite made STATEFUL: it catches a transition that
leaves `hit_target`'s (panel, scale, scroll) inconsistent with `_tip_xy` — e.g. a back-stack tuple that
restored scroll but not its panel, or a resize that didn't reach the hit path. Hypothesis shrinks any
failing action sequence to the minimal one. `integration`-marked → runs in `poe all`, not the fast loop.

    uv run python -m pytest tests/test_tooltip_statemachine.py
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, event, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from tip_fakes import hidpi_reader

from overlay.app import tooltip
from overlay.app.subtitle_render import NullRenderer
from overlay.app.subtitles import WordBox
from overlay.app.tokenize import Token

_SCALES = [1.5, 1.76, 2.0]
_NAV_QUERY = "見*"  # wildcard → always resolves via LinkingDS.search, so navigate is deterministic


def _fresh_reader():
    """A hi-dpi reader with three hoverable words (the shared LinkingDS entry backs each), crisp on."""
    r = hidpi_reader(2.0)
    r.renderer = NullRenderer()  # headless; no real mpv subtitle draw
    r.tokens = [
        Token("本命", "本命", "ほんめい", "名詞", 0, 2),
        Token("掛ける", "掛ける", "かける", "動詞", 2, 5),
        Token("見る", "見る", "みる", "動詞", 5, 7),
    ]
    r.boxes = [WordBox(i, 100, 300 + 60 * i, 40, 40) for i in range(3)]
    r._last_mouse = (0, 0)
    return r


def _assert_agrees(reader, *, nested: bool) -> None:
    """The render↔hit-test agreement oracle for one view: every VISIBLE drawn element's displayed centre
    round-trips back to that element through the real controller hit path (the same transform, inverted).
    Only the on-screen window is measured + tested — you can only click what's drawn, and a full-panel
    measure per step is too slow across a stateful run (the corners get covered as scroll moves the
    window)."""
    panel, s, scroll = tooltip.hit_target(reader, nested=nested)
    if panel is None:
        return
    view_h = reader._nest.view_h if nested else reader._tip_view_h
    panel.windowed.viewport(scroll, view_h)  # measure just the visible band range
    xy = reader._nest.xy if nested else reader._tip_xy
    if xy is None:
        return
    sx, sy = xy
    lo, hi = scroll, scroll + view_h
    link_hit = reader._nest_link_hit if nested else reader._tip_link_hit
    for lb in panel.windowed.link_boxes():
        if not (lo <= lb.y + lb.h / 2 < hi):
            continue
        mx, my = sx + (lb.x + lb.w / 2) * s, sy + (lb.y + lb.h / 2 - scroll) * s
        assert link_hit(mx, my) == lb, (
            f"link mis-hit (nested={nested} scale={s} scroll={scroll}): {lb}"
        )
    if not nested:  # scan-hit is the base-tooltip seam (nested exposes only link-hit)
        for b in panel.windowed.scan_boxes():
            if not (lo <= b.y + b.h / 2 < hi):
                continue
            mx, my = sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s
            assert reader._scan_hit(mx, my) == b, f"scan mis-hit (scale={s} scroll={scroll}): {b}"


@settings(
    max_examples=25,
    stateful_step_count=24,
    deadline=None,  # a cold panel build + full measure per step is legitimately slow
    suppress_health_check=[HealthCheck.too_slow],
)
class TooltipSession(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.r = _fresh_reader()
        self.shown = False
        self.nav_depth = 0
        self.nested_open = False

    @rule(idx=st.integers(min_value=0, max_value=2))
    def hover(self, idx: int) -> None:
        self.r._show_tooltip(idx)  # a fresh hover resets nav history and drops any nested popup
        self.shown, self.nav_depth, self.nested_open = True, 0, False
        self._check("hover")

    @precondition(lambda self: self.shown)
    @rule(delta=st.integers(min_value=-500, max_value=500))
    def scroll(self, delta: int) -> None:
        tooltip.scroll_tip(self.r, delta)
        self._check("scroll")

    @precondition(lambda self: self.shown)
    @rule()
    def navigate(self) -> None:
        before = len(self.r._tip_nav)
        tooltip.navigate_tip(self.r, _NAV_QUERY)
        assert len(self.r._tip_nav) == before + 1  # the wildcard target always resolves
        assert self.r._tip_key is None  # a navigated view is keyless (one panel, no synthetic key)
        self.nav_depth += 1
        self.nested_open = False  # navigate hides the stale nested popup
        self._check("navigate")

    @precondition(lambda self: self.shown)
    @rule()
    def back(self) -> None:
        expected = self.nav_depth > 0
        assert tooltip.tip_back(self.r) == expected  # False at the root → caller closes the tooltip
        if expected:
            self.nav_depth -= 1
        self._check("back")

    @precondition(lambda self: self.shown)
    @rule()
    def open_nested(self) -> None:
        tok = self.r.tokens[0]
        self.r._open_nested(tok, tok.surface, 200.0, 200.0, 40.0)
        self.nested_open = self.r._nest.state is not None
        self._check("open_nested")

    @precondition(lambda self: self.shown)
    @rule(scale=st.sampled_from(_SCALES))
    def resize(self, scale: int) -> None:
        self.r.osd = (round(1920 * scale), round(1080 * scale))  # live → changes _raster_scale
        tooltip.render_tip_view(self.r)  # re-blit at the new scale
        self._check("resize")

    @invariant()
    def model_matches_impl(self) -> None:
        assert (self.r._tip_state is not None) == self.shown
        assert len(self.r._tip_nav) == self.nav_depth
        assert (self.r._nest.state is not None) == self.nested_open

    def _check(self, action: str) -> None:
        view = "nested" if self.nested_open else ("nav" if self.nav_depth else "base")
        event(f"action={action} view={view} scale={self.r._raster_scale}")  # drift-gate signal
        _assert_agrees(self.r, nested=False)
        if self.r._nest.state is not None:
            _assert_agrees(self.r, nested=True)


TestTooltipSession = pytest.mark.integration(TooltipSession.TestCase)


@pytest.mark.integration
def test_the_agreement_oracle_has_teeth() -> None:
    # Negative control (arm-2 oracle-liveness made permanent): the invariant isn't vacuous. Correct
    # displayed centres round-trip, but a deliberately DRIFTED transform mis-hits — so a real seam
    # regression (a stale scroll / scale / panel after some transition) would turn the state machine red.
    r = _fresh_reader()
    r._show_tooltip(0)
    panel, s, scroll = tooltip.hit_target(r, nested=False)
    panel.windowed.viewport(scroll, r._tip_view_h)
    sx, sy = r._tip_xy
    lo, hi = scroll, scroll + r._tip_view_h
    visible = [b for b in panel.windowed.scan_boxes() if lo <= b.y + b.h / 2 < hi]
    assert visible  # the fixture shows scan cells to hit-test
    for b in visible:  # the true centres round-trip (the oracle passes on correct geometry)
        assert r._scan_hit(sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s) == b
    drifted = sum(
        r._scan_hit(sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s + 40 * s) != b
        for b in visible
    )
    assert drifted > 0  # a 40px transform drift mis-hits → the invariant can fail (has teeth)
