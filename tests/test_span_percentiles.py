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


def test_engaged_open_reports_as_its_own_prefetch_decode_kind() -> None:
    # Phase C/D: the off-thread clicked/keyed open warms bands under prefetch_decode[engaged_open], so it
    # must report separately from the base scroll-ahead warm (prefetch_decode[warm]) — else the win hides.
    sp = _mod()
    by = sp._durations_by_span(
        [_span("prefetch_decode", 4000, "engaged_open"), _span("prefetch_decode", 2000, "warm")]
    )
    assert by["prefetch_decode[engaged_open]"] == [4.0]
    assert by["prefetch_decode[warm]"] == [2.0]


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


def test_mark_interactive_includes_cold_miss_build_spans() -> None:
    # `*` = reaches the main thread. render/measure/dict_sql count: a warm hover skips them, but a cold
    # miss builds them inline in show_tooltip_impl (the tracked cold-first-paint) — the weak-HW overshoot.
    sp = _mod()
    assert sp._mark("tooltip_show") == "*"
    assert sp._mark("tip_compose[base]") == "*"  # kind-split base
    assert sp._mark("render[base]") == "*"  # inline on a cold miss
    assert sp._mark("measure") == "*"
    assert sp._mark("dict_sql") == "*"
    assert sp._mark("some_other_span") == " "  # unlisted


def test_mark_background_prefetch_warm_but_engaged_is_interactive() -> None:
    # prefetch_decode head/warm/head_ahead is the ONLY off-thread span (`~`, never blocks). The engaged_*
    # kinds are the exception: the user hovered a missed word and WAITS for that compose → `*`.
    sp = _mod()
    assert sp._mark("prefetch_decode[head]") == "~"
    assert sp._mark("prefetch_decode[warm]") == "~"
    assert sp._mark("prefetch_decode[head_ahead]") == "~"
    assert sp._mark("prefetch_decode[engaged_open]") == "*"
    assert sp._mark("prefetch_decode[engaged_nav]") == "*"
