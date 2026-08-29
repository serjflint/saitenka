"""Model-vs-impl state machine for the tooltip session (test-coverage-plan Phase 3 / gap G4).

This drives the REAL controller through arbitrary hover/scroll/navigate/back/open_nested/resize sequences
and asserts, after every step, three things:

  * the model matches the impl — base shown ⇔ `_tip_state`, nav-depth ⇔ the back-stack's depth, nested ⇔ `_nest`;
  * the ONE-PANEL invariant — `hit_target`'s panel IS the panel the blit composites from
    (`_tip_state` / `_nest.state`); a reintroduced second draw-panel (the Session5b two-geometry split)
    would make these diverge and fail this assertion;
  * inverse-transform correctness — every visible drawn element's displayed centre round-trips back to
    that element through the real `scan_hit` / `link_hit_at`.

**What this does and does NOT catch (honest scope — see the review, finding C1).** The round-trip is
*self-consistent by construction*: it derives each element's centre from `hit_target`'s panel and inverts
through the same `hit_target`, so it verifies the inverse transform and that state stays coherent across
transitions (a back-stack tuple that restored scroll but not its panel, a resize that didn't reach the hit
path) — NOT that the drawn pixels match a *different* panel. Two-panel wrap drift is caught by the explicit
one-panel invariant above (and is prevented structurally by the scale-boundary rewrite: there is only one
panel), not by the round-trip. Hypothesis shrinks any failing sequence. `integration`-marked → `poe all`.

    uv run python -m pytest tests/features/tooltip/test_tooltip_statemachine.py
"""

from __future__ import annotations

from functools import partial

import pytest
from driver import Driver
from hypothesis import HealthCheck, event, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from tip_fakes import hidpi_reader

from saitenka.app.features.tooltip import nested_popup, tooltip, tooltip_panel
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.subtitles import WordBox
from saitenka.app.tokenize import Token

_SCALES = [1.5, 1.76, 2.0]
_NAV_QUERY = "見*"  # wildcard → always resolves via LinkingDS.search, so navigate is deterministic


def _fresh_reader():
    """A hi-dpi reader with three hoverable words (the shared LinkingDS entry backs each), crisp on."""
    r = hidpi_reader(2.0)
    turn = r.turn
    turn.subtitle_presentation.renderer = NullRenderer()  # headless; no real mpv subtitle draw
    turn.subtitle_presentation.cue.replace_tokenized(
        tokens=[
            Token("本命", "本命", "ほんめい", "名詞", 0, 2),
            Token("掛ける", "掛ける", "かける", "動詞", 2, 5),
            Token("見る", "見る", "みる", "動詞", 5, 7),
        ]
    )
    turn.subtitle_presentation.cue.replace_geometry(
        boxes=[WordBox(i, 100, 300 + 60 * i, 40, 40) for i in range(3)]
    )
    turn.tooltip_controller.surface_state().last_mouse = (0, 0)
    return r


def _assert_agrees(reader, *, nested: bool) -> None:
    """The render↔hit-test agreement oracle for one view: every VISIBLE drawn element's displayed centre
    round-trips back to that element through the real controller hit path (the same transform, inverted).
    Only the on-screen window is measured + tested — you can only click what's drawn, and a full-panel
    measure per step is too slow across a stateful run (the corners get covered as scroll moves the
    window)."""
    reader = reader.turn
    panel, s, scroll = tooltip_panel.hit_target(
        reader.tooltip_controller.surface_state().nest,
        reader.tooltip_controller.surface_state().view.state,
        reader.tooltip_controller.surface_state().view.scroll,
        reader.tooltip_controller.scale().raster,
        nested=nested,
    )
    if panel is None:
        return
    # One-panel invariant (C1): the hit-tested panel IS the one the blit composites from. This — not the
    # self-consistent round-trip below — is what would catch a reintroduced second draw-panel.
    drawn = (
        reader.tooltip_controller.surface_state().nest.state
        if nested
        else reader.tooltip_controller.surface_state().view.state
    )
    assert panel is drawn, (
        f"hit_target panel is not the drawn panel (two-panel regression, nested={nested})"
    )
    view_h = (
        reader.tooltip_controller.surface_state().nest.view_h
        if nested
        else reader.tooltip_controller.surface_state().view.view_h
    )
    panel.windowed.viewport(scroll, view_h)  # measure just the visible band range
    xy = (
        reader.tooltip_controller.surface_state().nest.xy
        if nested
        else reader.tooltip_controller.surface_state().view.xy
    )
    if xy is None:
        return
    sx, sy = xy
    lo, hi = scroll, scroll + view_h
    link_hit = partial(
        tooltip_panel.link_hit_at,
        reader.tooltip_controller.surface_state(),
        reader.tooltip_controller.scale().raster,
        nested=nested,
    )
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
            assert (
                tooltip_panel.scan_hit(
                    reader.tooltip_controller.surface_state(),
                    reader.tooltip_controller.scale().raster,
                    mx,
                    my,
                )
                == b
            ), f"scan mis-hit (scale={s} scroll={scroll}): {b}"


