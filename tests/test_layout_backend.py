"""LayoutBackend seam (#113): the default arithmetic and the independent flex-column solver agree, both
satisfy the vendored column-layout fixtures, and a real panel renders pixel-identically under either.

Plus the 2-D flex-tree contract (Phase A2): ``taffylite.Tree`` — the flexbox surface Phase B will build
richer layouts on, unused by the 1-D column seam today — is differentially checked against an independent
pure-Python reference (``flex_reference``) that is itself pinned to the vendored Chrome-derived rects, so
the flex path Phase B depends on has a real oracle before any UI change leans on it."""

from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path

import numpy as np
import pytest
from flex_reference import OverflowUnsupported
from flex_reference import solve as flex_reference_solve
from hypothesis import example, given, settings
from hypothesis import strategies as st

from saitenka.panel import panel_rows, render_panel
from saitenka.render.banded import WindowedPanel
from saitenka.render.layout_backend import (
    DEFAULT_BACKEND,
    DefaultLayoutBackend,
    FlexColumnBackend,
    LayoutBackend,
    LayoutResult,
    Rect,
    TaffyLayoutBackend,
    resolve_backend,
)

_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "layout" / "column_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]

WIDTH = 384

# TaffyLayoutBackend is the optional `layout-engine` extra: importable always (its taffylite import is
# lazy), but only exercisable when the cp314t wheel is installed. Skipped in the default `poe` env; the
# CI free-threaded-wheel job installs the extra and runs the full parity gate against the Rust engine.
_HAS_TAFFY = importlib.util.find_spec("taffylite") is not None
requires_taffy = pytest.mark.skipif(not _HAS_TAFFY, reason="taffylite (layout-engine extra) absent")


def test_both_backends_are_layout_backends():
    assert isinstance(DefaultLayoutBackend(), LayoutBackend)
    assert isinstance(FlexColumnBackend(), LayoutBackend)


@requires_taffy
def test_taffy_backend_is_a_layout_backend():
    assert isinstance(TaffyLayoutBackend(), LayoutBackend)


def test_resolve_backend_default_is_the_shared_default():
    assert resolve_backend("default") is DEFAULT_BACKEND


def test_resolve_backend_unknown_name_falls_back_to_default():
    assert resolve_backend("nonsense") is DEFAULT_BACKEND


@requires_taffy
def test_resolve_backend_taffy_selects_the_taffy_engine():
    assert isinstance(resolve_backend("taffy"), TaffyLayoutBackend)


def test_resolve_backend_taffy_falls_back_to_default_when_wheel_absent(monkeypatch):
    # Simulate a missing wheel even where it IS installed: force the chokepoint's taffylite import to
    # fail, so the degrade-to-default path is exercised deterministically in every env.
    import builtins

    real_import = builtins.__import__

    def _no_taffylite(name, *args, **kwargs):
        if name == "taffylite":
            raise ImportError("simulated missing layout-engine wheel")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_taffylite)
    assert resolve_backend("taffy") is DEFAULT_BACKEND


@given(
    data=st.lists(st.tuples(st.integers(0, 90_000), st.integers(0, 200)), min_size=0, max_size=40),
    top_pad=st.integers(0, 64),
    bottom_pad=st.integers(0, 64),
)
@settings(max_examples=300, deadline=None)
def test_default_and_flex_backends_agree(data, top_pad, bottom_pad):
    heights = [h for h, _ in data]
    gaps = [g for _, g in data]
    d = DefaultLayoutBackend().cumulative(heights, gaps, top_pad)
    f = FlexColumnBackend().cumulative(heights, gaps, top_pad)
    assert d == f  # the parity gate: two independent computations, identical geometry
    # solve() (rects + order + total) also agrees end to end
    ds = DefaultLayoutBackend().solve(
        heights, WIDTH, gaps=gaps, top_pad=top_pad, bottom_pad=bottom_pad, x=16
    )
    fs = FlexColumnBackend().solve(
        heights, WIDTH, gaps=gaps, top_pad=top_pad, bottom_pad=bottom_pad, x=16
    )
    assert ds == fs


