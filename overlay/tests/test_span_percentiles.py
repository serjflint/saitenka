"""The per-span latency reporter (tools/span_percentiles.py) — the kind-split grouping (PR B).

A paint span (``render``/``tip_compose``/``prefetch_decode``) covers visibly-distinct interactions —
base hover vs nested scan popup vs clicked cross-ref — so its latency must be reported per ``kind``, not
folded into one base aggregate that hides a nested/clicked tail. Stdlib reporter, loaded by path like
tests/test_corpus_check.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SP = Path(__file__).resolve().parent.parent / "tools" / "span_percentiles.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_span_percentiles", _SP)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _span(name: str, dur_us: int, kind: str | None = None) -> dict:
    ev: dict = {"name": name, "ph": "X", "dur": dur_us}
    if kind is not None:
        ev["args"] = {"kind": kind}
    return ev


def test_kind_split_span_groups_per_kind() -> None:
    # render carries kind=base|nested → two separate rows; a plain span (no kind) stays one row.
    sp = _mod()
    events = [
        _span("render", 1000, "base"),
        _span("render", 3000, "nested"),
        _span("render", 5000, "nested"),
        _span("tooltip_show", 2000),
    ]
    by = sp._durations_by_span(events)
    assert set(by) == {"render[base]", "render[nested]", "tooltip_show"}
    assert by["render[nested]"] == [3.0, 5.0]  # µs → ms, both nested durations
    assert by["render[base]"] == [1.0]


def test_non_split_span_ignores_kind() -> None:
    # A span NOT in the kind-split set aggregates by name even if it happens to carry a kind attr.
    sp = _mod()
    by = sp._durations_by_span([_span("hit_test", 500, "whatever"), _span("hit_test", 700)])
    assert set(by) == {"hit_test"}
    assert by["hit_test"] == [0.5, 0.7]


def test_base_strips_kind_suffix_for_ordering() -> None:
    sp = _mod()
    assert sp._base("tip_compose[nested]") == "tip_compose"
    assert sp._base("tooltip_show") == "tooltip_show"


def test_critical_kind_rows_sort_ahead_of_noncritical() -> None:
    # A kind-split critical span (tip_compose[nested]) still sorts by its base name's critical rank,
    # ahead of a non-critical span — the ordering keys off the base, not the decorated label.
    sp = _mod()
    rows = sp._rows(
        sp._durations_by_span(
            [_span("some_other_span", 9000), _span("tip_compose", 1000, "nested")]
        )
    )
    assert rows[0][0] == "tip_compose[nested]"  # critical → first despite its shorter total time
