"""The main thread's 1× compose, driven the way production drives it.

Every other banded test calls `viewport()` / `viewport_bgra()` **non-warm** — the rastering,
evicting path, which on the main thread is exactly what `guard_main_render` forbids. So the shipped
compositor (`warm_only=True`, bands warmed by a worker) was covered by nothing, and three defects sat
green: `missed_last_assemble` was structurally true, `viewport_warm` disagreed with it every tick,
and no production path evicted at all.

Warms through the worker API and asserts against the frame, which is the precondition rule in
AGENTS.md — a back door that stores bands directly would prove the compositor works on a cache
production never assembles.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from saitenka.panel import Definition, Entry, Row, panel_rows
from saitenka.render.banded import _AHEAD_BANDS, _BAND_PX, WindowedPanel

WIDTH = 384
VIEW_H = 300


def _panel(n_defs: int = 20, length: int = 4) -> WindowedPanel:
    entry = Entry(
        headword=["掛ける", {"tag": "rt", "content": "かける"}],
        defs=[
            Definition(f"辞書{i}", [f"意味{i}：長い説明文が縦に伸びていく本文。" * length])
            for i in range(n_defs)
        ],
    )
    return WindowedPanel(panel_rows(entry, WIDTH), WIDTH)


def _warm_like_a_worker(wp: WindowedPanel, scroll: int, view_h: int = VIEW_H) -> None:
    """What `run_render_ahead` owes the frame: the destination, then the lookahead."""
    wp.warm_viewport(scroll, view_h)
    wp.render_ahead(scroll, view_h, direction=1, overscan=view_h, workers=1)


def test_the_warm_compose_matches_the_rastering_one_pixel_for_pixel():
    """The differential oracle the main thread's compositor never had.

    `_assemble_bgra` has an equivalence proof against `_composite_bands`, which has one against a
    one-shot `render_panel`. `_assemble_warm_1x` is a fourth assembler with no link into that chain,
    and it is the only one production runs.
    """
    for scroll in (0, 300, 900, 1500):
        warm, reference = _panel(), _panel()
        _warm_like_a_worker(warm, scroll)
        got = warm.viewport_bgra(scroll, VIEW_H, VIEW_H, warm_only=True)
        expected = reference.viewport_bgra(scroll, VIEW_H, VIEW_H)  # rasters what it needs

        assert not warm.missed_last_assemble, f"scroll={scroll} left a band as background"
        assert np.array_equal(got, expected), f"warm compose diverged at scroll={scroll}"


@pytest.mark.parametrize("scroll", [0, 150, 300, 900, 1500, 2400])
def test_the_warmth_predicate_agrees_with_the_frame_it_gates(scroll):
    """`viewport_warm` and `missed_last_assemble` answer one question and must not disagree.

    They did, in two independent ways: the predicate walked one screen while `blit_panel` composes
    at `overscan=view_h`, and the plan counted every band of a tall row including the ones below the
    fold. `apply_pending_crisp` cleared on the predicate and re-armed on the flag, so the tooltip
    re-composited and re-uploaded itself on every 25ms poll tick for as long as it stayed open.
    """
    wp = _panel()
    _warm_like_a_worker(wp, scroll)
    predicate = wp.viewport_warm(scroll, VIEW_H)
    wp.viewport_bgra(scroll, VIEW_H, VIEW_H, warm_only=True)  # the compose blit_panel issues

    assert predicate is not wp.missed_last_assemble


def test_the_predicate_still_reports_a_genuinely_cold_viewport():
    """The negative control: the two agreeing is worthless if both are pinned to warm.

    The fix moved `missed_last_assemble` from always-true toward the frame's real needs, so the
    failure mode it could introduce is always-false — a partial frame that never asks to be redrawn.
    """
    wp = _panel()
    wp.measure_to(4000)  # heights known, no band rastered

    assert not wp.viewport_warm(600, VIEW_H)
    wp.viewport_bgra(600, VIEW_H, VIEW_H, warm_only=True)
    assert wp.missed_last_assemble


def test_a_tall_non_body_row_is_one_band_to_every_reader():
    """`_row_band_spans` is how a row is STORED; `_row_bands` is how a body row is TILED.

    A non-body row (header/chip/freq) taller than one band is stored whole, so the tiler invents a
    second band that is missing forever. `_row_band_spans`' own comment records this breaking once
    — the composite left a hi-dpi header's lower half blank — while four query sites went on using
    the tiler.
    """
    height = _BAND_PX + 144

    def render(*_args, **_kwargs):
        return Image.new("RGBA", (WIDTH, height), (10, 20, 30, 255)), [], []

    wp = WindowedPanel([Row(0, render)], WIDTH)  # no render_window → non-body
    wp.viewport_bgra(0, VIEW_H, 0)  # stores it as the single band (0, 0)

    assert wp.cached_blocks == 1
    assert wp.viewport_warm(0, VIEW_H)
    wp.viewport_bgra(0, VIEW_H, 0, warm_only=True)
    assert not wp.missed_last_assemble, "the tiler invented a band nothing will ever store"


def test_retained_pixels_stay_bounded_across_a_long_scroll():
    """`WindowedPanel`'s headline bound, on the path the app takes.

    `_evict` was reached only from the two rastering assemblers, and the shipped main thread stopped
    calling those when it became warm-only — so a panel's band cache only grew. Measured 0.51 to
    3.29 MB over eight notches before this, with the LRU cap never engaging.
    """
    wp = _panel(n_defs=30, length=8)
    wp.measure_to(10**6)
    slack = VIEW_H + _AHEAD_BANDS * _BAND_PX  # `_retention_window`, spelled from the same constants
    budget = (2 * slack + VIEW_H) * WIDTH * 4

    assert wp.full_height > 2 * (2 * slack + VIEW_H), "the panel must outrun its retention window"

    retained = []
    for scroll in range(0, wp.full_height, VIEW_H):
        _warm_like_a_worker(wp, scroll)
        wp.viewport_bgra(scroll, VIEW_H, VIEW_H, warm_only=True)
        retained.append(wp.retained_nbytes)

    assert max(retained) <= budget, "retained pixels outgrew the window they are bounded by"


def test_eviction_keeps_what_the_lookahead_just_warmed():
    """The bound must not fight the warm it is there to serve.

    Evicting at the compose's own `overscan` boundary would drop the bands `render_ahead` warms just
    past it — the next frame re-rasters them, and the retention fix pays for itself in jank.
    """
    wp = _panel(n_defs=30, length=8)
    _warm_like_a_worker(wp, 0)
    ahead = set(wp._blocks)
    wp.viewport_bgra(0, VIEW_H, VIEW_H, warm_only=True)

    assert ahead <= set(wp._blocks), "the compose evicted the lookahead it had just been handed"