def test_every_backend_satisfies_the_vendored_fixtures():
    for backend in (DefaultLayoutBackend(), FlexColumnBackend()):
        for case in _FIXTURES:
            starts, ends = backend.cumulative(case["heights"], case["gaps"], case["top_pad"])
            total = (ends[-1] if ends else case["top_pad"]) + case["bottom_pad"]
            assert list(starts) == case["starts"], (backend, case["name"])
            assert list(ends) == case["ends"], (backend, case["name"])
            assert total == case["total"], (backend, case["name"])


def test_solve_measures_each_row_once_and_lazily():
    # The deferred-measure hook is called exactly once per row (an off-screen tall row is measured, never
    # laid out beyond its extent).
    calls: list[int] = []

    def measure(i: int) -> int:
        calls.append(i)
        return (i + 1) * 10

    res = DEFAULT_BACKEND.solve(
        range(5), WIDTH, measure, gaps=[4, 4, 4, 4, 4], top_pad=8, bottom_pad=8
    )
    assert calls == [0, 1, 2, 3, 4]  # once each, in order
    assert res.rects[0].h == 10 and res.rects[4].h == 50
    assert res.total == res.ends[-1] + 8


def _tall_entry(n_defs: int = 6):
    from saitenka.panel import Definition, Entry

    para = "これはとても長い定義の説明でありスクロールが必要になるほど縦に伸びる本文です。" * 2
    return Entry(
        headword=["本命", {"tag": "rt", "content": "ほんめい"}],
        defs=[Definition(f"辞書{i}", [para]) for i in range(n_defs)],
    )


def test_real_panel_is_pixel_identical_under_either_backend():
    # The differential on a real panel: the opt-in flex backend must render byte-identically to the
    # default across the full scroll, so it is a true drop-in. Also pins the golden path (default).
    entry = _tall_entry(6)
    rows = panel_rows(entry, WIDTH)
    total = render_panel(entry, width=WIDTH).height
    default = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, layout_backend=DefaultLayoutBackend())
    flex = WindowedPanel(rows, WIDTH, layout_backend=FlexColumnBackend())
    for scroll in range(0, max(1, total - 200), 137):
        a = np.asarray(default.viewport(scroll, 240))
        b = np.asarray(flex.viewport(scroll, 240))
        assert np.array_equal(a, b), f"backends diverged at scroll {scroll}"


@requires_taffy
@given(
    data=st.lists(st.tuples(st.integers(0, 90_000), st.integers(0, 200)), min_size=0, max_size=40),
    top_pad=st.integers(0, 64),
    bottom_pad=st.integers(0, 64),
)
@example(data=[], top_pad=0, bottom_pad=0)  # empty column
@example(data=[(100, 7)], top_pad=12, bottom_pad=8)  # single row (its trailing gap is dropped)
@example(data=[(0, 4), (0, 4), (0, 4)], top_pad=10, bottom_pad=10)  # zero-height rows
@example(data=[(30, 0), (30, 0), (30, 0)], top_pad=0, bottom_pad=0)  # zero gaps, zero padding
@example(
    data=[(90_000, 200)], top_pad=64, bottom_pad=64
)  # large values, at the float-cast boundary
@settings(max_examples=300, deadline=None)
def test_taffy_backend_agrees_with_default(data, top_pad, bottom_pad):
    # The parity gate for the Rust engine: taffy's flexbox solver must reproduce the default arithmetic
    # exactly (cumulative + full solve), so TaffyLayoutBackend is a byte-identical drop-in.
    heights = [h for h, _ in data]
    gaps = [g for _, g in data]
    assert TaffyLayoutBackend().cumulative(
        heights, gaps, top_pad
    ) == DefaultLayoutBackend().cumulative(heights, gaps, top_pad)
    ts = TaffyLayoutBackend().solve(
        heights, WIDTH, gaps=gaps, top_pad=top_pad, bottom_pad=bottom_pad, x=16
    )
    ds = DefaultLayoutBackend().solve(
        heights, WIDTH, gaps=gaps, top_pad=top_pad, bottom_pad=bottom_pad, x=16
    )
    assert ts == ds


