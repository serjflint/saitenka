"""LayoutBackend seam (#113): the default arithmetic and the independent flex-column solver agree, both
satisfy the vendored column-layout fixtures, and a real panel renders pixel-identically under either."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from overlay.panel import panel_rows, render_panel
from overlay.render.banded import WindowedPanel
from overlay.render.layout_backend import (
    DEFAULT_BACKEND,
    DefaultLayoutBackend,
    FlexColumnBackend,
    LayoutBackend,
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
    from overlay.panel import Definition, Entry

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
