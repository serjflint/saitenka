"""Tests run by `poe loop-tools-test`, or explicitly:
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
        {
            "outcome": "synced",
            "tool": "uvx ffsubsync",
            "reference": "audio",
            "shift_ms": 0,
            "src_cue_ms": [1000],
            "out_cue_ms": [1000],
        }
    )
    assert "NO-OP" in line and "output == input" in line


def test_diagnose_resync_reports_the_applied_shift_without_cross_track_first_cue_claim():
    # A real shift is reported by magnitude only — NOT by comparing the JP first cue to the EN first
    # cue (they caption different opening events, so cue-1↔cue-1 is a false "off by N" signal).
    line = sr.diagnose_resync(
        {
            "outcome": "synced",
            "tool": "alass",
            "reference": "embedded",
            "shift_ms": -6106,
            "src_cue_ms": [6106],
            "out_cue_ms": [0],
            "ref_cue_ms": [41410],
        }
    )
    assert "shifted -6106ms" in line
    assert "41410" not in line and "off the reference" not in line  # no bogus cross-track delta
    assert "under-split" in line  # names the wrong-before/right-after-OP failure mode


def test_diagnose_resync_names_the_missing_aligner():
    assert "NO ALIGNER" in sr.diagnose_resync({"outcome": "unavailable"})


def test_diagnose_resync_surfaces_the_failure_reason_and_reference_format():
    line = sr.diagnose_resync(
        {
            "outcome": "failed",
            "tool": "alass-cli",
            "reference": "embedded",
            "reference_fmt": ".ass",
            "fail_reason": "resync failed (exit 1): parse error at line 1164",
        }
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
        _resync(
            2,
            outcome="synced",
            src_cue_ms=[1000],
            out_cue_ms=[8000],
            ref_cue_ms=[8000],
            span_id="abc",
            trace_id="def",
        ),
    ]
    out = sr.extract(events)
    assert out["fetches"][0]["args"]["episode"] == 3
    r = out["resyncs"][0]["args"]
    assert r["src_cue_ms"] == [1000] and r["out_cue_ms"] == [8000]
    assert (
        "span_id" not in r and "trace_id" not in r
    )  # ids stripped — only the timing vectors remain


def test_extract_includes_bounded_native_geometry_evidence():
    events = [
        {
            "name": "subtitle_geometry_decision",
            "ph": "X",
            "ts": 1,
            "args": {
                "outcome": "failed",
                "reason": "geometry-provider-failed",
                "error_code": "missing-token-colors",
                "eligible_tokens": 4,
                "subtitle_text": "must not be emitted",
                "span_id": "abc",
            },
        },
        {
            "name": "subtitle_geometry_clock",
            "ph": "X",
            "ts": 2,
            "args": {
                "outcome": "ready",
                "video_time_ms": 11_250,
                "sub_delay_ms": 10_000,
                "subtitle_time_ms": 1_250,
            },
        },
        {
            "name": "subtitle_geometry_libass",
            "ph": "X",
            "ts": 3,
            "args": {"timestamp_ms": 1_250, "subtitle_text": "must not be emitted"},
        },
        {
            "name": "subtitle_pixel_ownership",
            "ph": "X",
            "ts": 4,
            "args": {
                "event": "legacy-stage-result",
                "owner_before": "unknown",
                "owner_after": "legacy",
                "accepted": True,
                "selection_id": "must not be emitted",
            },
        },
    ]
    geometry = sr.extract(events)["geometry"]
    decision = geometry[0]["args"]
    assert decision["error_code"] == "missing-token-colors"
    assert decision["eligible_tokens"] == 4
    assert "span_id" not in decision
    assert geometry[1]["args"] == {
        "outcome": "ready",
        "video_time_ms": 11_250,
        "sub_delay_ms": 10_000,
        "subtitle_time_ms": 1_250,
    }
    assert geometry[2]["args"] == {"timestamp_ms": 1_250}
    assert geometry[3]["args"] == {
        "event": "legacy-stage-result",
        "owner_before": "unknown",
        "owner_after": "legacy",
        "accepted": True,
    }


def test_ownership_diagnosis_explains_catastrophic_handoff() -> None:
    line = sr._geometry_diagnosis(
        {
            "name": "subtitle_pixel_ownership",
            "args": {
                "event": "legacy-stage-result",
                "owner_before": "unknown",
                "owner_after": "legacy",
                "visibility": "false",
                "retry_attempts": 0,
                "accepted": True,
            },
        }
    )

    assert line == (
        "legacy-stage-result: unknown -> legacy visibility=false retries=0 accepted=True"
    )


def test_geometry_diagnosis_explains_owner_transition_and_skips():
    line = sr._geometry_diagnosis(
        {
            "name": "subtitle_geometry_decision",
            "args": {
                "outcome": "ready",
                "reason": "ready",
                "active_events": 2,
                "eligible_tokens": 7,
                "skipped_whitespace": 1,
                "skipped_tokenizer": 2,
                "skipped_unpaintable": 1,
                "owner_transition": "legacy_to_native",
            },
        }
    )
    assert line == "ready: ready (events=2 eligible=7 skipped=4) legacy_to_native"


def test_geometry_diagnosis_reports_prefetch_cache_outcome():
    line = sr._geometry_diagnosis(
        {
            "name": "subtitle_geometry_cache",
            "args": {
                "outcome": "miss",
                "cache_hits": 3,
                "prefetch_cache_entries": 0,
                "prefetch_dropped": 2,
            },
        }
    )

    assert line == "miss: hits=3 ready=0 dropped=2"


def test_geometry_diagnosis_attributes_libass_render_and_extraction_cost():
    line = sr._geometry_diagnosis(
        {
            "name": "subtitle_geometry_libass",
            "args": {
                "provider": "libasslite",
                "libass_version": "0x1705000",
                "layer_count": 12,
                "found_tokens": 7,
                "timestamp_ms": 1_250,
                "render_ms": 2.5,
                "extract_ms": 0.8,
            },
        }
    )
    assert line == (
        "provider=libasslite libass=0x1705000 at=1250ms layers=12 tokens=7 "
        "render=2.5ms extract=0.8ms"
    )


def test_geometry_diagnosis_reports_delay_adjusted_subtitle_clock():
    line = sr._geometry_diagnosis(
        {
            "name": "subtitle_geometry_clock",
            "args": {
                "outcome": "ready",
                "video_time_ms": 11_250,
                "sub_delay_ms": 10_000,
                "subtitle_time_ms": 1_250,
            },
        }
    )

    assert line == "video=11250ms delay=10000ms subtitle=1250ms"


def test_geometry_report_uses_trace_microseconds_for_elapsed_seconds(capsys):
    events = [
        {"name": "subtitle_geometry_decision", "ph": "X", "ts": 1_000_000, "args": {}},
        {"name": "subtitle_geometry_render", "ph": "X", "ts": 2_000_000, "args": {}},
    ]

    sr.print_geometry(Path("report.zip"), events)

    assert "t+    1.0s  render" in capsys.readouterr().out


def test_subtitle_spans_are_sorted_and_filtered():
    events = [
        _resync(3000),
        {"name": "render", "ph": "X", "ts": 1, "args": {}},  # non-subtitle span ignored
        {"name": "subtitle.fetch", "ph": "X", "ts": 1000, "args": {}},
        {"name": "subtitle.resync", "ph": "C", "ts": 500, "args": {"value": 1}},  # counter ignored
    ]
    got = sr.subtitle_spans(events)
    assert [s["name"] for s in got] == [
        "subtitle.fetch",
        "subtitle.resync",
    ]  # ts-sorted, ph==X only


def test_find_cached_sub_matches_the_raw_sub_by_video_stem(tmp_path):
    # The cache names files after the video stem; reproduce must pick the RAW match, never the already
    # -synced output (we re-align from scratch) and never a different episode.
    video = tmp_path / "[Grp] Show - 02 [1080p].mkv"
    stem = video.stem
    (tmp_path / f"{stem}-Show-ep2-111.srt").write_text("x", encoding="utf-8")
    (tmp_path / f"{stem}-Show-ep2-111.synced.srt").write_text("x", encoding="utf-8")  # skip synced
    (tmp_path / "[Grp] Show - 03 [1080p]-Show-ep3-222.srt").write_text(
        "x", encoding="utf-8"
    )  # other ep
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
