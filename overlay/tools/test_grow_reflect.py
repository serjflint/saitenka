"""Tests run by `poe loop-tools-test`, or explicitly:
uv run python -m pytest tools/test_grow_reflect.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import grow_reflect as gr

MANIFEST = {"type": "manifest", "loop_version": 1}


def _ledger(tmp_path: Path, records: list[dict]) -> gr.ReflectionLedger:
    p = tmp_path / ".reflection.grow.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return gr.ReflectionLedger.load(p)


def _finding(
    subject: str, *, loop_version: int = 1, category: str = "arm-limitation", **extra
) -> dict:
    return {
        "finding_id": gr.finding_id(category, subject),
        "category": category,
        "subject": subject,
        "severity": "medium",
        "loop_version": loop_version,
        **extra,
    }


def test_finding_id_is_semantic_and_deterministic():
    a = gr.finding_id("arm-limitation", "arm-3 misses branch-of-covered-line gaps")
    b = gr.finding_id("arm-limitation", "arm-3 misses branch-of-covered-line gaps")
    c = gr.finding_id("triage-signal", "arm-3 misses branch-of-covered-line gaps")
    assert a == b
    assert a != c  # category is part of identity


def test_recurrence_counts_repeats_at_the_current_loop_version(tmp_path):
    subj = "arm-3 is line-level; misses branch-of-covered-line"
    led = _ledger(tmp_path, [MANIFEST, _finding(subj), _finding(subj), _finding(subj)])
    assert led.recurrence(gr.finding_id("arm-limitation", subj)) == 3


def test_a_loop_version_bump_resets_recurrence(tmp_path):
    # the same finding filed 3× at v1, then the human lands a fix and bumps to v2 → the v1 accumulation no
    # longer counts; the finding is considered addressed until it recurs at v2.
    subj = "triage under-spec rides on the seam proxy"
    recs = [
        {"type": "manifest", "loop_version": 2},
        *[_finding(subj, loop_version=1) for _ in range(3)],
    ]
    led = _ledger(tmp_path, recs)
    assert led.recurrence(gr.finding_id("arm-limitation", subj)) == 0


def test_escalated_returns_findings_at_or_over_threshold(tmp_path):
    hot = "gate ANDs arm-1 and arm-3 (should be OR)"
    warm = "context CLI cannot pass --deselect"
    recs = [MANIFEST, _finding(hot), _finding(hot), _finding(warm)]  # hot ×2, warm ×1
    led = _ledger(tmp_path, recs)
    esc = led.escalated(threshold=2)
    subjects = {r["subject"] for r in esc}
    assert hot in subjects
    assert warm not in subjects  # below threshold — not escalated


def test_escalated_returns_the_latest_record_per_finding(tmp_path):
    subj = "arm-1 n/a for most triage-selected modules"
    recs = [
        MANIFEST,
        _finding(subj, proposal="expand cosmic-ray TARGETS"),
        _finding(subj, proposal="ad-hoc text mutant (growth-adhoc)"),  # the refined proposal
    ]
    led = _ledger(tmp_path, recs)
    (rec,) = led.escalated(threshold=2)
    assert rec["proposal"] == "ad-hoc text mutant (growth-adhoc)"


def test_append_round_trips(tmp_path):
    led = _ledger(tmp_path, [MANIFEST])
    rec = _finding("a fresh finding")
    led.append(rec)
    reloaded = gr.ReflectionLedger.load(tmp_path / ".reflection.grow.jsonl")
    assert reloaded.recurrence(rec["finding_id"]) == 1