@requires_taffy
def test_taffy_backend_satisfies_the_vendored_fixtures():
    backend = TaffyLayoutBackend()
    for case in _FIXTURES:
        starts, ends = backend.cumulative(case["heights"], case["gaps"], case["top_pad"])
        total = (ends[-1] if ends else case["top_pad"]) + case["bottom_pad"]
        assert list(starts) == case["starts"], case["name"]
        assert list(ends) == case["ends"], case["name"]
        assert total == case["total"], case["name"]


@requires_taffy
def test_real_panel_is_pixel_identical_under_taffy_backend():
    # The differential on a real panel across the full scroll: the Rust engine renders byte-identically
    # to the pure-Python default, so it is a true drop-in behind the WindowedPanel seam.
    entry = _tall_entry(6)
    rows = panel_rows(entry, WIDTH)
    total = render_panel(entry, width=WIDTH).height
    default = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, layout_backend=DefaultLayoutBackend())
    taffy = WindowedPanel(rows, WIDTH, layout_backend=TaffyLayoutBackend())
    for scroll in range(0, max(1, total - 200), 137):
        a = np.asarray(default.viewport(scroll, 240))
        b = np.asarray(taffy.viewport(scroll, 240))
        assert np.array_equal(a, b), f"taffy diverged from default at scroll {scroll}"


class _ShiftedBackend:
    """Default geometry with every row pushed down 1px — a deliberately-wrong backend, never resolved,
    used only as the negative control that proves the parity equality below is non-vacuous."""

    def cumulative(self, heights, gaps, top_pad):
        s, e = DEFAULT_BACKEND.cumulative(heights, gaps, top_pad)
        return tuple(v + 1 for v in s), tuple(v + 1 for v in e)

    def solve(self, rows, width, measure=None, *, gaps, top_pad, bottom_pad, x=0):
        r = DEFAULT_BACKEND.solve(
            rows, width, measure, gaps=gaps, top_pad=top_pad, bottom_pad=bottom_pad, x=x
        )
        return LayoutResult(
            tuple(v + 1 for v in r.starts),
            tuple(v + 1 for v in r.ends),
            tuple(Rect(rc.x, rc.y + 1, rc.w, rc.h) for rc in r.rects),
            r.order,
            r.total,
        )


def test_geometry_parity_equality_is_non_vacuous():
    # Negative control for the cumulative/solve parity gate: a backend off by 1px MUST fail the exact
    # equality the flex/taffy parity tests assert — proving those `==` checks can fail, not pass blindly.
    heights, gaps = [40, 60, 20], [5, 10, 99]
    assert _ShiftedBackend().cumulative(heights, gaps, 8) != DEFAULT_BACKEND.cumulative(
        heights, gaps, 8
    )
    args = {"gaps": gaps, "top_pad": 8, "bottom_pad": 8, "x": 16}
    assert _ShiftedBackend().solve(heights, WIDTH, **args) != DEFAULT_BACKEND.solve(
        heights, WIDTH, **args
    )


def test_real_panel_pixel_diff_is_non_vacuous():
    # Negative control for the pixel differential: two different content windows (top vs a scrolled-down
    # view of the same tall panel) must NOT be pixel-equal, so a real engine divergence could never slip
    # past the np.array_equal the panel parity tests use.
    panel = WindowedPanel(panel_rows(_tall_entry(6), WIDTH), WIDTH, layout_backend=DEFAULT_BACKEND)
    top = np.asarray(panel.viewport(0, 240))
    deeper = np.asarray(panel.viewport(240, 240))
    assert not np.array_equal(top, deeper)