@settings(
    max_examples=25,
    stateful_step_count=24,
    deadline=None,  # a cold panel build + full measure per step is legitimately slow
    suppress_health_check=[HealthCheck.too_slow],
)
class TooltipSession(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.session = _fresh_reader()
        self.r = self.session.turn
        self.shown = False
        self.nav_depth = 0
        self.nested_open = False

    @rule(idx=st.integers(min_value=0, max_value=2))
    def hover(self, idx: int) -> None:
        # Below the input seam, like `scroll` below, and for a sharper reason: the fixture's tooltip
        # (1280×864 at y=352) covers words 1 and 2, so a cursor that opened word 0 can never reach
        # another one — correct runtime behaviour, and it collapses this rule to a single word. The
        # subject here is panel geometry across transitions, not which word a cursor can pick.
        self.r.tooltip_controller.show_tooltip(
            idx
        )  # a fresh hover resets nav history and drops any nested popup
        self.shown, self.nav_depth, self.nested_open = True, 0, False
        self._check("hover")

    @precondition(lambda self: self.shown)
    @rule(delta=st.integers(min_value=-500, max_value=500))
    def scroll(self, delta: int) -> None:
        # Below the input seam on purpose: the oracle is scroll *position* vs drawn geometry, and the
        # two producers (a wheel notch, a TIP_UP/DOWN page) only reach a handful of the offsets where
        # clamping and band boundaries live. A notch count would not explore them.
        self.r.tooltip_controller.scroll_tip(delta)
        self._check("scroll")

    @precondition(lambda self: self.shown)
    @rule()
    def navigate(self) -> None:
        before = self.r.tooltip_controller.observation().navigation_depth
        tooltip.navigate_tip(
            self.r.tooltip_controller.tip_ports, self.r.tooltip_controller.panel_ports, _NAV_QUERY
        )
        assert (
            self.r.tooltip_controller.observation().navigation_depth == before + 1
        )  # the wildcard target always resolves
        assert (
            self.r.tooltip_controller.surface_state().view.key is None
        )  # a navigated view is keyless (one panel, no synthetic key)
        self.nav_depth += 1
        self.nested_open = False  # navigate hides the stale nested popup
        self._check("navigate")

    @precondition(lambda self: self.shown)
    @rule()
    def back(self) -> None:
        expected = self.nav_depth > 0
        assert (
            tooltip.tip_back(self.r.tooltip_controller.tip_ports) == expected
        )  # False at the root → caller closes the tooltip
        if expected:
            self.nav_depth -= 1
        self._check("back")

    @precondition(lambda self: self.shown)
    @rule()
    def open_nested(self) -> None:
        tok = self.r.subtitle_presentation.cue.current.tokens[0]
        nested_popup.open_nested(
            self.r.tooltip_controller.tip_ports,
            self.r.tooltip_controller.panel_ports,
            tok,
            tok.surface,
            nested_popup.Anchor(200.0, 200.0, 40.0),
        )
        self.nested_open = self.r.tooltip_controller.surface_state().nest.state is not None
        self._check("open_nested")

    @precondition(lambda self: self.shown)
    @rule(scale=st.sampled_from(_SCALES))
    def resize(self, scale: int) -> None:
        self.r.screen.osd = (
            round(1920 * scale),
            round(1080 * scale),
        )  # live → changes tip_scale.raster
        tooltip_panel.render_view(
            self.r.tooltip_controller.tip_ports, self.r.tooltip_controller.surface_state().view
        )  # re-blit at the new scale
        self._check("resize")

    @invariant()
    def model_matches_impl(self) -> None:
        assert (self.r.tooltip_controller.surface_state().view.state is not None) == self.shown
        assert self.r.tooltip_controller.observation().navigation_depth == self.nav_depth
        assert (
            self.r.tooltip_controller.surface_state().nest.state is not None
        ) == self.nested_open

    def _check(self, action: str) -> None:
        view = "nested" if self.nested_open else ("nav" if self.nav_depth else "base")
        event(
            f"action={action} view={view} scale={self.r.tooltip_controller.scale().raster}"
        )  # drift-gate signal
        _assert_agrees(self.session, nested=False)
        if self.r.tooltip_controller.surface_state().nest.state is not None:
            _assert_agrees(self.session, nested=True)


TestTooltipSession = pytest.mark.integration(TooltipSession.TestCase)


@pytest.mark.integration
def test_the_agreement_oracle_has_teeth() -> None:
    # Negative control (arm-2 oracle-liveness made permanent): the invariant isn't vacuous. Correct
    # displayed centres round-trip, but a deliberately DRIFTED transform mis-hits — so a real seam
    # regression (a stale scroll / scale / panel after some transition) would turn the state machine red.
    session = _fresh_reader()
    r = session.turn
    Driver(session).move_to_word(0)
    panel, s, scroll = tooltip_panel.hit_target(
        r.tooltip_controller.surface_state().nest,
        r.tooltip_controller.surface_state().view.state,
        r.tooltip_controller.surface_state().view.scroll,
        r.tooltip_controller.scale().raster,
        nested=False,
    )
    panel.windowed.viewport(scroll, r.tooltip_controller.surface_state().view.view_h)
    sx, sy = r.tooltip_controller.surface_state().view.xy
    lo, hi = scroll, scroll + r.tooltip_controller.surface_state().view.view_h
    visible = [b for b in panel.windowed.scan_boxes() if lo <= b.y + b.h / 2 < hi]
    assert visible  # the fixture shows scan cells to hit-test
    for b in visible:  # the true centres round-trip (the oracle passes on correct geometry)
        assert (
            tooltip_panel.scan_hit(
                r.tooltip_controller.surface_state(),
                r.tooltip_controller.scale().raster,
                sx + (b.x + b.w / 2) * s,
                sy + (b.y + b.h / 2 - scroll) * s,
            )
            == b
        )
    drifted = sum(
        tooltip_panel.scan_hit(
            r.tooltip_controller.surface_state(),
            r.tooltip_controller.scale().raster,
            sx + (b.x + b.w / 2) * s,
            sy + (b.y + b.h / 2 - scroll) * s + 40 * s,
        )
        != b
        for b in visible
    )
    assert drifted > 0  # a 40px transform drift mis-hits → the invariant can fail (has teeth)
