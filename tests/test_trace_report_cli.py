from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

from saitenka.app.commands.diagnostics import trace_report
from saitenka.app.subtitle_report import load_trace
from saitenka.app.trace_report import latency_summary, startup_json, startup_records

if TYPE_CHECKING:
    from pathlib import Path


def _trace(path: Path, *, slow: bool) -> Path:
    duration = 8_300_000 if slow else 8_000
    path.write_text(
        json.dumps(
            {
                "traceEvents": [
                    {
                        "name": "cue_annotation",
                        "ph": "X",
                        "ts": 10,
                        "dur": duration,
                        "args": {
                            "priority": "current",
                            "queue_wait_ms": 2.0,
                            "token_count": 3,
                            "subtitle_text": "must not be emitted",
                        },
                    },
                    {
                        "name": "dictionary_attestation",
                        "ph": "X",
                        "ts": 20,
                        "dur": duration - 1_000,
                        "args": {"requested_forms": 4, "hit_count": 1},
                    },
                    {
                        "name": "startup.interactive_ready",
                        "ph": "X",
                        "ts": 30,
                        "dur": 100,
                        "args": {"since_ipc_ms": 25.0, "deps_pending": True},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_installed_trace_report_attributes_the_slow_annotation(tmp_path: Path, capsys) -> None:
    trace = _trace(tmp_path / "slow.json", slow=True)

    assert trace_report(str(trace)) == 0

    output = capsys.readouterr().out
    assert "interactive readiness: 25.0 ms" in output
    assert "cue_annotation" in output and "dictionary_attestation" in output
    assert "must not be emitted" not in output


def test_startup_records_keep_the_fast_control_text_free(tmp_path: Path) -> None:
    records = startup_records(load_trace(_trace(tmp_path / "fast.json", slow=False)))

    assert records[0]["duration_ms"] == 8.0
    assert "subtitle_text" not in records[0]["args"]


def test_json_report_caps_records_and_reports_the_dropped_count() -> None:
    events = [
        {"name": "cue_annotation", "ph": "X", "ts": index, "dur": 1, "args": {}}
        for index in range(300)
    ]

    report = json.loads(startup_json(events))

    assert len(report["startup"]) == 256
    assert report["total"] == 300
    assert report["dropped"] == 44


def test_json_report_reserves_a_tail_for_late_interactions() -> None:
    events = [
        {"name": "cue_annotation", "ph": "X", "ts": index, "dur": 1, "args": {}}
        for index in range(300)
    ]
    events.append(
        {
            "name": "tooltip_request",
            "ph": "X",
            "ts": 301,
            "dur": 1,
            "args": {"outcome": "failed", "latency_ms": 9.0},
        }
    )

    report = json.loads(startup_json(events))

    assert len(report["startup"]) == 256
    assert report["startup"][-1]["name"] == "tooltip_request"
    assert report["interaction_latency"]["tooltip_request"]["outcomes"] == {"failed": 1}


def test_sampled_interactions_cannot_evict_terminal_outcomes() -> None:
    events = [
        {
            "name": "tooltip_request",
            "ph": "X",
            "ts": 1,
            "dur": 1,
            "args": {"outcome": "failed", "latency_ms": 9.0},
        }
    ] + [
        {"name": "hover_transition", "ph": "X", "ts": index + 2, "dur": 1, "args": {}}
        for index in range(100)
    ]

    report = json.loads(startup_json(events))

    assert report["interaction_latency"]["tooltip_request"]["outcomes"] == {"failed": 1}


def test_report_keeps_scroll_and_geometry_provenance() -> None:
    events = [
        {
            "name": "scroll_frame",
            "ph": "X",
            "ts": 1,
            "dur": 2_000,
            "args": {"bands": 2, "full_h": 500, "scale": "2.0", "crisp_miss": "cold"},
        },
        {
            "name": "subtitle_geometry_cache",
            "ph": "X",
            "ts": 2,
            "dur": 1_000,
            "args": {"outcome": "miss", "reason": "not-prefetched", "cache_hits": 4},
        },
    ] + [
        {"name": "hover_transition", "ph": "X", "ts": index + 3, "dur": 1, "args": {}}
        for index in range(100)
    ]

    report = json.loads(startup_json(events))

    assert report["startup"][0]["args"]["crisp_miss"] == "cold"
    geometry = report["interaction_latency"]["subtitle_geometry_cache"]
    assert geometry["outcomes"] == {"miss": 1}
    assert geometry["reasons"] == {"not-prefetched": 1}


def test_scroll_frames_cannot_evict_geometry_provenance() -> None:
    events = [
        {
            "name": "subtitle_geometry_cache",
            "ph": "X",
            "ts": 1,
            "dur": 1_000,
            "args": {"outcome": "miss", "reason": "not-prefetched"},
        }
    ] + [
        {"name": "scroll_frame", "ph": "X", "ts": index + 2, "dur": 1, "args": {}}
        for index in range(40)
    ]

    report = json.loads(startup_json(events))

    geometry = report["interaction_latency"]["subtitle_geometry_cache"]
    assert geometry["reasons"] == {"not-prefetched": 1}


def test_interaction_summary_attributes_tail_and_terminal_outcomes() -> None:
    records = [
        {"name": "hover_target_lookup", "duration_ms": value, "args": {}}
        for value in (0.1, 0.2, 4.0)
    ] + [
        {"name": "tooltip_request", "duration_ms": 12.0, "args": {"outcome": "painted"}},
        {"name": "tooltip_request", "duration_ms": 3.0, "args": {"outcome": "superseded"}},
    ]

    summary = latency_summary(records)

    assert summary["hover_target_lookup"]["max_ms"] == 4.0
    assert summary["tooltip_request"]["outcomes"] == {"painted": 1, "superseded": 1}


def test_installed_trace_report_drops_malformed_records_and_values(tmp_path: Path, capsys) -> None:
    trace = tmp_path / "malformed.json"
    trace.write_text(
        json.dumps(
            {
                "traceEvents": [
                    1,
                    {"name": "cue_annotation", "ph": "X", "dur": "bad"},
                    {
                        "name": "cue_annotation",
                        "ph": "X",
                        "ts": 2,
                        "dur": 1_000,
                        "args": {
                            "priority": "current",
                            "failure": {"secret": "subtitle"},
                            "outcome": "x" * 129,
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert trace_report(str(trace), json_out=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 1
    assert report["startup"][0]["args"] == {"priority": "current"}


def test_interaction_summary_ignores_non_numeric_latency() -> None:
    events = [
        {
            "name": "tooltip_request",
            "ph": "X",
            "ts": 1,
            "dur": 2_000,
            "args": {"outcome": "failed", "latency_ms": "invalid"},
        }
    ]

    report = json.loads(startup_json(events))

    assert report["interaction_latency"]["tooltip_request"]["max_ms"] == 2.0
    assert "latency_ms" not in report["startup"][0]["args"]


def test_startup_records_bound_numeric_arguments() -> None:
    events = [
        {
            "name": "cue_annotation",
            "ph": "X",
            "ts": 1,
            "dur": 1,
            "args": {"work_ms": 1e18, "queue_wait_ms": 10**1000},
        }
    ]

    assert startup_records(events)[0]["args"] == {"work_ms": 1e18}


def test_installed_trace_report_rejects_oversized_bare_json(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from saitenka.app import trace_report as report_module

    monkeypatch.setattr(report_module, "_MAX_TRACE_BYTES", 16)
    trace = tmp_path / "large.json"
    trace.write_text("x" * 17, encoding="utf-8")

    assert trace_report(str(trace)) == 1
    assert "exceeds the 64 MiB diagnostic limit" in capsys.readouterr().err


def test_installed_trace_report_rejects_oversized_zip_member(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from saitenka.app import trace_report as report_module

    monkeypatch.setattr(report_module, "_MAX_TRACE_BYTES", 16)
    report = tmp_path / "report.zip"
    with zipfile.ZipFile(report, "w") as archive:
        archive.writestr("telemetry/trace.json", "x" * 17)

    assert trace_report(str(report)) == 1
    assert "exceeds the 64 MiB diagnostic limit" in capsys.readouterr().err