@requires_taffy
@pytest.mark.parametrize("n_defs", [1, 3, 6, 12])
@pytest.mark.parametrize("width", [280, 384, 512])
def test_taffy_renders_identically_through_the_config_seam(n_defs, width):
    # The production path, not the class directly: `[tooltip] layout_engine="taffy"` →
    # resolve_backend("taffy") → WindowedPanel, differential vs the default across a matrix of entry
    # shapes and widths — so a divergence the single canonical panel misses is caught.
    taffy = resolve_backend("taffy")
    assert isinstance(
        taffy, TaffyLayoutBackend
    )  # wheel present, so the real Rust engine is exercised
    entry = _tall_entry(n_defs)
    rows = panel_rows(entry, width)
    total = render_panel(entry, width=width).height
    default = WindowedPanel(panel_rows(entry, width), width, layout_backend=DEFAULT_BACKEND)
    taffy_panel = WindowedPanel(rows, width, layout_backend=taffy)
    for scroll in range(0, max(1, total - 200), 137):
        a = np.asarray(default.viewport(scroll, 240))
        b = np.asarray(taffy_panel.viewport(scroll, 240))
        assert np.array_equal(a, b), (
            f"taffy≠default at width={width} n_defs={n_defs} scroll={scroll}"
        )


# --- 2-D flex-tree contract (Phase A2) --------------------------------------------------------------
# taffylite.Tree — the flexbox surface Phase B builds richer tooltip layouts on — is unused by the 1-D
# column seam, so nothing in overlay's gate exercised it. These promote the vendored Chrome-derived flex
# corpus (taffylite/tests/fixtures) into overlay's own contract and add an INDEPENDENT reference solver
# (tests/flex_reference.py) as a differential oracle: proven faithful against the browser rects here (in
# pure Python, no wheel needed), then trusted to check the real engine on Hypothesis-generated trees.

_FLEX_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "taffylite" / "tests" / "fixtures"
_FLEX_CASES = [
    case
    for name in ("flex_cases.json", "taffy_gentest_flex.json")
    for case in json.loads((_FLEX_FIXTURE_DIR / name).read_text(encoding="utf-8"))["cases"]
]


def _flex_rects(case: dict) -> list[tuple[int, ...]]:
    return [tuple(r) for r in case["rects"]]


def _reference_rects(nodes: list[dict], root: int, n: int) -> list[tuple[int, ...]]:
    ref = flex_reference_solve(nodes, root)
    return [tuple(ref[i]) for i in range(n)]


def _build_flex_tree(nodes: list[dict]):
    import taffylite  # noqa: TID251  # test-only: the flex-tree parity oracle needs the real engine

    tree = taffylite.Tree()
    handles: list[int] = []
    for node in nodes:
        if "leaf" in node:
            spec = node["leaf"]
            margin = tuple(spec[2]) if len(spec) > 2 else (0.0, 0.0, 0.0, 0.0)
            handles.append(tree.add_leaf(spec[0], spec[1], margin))
        else:
            f = node["flex"]
            handles.append(
                tree.add_flex(
                    [handles[i] for i in f["children"]],
                    direction=f.get("direction", "column"),
                    gap=f.get("gap", 0.0),
                    padding=tuple(f.get("padding", (0.0, 0.0, 0.0, 0.0))),
                    margin=tuple(f.get("margin", (0.0, 0.0, 0.0, 0.0))),
                    width=f.get("width"),
                    height=f.get("height"),
                    wrap=f.get("wrap", False),
                )
            )
    return tree, handles


def _taffy_rects(nodes: list[dict], root: int) -> list[tuple[int, ...]]:
    tree, handles = _build_flex_tree(nodes)
    tree.set_root(handles[root])
    return [tuple(round(v) for v in r) for r in tree.compute()]


def test_flex_reference_matches_vendored_chrome_rects():
    # The reference solver reproduces taffy's own Chrome-derived expected rects on every fixture in its
    # no-shrink domain — this is what earns it the right to be an oracle for random trees below. Pure
    # Python: runs in the default `poe` env, no taffylite wheel needed. The 2 flex-shrink cases are out
    # of domain (taffy's proportional shrink is deliberately unmodelled) and covered by the recorded-rect
    # oracle; pinning the split flags a re-vendor that changes the shrink census.
    matched = skipped = 0
    for case in _FLEX_CASES:
        try:
            got = _reference_rects(case["nodes"], case["root"], len(case["rects"]))
        except OverflowUnsupported:
            skipped += 1
            continue
        assert got == _flex_rects(case), case["name"]
        matched += 1
    assert matched >= 12, f"reference matched only {matched} in-domain fixtures"
    assert skipped == 2, f"expected 2 flex-shrink (out-of-domain) fixtures, saw {skipped}"


