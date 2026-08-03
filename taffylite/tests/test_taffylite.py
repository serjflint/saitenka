"""taffylite's own test suite — run in the CI free-threaded-wheel job (not by overlay's `poe`).

Two contracts:
1. ``column`` reproduces the reference row-stack cumulative arithmetic exactly (fixtures + random),
   which is what makes ``TaffyLayoutBackend`` a byte-identical drop-in behind the seam.
2. The generic ``Tree`` reproduces taffy's flexbox layout on the vendored fixed-size fixtures — proof
   that the thin wrapper does not distort the engine it binds.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

import taffylite

_FIXTURES = Path(__file__).parent / "fixtures"


def _reference_cumulative(
    heights: list[int], gaps: list[int], top_pad: int
) -> tuple[list[int], list[int]]:
    """The canonical row-stack offsets (mirrors overlay ``window._cumulative``): the gap after the
    last row is never added."""
    n = len(heights)
    starts: list[int] = []
    ends: list[int] = []
    y = top_pad
    for i, h in enumerate(heights):
        starts.append(y)
        ends.append(y + h)
        y += h + (gaps[i] if i < n - 1 else 0)
    return starts, ends


def _column(heights: list[int], gaps: list[int], top_pad: int) -> tuple[list[int], list[int]]:
    s, e = taffylite.column(
        [float(h) for h in heights], [float(g) for g in gaps], float(top_pad)
    )
    return list(s), list(e)


def test_column_matches_reference_on_edge_cases():
    for heights, gaps, top_pad in [
        ([], [], 12),
        ([100], [7], 12),
        ([100, 80], [7, 7], 12),
        ([40, 60, 20], [5, 10, 99], 8),
        ([30, 30, 30], [0, 0, 0], 0),
        ([50, 0, 50], [4, 4, 4], 10),
        ([90000], [8], 16),
    ]:
        assert _column(heights, gaps, top_pad) == _reference_cumulative(heights, gaps, top_pad)


def test_column_matches_reference_on_random_inputs():
    rng = random.Random(20260803)
    for _ in range(5000):
        n = rng.randint(0, 40)
        heights = [rng.randint(0, 90_000) for _ in range(n)]
        gaps = [rng.randint(0, 200) for _ in range(n)]
        top_pad = rng.randint(0, 64)
        assert _column(heights, gaps, top_pad) == _reference_cumulative(heights, gaps, top_pad)


def _build(nodes: list[dict]) -> tuple[taffylite.Tree, list[int]]:
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


@pytest.mark.parametrize(
    "case",
    json.loads((_FIXTURES / "flex_cases.json").read_text(encoding="utf-8"))["cases"],
    ids=lambda c: c["name"],
)
def test_generic_tree_matches_vendored_flex_fixtures(case):
    tree, handles = _build(case["nodes"])
    tree.set_root(handles[case["root"]])
    rects = [tuple(round(v) for v in r) for r in tree.compute()]
    assert rects == [tuple(r) for r in case["rects"]], case["name"]
