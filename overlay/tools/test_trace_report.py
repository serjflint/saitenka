"""Tests for the trace-report distiller. Run explicitly (tools/ is outside `poe all`):
    uv run python -m pytest tools/test_trace_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import trace_report as tr


def _span(name: str, ts: float, dur: float = 1000.0, **args) -> dict:
    return {"name": name, "ph": "X", "ts": ts, "dur": dur, "tid": 1, "args": args}


def test_first_paints_picks_earliest_per_kind():
    # A base cold show, then two more tip_composes: a nested and a clicked. first_paints must pick the
    # EARLIEST of each kind by start time, not by document order.
    events = [
        _span("tip_compose", ts=5000, kind="clicked"),  # earlier clicked, out of order
        _span("subtitle_render", ts=1000),
        _span("tooltip_show", ts=2000, cold="True"),
        _span("tip_compose", ts=3000, kind="nested"),
        _span("tip_compose", ts=9000, kind="nested"),  # later nested, must be ignored
    ]
    rows = {label: sp for label, sp, _note in tr.first_paints(events)}
    assert rows["first subtitle cue paint"]["ts"] == 1000
    assert rows["first tooltip paint (base)"]["ts"] == 2000
    assert rows["first nested tooltip paint"]["ts"] == 3000  # earliest nested, not 9000
    assert rows["first clicked tooltip paint"]["ts"] == 5000


def test_first_paints_falls_back_to_cue_redraw_for_subtitle():
    events = [_span("cue_redraw", ts=1200), _span("tooltip_show", ts=2000, cold="False")]
    rows = {label: sp for label, sp, _note in tr.first_paints(events)}
    assert rows["first subtitle cue paint"]["ts"] == 1200  # no subtitle_render → cue_redraw stands in


def test_first_paints_notes_missing_kind_marker():
    # A bundle predating the `kind` attribute: nested/clicked are unknowable, and the note must say so
    # rather than reporting a false "never happened".
    events = [_span("tip_compose", ts=3000, soft_reason="")]  # no kind
    rows = {label: (sp, note) for label, sp, note in tr.first_paints(events)}
    sp, note = rows["first nested tooltip paint"]
    assert sp is None and "predates the marker" in note


def test_first_paints_reports_kinded_bundle_without_note():
    events = [_span("tip_compose", ts=3000, kind="base")]  # kind present → no caveat note
    rows = {label: note for label, _sp, note in tr.first_paints(events)}
    assert rows["first nested tooltip paint"] == ""


def test_first_ignores_counter_samples():
    # ph=="C" counter rows share names with spans in real bundles; _first must only see ph=="X".
    events = [
        {"name": "tip_compose", "ph": "C", "ts": 500, "args": {"value": 1}},
        _span("tip_compose", ts=4000, kind="nested"),
    ]
    got = tr._first([e for e in events if e.get("ph") == "X"], "tip_compose")
    assert got["ts"] == 4000