def test_flex_reference_faithfulness_is_non_vacuous():
    # Negative control for the equality above: a reference that shifts every node down 1px must NOT
    # reproduce the vendored rects — proving that `==` can fail, so a real reference bug can't slip past.
    case = next(c for c in _FLEX_CASES if c["name"] == "row-gap")
    ref = flex_reference_solve(case["nodes"], case["root"])
    shifted = [(x, y + 1, w, h) for x, y, w, h in (ref[i] for i in range(len(case["rects"])))]
    assert shifted != _flex_rects(case)


@st.composite
def _flat_container(draw):
    """A single flex container of fixed-size leaves, kept inside the reference's no-shrink domain by
    construction (a definite main size is always ≥ what its children need): the chip-row / column-stack
    geometry Phase B leans on, spanning direction × gap × padding × per-child margin × wrap × fixed/auto
    box size. Integers throughout, and a wrapping container keeps an auto cross size, so every coordinate
    is exact (no fractional align-content distribution) and the taffy↔reference `==` is byte-exact."""
    n = draw(st.integers(1, 4))
    leaves = [
        (
            draw(st.integers(1, 50)),
            draw(st.integers(1, 50)),
            tuple(draw(st.integers(0, 6)) for _ in range(4)),
        )
        for _ in range(n)
    ]
    row = draw(st.booleans())
    gap = draw(st.integers(0, 10))
    padding = tuple(draw(st.integers(0, 8)) for _ in range(4))
    wrap = draw(st.booleans())

    def main_cross(e):
        left, top, right, bottom = e
        return (left, right, top, bottom) if row else (top, bottom, left, right)

    outer_main = []
    outer_cross = []
    for w, h, mg in leaves:
        ml, mt, cl, ct = main_cross(mg)
        outer_main.append(ml + (w if row else h) + mt)
        outer_cross.append(cl + (h if row else w) + ct)
    pml, pmt, pcl, pct = main_cross(padding)
    content_main = sum(outer_main) + gap * (n - 1)
    # Definite main ≥ content (no wrap) or ≥ the widest child (wrap) → never a single-child overflow.
    lo = max(outer_main) if wrap else content_main
    def_main = (
        draw(st.integers(lo, content_main + 20)) + (pml + pmt) if draw(st.booleans()) else None
    )
    # A wrapping container hugs its cross (auto) to keep line placement integer-exact; a single-line one
    # may fix its cross (exercising align-content stretch of the one line — still integer).
    def_cross = (
        draw(st.integers(0, max(outer_cross) + 20)) + (pcl + pct)
        if not wrap and draw(st.booleans())
        else None
    )
    flex: dict = {
        "children": list(range(n)),
        "direction": "row" if row else "column",
        "gap": gap,
        "padding": list(padding),
        "wrap": wrap,
    }
    width, height = (def_main, def_cross) if row else (def_cross, def_main)
    if width is not None:
        flex["width"] = width
    if height is not None:
        flex["height"] = height
    nodes = [{"leaf": [w, h, list(mg)]} for w, h, mg in leaves]
    nodes.append({"flex": flex})
    return nodes, n


@requires_taffy
@given(spec=_flat_container())
@settings(max_examples=400, deadline=None)
def test_taffy_tree_matches_reference_on_flat_containers(spec):
    # The differential the fixtures can't give: the real Rust flexbox and the independent Python
    # reference must place every node identically across the generated in-domain space.
    nodes, root = spec
    assert _taffy_rects(nodes, root) == _reference_rects(nodes, root, len(nodes))


