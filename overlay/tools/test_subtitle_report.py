"""Tests for the subtitle-report distiller. Run explicitly (tools/ is outside `poe all`):
    uv run python -m pytest tools/test_subtitle_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import subtitle_report as sr


def _resync(ts: float, **args) -> dict:
    return {"name": "subtitle.resync", "ph": "X", "ts": ts, "dur": 1000.0, "tid": 1, "args": args}


def test_diagnose_resync_flags_a_silent_no_op():
    # out == src while a shift was expected → the class that shipped mistimed subs as "synced".
    line = sr.diagnose_resync(
        {"outcome": "synced", "tool": "uvx ffsubsync", "reference": "audio",
         "shift_ms": 0, "src_cue_ms": [1000], "out_cue_ms": [1000]}
    )
    assert "NO-OP" in line and "output == input" in line


def test_diagnose_resync_reports_the_applied_shift_without_cross_track_first_cue_claim():
    # A real shift is reported by magnitude only — NOT by comparing the JP first cue to the EN first
    # cue (they caption different opening events, so cue-1↔cue-1 is a false "off by N" signal).
    line = sr.diagnose_resync(
        {"outcome": "synced", "tool": "alass", "reference": "embedded",
         "shift_ms": -6106, "src_cue_ms": [6106], "out_cue_ms": [0], "ref_cue_ms": [41410]}
    )
    assert "shifted -6106ms" in line
    assert "41410" not in line and "off the reference" not in line  # no bogus cross-track delta
    assert "under-split" in line  # names the wrong-before/right-after-OP failure mode


def test_diagnose_resync_names_the_missing_aligner():
    assert "NO ALIGNER" in sr.diagnose_resync({"outcome": "unavailable"})


def test_diagnose_resync_surfaces_the_failure_reason_and_reference_format():
    line = sr.diagnose_resync(
        {"outcome": "failed", "tool": "alass-cli", "reference": "embedded",
         "reference_fmt": ".ass", "fail_reason": "resync failed (exit 1): parse error at line 1164"}
    )
    assert "ALIGNER FAILED (alass-cli vs embedded.ass)" in line
    assert "parse error at line 1164" in line


def test_retry_deltas_expose_oscillation():
    # Three retries whose first cue swings −5203 then +833 → re-syncing its own output.
    resyncs = [
        _resync(1000, outcome="synced", out_cue_ms=[8000]),
        _resync(2000, outcome="synced", out_cue_ms=[2797]),  # −5203
        _resync(3000, outcome="synced", out_cue_ms=[3630]),  # +833
    ]
    deltas = sr.retry_deltas(resyncs)
    assert deltas == [None, -5203, 833]  # first has no predecessor


def test_extract_is_text_free_and_carries_fingerprints():
    events = [
        {"name": "subtitle.fetch", "ph": "X", "ts": 1, "args": {"picked": "x.srt", "episode": 3}},
        _resync(2, outcome="synced", src_cue_ms=[1000], out_cue_ms=[8000], ref_cue_ms=[8000],
                span_id="abc", trace_id="def"),
    ]
    out = sr.extract(events)
    assert out["fetches"][0]["args"]["episode"] == 3
    r = out["resyncs"][0]["args"]
    assert r["src_cue_ms"] == [1000] and r["out_cue_ms"] == [8000]
    assert "span_id" not in r and "trace_id" not in r  # ids stripped — only the timing vectors remain


def test_subtitle_spans_are_sorted_and_filtered():
    events = [
        _resync(3000),
        {"name": "render", "ph": "X", "ts": 1, "args": {}},  # non-subtitle span ignored
        {"name": "subtitle.fetch", "ph": "X", "ts": 1000, "args": {}},
        {"name": "subtitle.resync", "ph": "C", "ts": 500, "args": {"value": 1}},  # counter ignored
    ]
    got = sr.subtitle_spans(events)
    assert [s["name"] for s in got] == ["subtitle.fetch", "subtitle.resync"]  # ts-sorted, ph==X only


def test_find_cached_sub_matches_the_raw_sub_by_video_stem(tmp_path):
    # The cache names files after the video stem; reproduce must pick the RAW match, never the already
    # -synced output (we re-align from scratch) and never a different episode.
    video = tmp_path / "[Grp] Show - 02 [1080p].mkv"
    stem = video.stem
    (tmp_path / f"{stem}-Show-ep2-111.srt").write_text("x", encoding="utf-8")
    (tmp_path / f"{stem}-Show-ep2-111.synced.srt").write_text("x", encoding="utf-8")  # skip synced
    (tmp_path / "[Grp] Show - 03 [1080p]-Show-ep3-222.srt").write_text("x", encoding="utf-8")  # other ep
    got = sr.find_cached_sub(video, tmp_path)
    assert got is not None and got.name == f"{stem}-Show-ep2-111.srt"


def test_find_cached_sub_returns_none_when_no_match(tmp_path):
    (tmp_path / "unrelated.srt").write_text("x", encoding="utf-8")
    assert sr.find_cached_sub(tmp_path / "Show - 05.mkv", tmp_path) is None


def test_notable_log_selects_subtitle_lines():
    log = [
        {"timestamp": "2026-08-07T12:00:01", "level": "info", "event": "jimaku: picked foo.srt"},
        {"timestamp": "2026-08-07T12:00:02", "level": "info", "event": "hover at 3,4"},  # dropped
        {"timestamp": "2026-08-07T12:00:03", "level": "info", "event": "resync: running alass"},
    ]
    got = sr.notable_log(log)
    assert [e for _ts, _lvl, e in got] == ["jimaku: picked foo.srt", "resync: running alass"]