@requires_taffy
def test_taffy_tree_agrees_with_reference_on_vendored_corpus():
    # Tri-oracle in overlay's own contract: the engine Phase B depends on == the independent reference ==
    # (transitively, via the test above) the browser rects, on every in-domain vendored/authored case.
    checked = 0
    for case in _FLEX_CASES:
        try:
            ref = _reference_rects(case["nodes"], case["root"], len(case["rects"]))
        except OverflowUnsupported:
            continue
        assert _taffy_rects(case["nodes"], case["root"]) == ref, case["name"]
        checked += 1
    assert checked >= 12


# --- solve_row: 2-D flex row-wrap (Phase B1) --------------------------------------------------------
# The primitive chip rows (and later multi-column defs) lay out through — a wrap the 1-D prefix sum
# can't express. Default is pure Python (ships without the wheel); taffy is the parity-checked drop-in.


@st.composite
def _row_case(draw):
    # Each box width ≤ max_width, so no box overflows its line — the no-shrink domain the pure-Python
    # reference and taffy agree on exactly (a chip is always narrower than the tooltip).
    max_width = draw(st.integers(8, 320))
    gap = draw(st.integers(0, 16))
    n = draw(st.integers(0, 8))
    sizes = [(draw(st.integers(1, max_width)), draw(st.integers(1, 60))) for _ in range(n)]
    return sizes, gap, max_width


@given(case=_row_case())
@settings(max_examples=400, deadline=None)
def test_solve_row_default_and_flex_agree(case):
    sizes, gap, mw = case
    d = DefaultLayoutBackend().solve_row(sizes, gap=gap, max_width=mw)
    f = FlexColumnBackend().solve_row(sizes, gap=gap, max_width=mw)
    assert d == f  # both are the shared pure-Python reference


@requires_taffy
@given(case=_row_case())
@example(case=([], 4, 100))  # empty row
@example(case=([(30, 20)], 6, 30))  # single box exactly max_width (one line, no shrink)
@example(case=([(30, 10), (30, 12), (30, 14)], 0, 60))  # zero gap, exact-fit wrap boundary
@settings(max_examples=400, deadline=None)
def test_solve_row_taffy_matches_the_reference(case):
    # The differential: the Rust flex engine reproduces the pure-Python row-wrap byte-for-byte (rects +
    # used width + height), so taffy is a true drop-in for the 2-D primitive too.
    sizes, gap, mw = case
    assert TaffyLayoutBackend().solve_row(
        sizes, gap=gap, max_width=mw
    ) == DefaultLayoutBackend().solve_row(sizes, gap=gap, max_width=mw)


def test_solve_row_wraps_with_uniform_gap_and_no_overlap():
    # The behavioural oracle (platform-independent, no pixels): boxes pack left-to-right with a UNIFORM
    # gap, wrap to a fresh line when the next won't fit, and never overlap — the chip-row invariant.
    sizes = [(60, 30), (70, 30), (50, 30), (90, 30)]
    rects, used, height = DefaultLayoutBackend().solve_row(sizes, gap=8, max_width=200)
    assert [r.y for r in rects] == [0, 0, 0, 38]  # first three share line 0; the fourth wraps
    assert rects[1].x - (rects[0].x + rects[0].w) == 8  # uniform gap between same-line boxes
    assert rects[2].x - (rects[1].x + rects[1].w) == 8
    assert rects[3].x == 0  # a wrapped line restarts at the left
    assert used == 196 and height == 68  # widest line + stacked line heights with the cross gap
    for a, b in itertools.pairwise(rects):  # no two boxes overlap on a line
        if a.y == b.y:
            assert a.x + a.w <= b.x


def test_solve_row_gap_is_not_vacuous():
    # Negative control: the gap actually moves boxes — a different gap yields a different layout, so the
    # exact-equality parity checks above can't pass on a solver that ignored the gap.
    sizes = [(30, 10), (30, 10)]
    tight = DefaultLayoutBackend().solve_row(sizes, gap=0, max_width=200)
    loose = DefaultLayoutBackend().solve_row(sizes, gap=20, max_width=200)
    assert tight != loose
