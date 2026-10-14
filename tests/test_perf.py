"""In-process op timing ring buffer: recording, percentile snapshot, ring cap, crash/doctor wiring."""

from __future__ import annotations

import time

import pytest

from saitenka.app import perf


def setup_function() -> None:
    perf.reset()


def test_record_and_snapshot_percentiles() -> None:
    for ms in [1.0, 2.0, 3.0, 4.0, 100.0]:
        perf.record("op", ms)
    snap = perf.snapshot()
    assert snap["op"]["n"] == 5
    assert snap["op"]["max"] == 100.0
    assert snap["op"]["p50"] == 3.0


def test_snapshot_empty_until_recorded() -> None:
    assert perf.snapshot() == {}


def test_timed_records_a_sample() -> None:
    with perf.timed("sleepy"):
        time.sleep(0.001)
    snap = perf.snapshot()
    assert snap["sleepy"]["n"] == 1
    assert snap["sleepy"]["max"] > 0


def test_ring_buffer_is_bounded() -> None:
    for i in range(perf._MAXLEN + 50):
        perf.record("op", float(i))
    snap = perf.snapshot()
    assert snap["op"]["n"] == perf._MAXLEN
    assert snap["op"]["max"] == float(perf._MAXLEN + 49)  # oldest samples dropped


def test_rss_mb_returns_a_positive_reading() -> None:
    rss = perf.rss_mb()
    assert rss is not None
    assert rss > 0


def test_rss_mb_returns_none_on_psutil_failure(monkeypatch) -> None:
    import psutil

    def _boom():
        raise OSError("no such process")

    monkeypatch.setattr(psutil, "Process", _boom)
    assert perf.rss_mb() is None


@pytest.mark.parametrize(
    ("recorded_ms", "rss", "expected"),
    [
        (12.3, 42.0, ("recent op timings", "show_tooltip", "rss:")),
        (None, 42.0, ("recent op timings", "rss:")),
        (None, None, ()),
    ],
    ids=["timings-and-rss", "rss-only", "empty"],
)
def test_crash_report_perf_section(monkeypatch, tmp_path, recorded_ms, rss, expected):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(perf, "rss_mb", lambda: rss)
    if recorded_ms is not None:
        perf.record("show_tooltip", recorded_ms)
    from saitenka.app import crashlog

    p = crashlog.write_report("main-thread", "ValueError: kaboom")
    text = p.read_text(encoding="utf-8")
    if expected:
        assert all(fragment in text for fragment in expected)
    else:
        assert "recent op timings" not in text


@pytest.mark.parametrize(
    ("recorded_ms", "expected"),
    [(None, "no ops recorded yet"), (5.0, "show_tooltip")],
    ids=["empty-snapshot", "populated-snapshot"],
)
def test_doctor_check_perf_reports_snapshot_and_rss(monkeypatch, recorded_ms, expected) -> None:
    from saitenka.app import doctor as doc

    monkeypatch.setattr(perf, "rss_mb", lambda: 42.0)
    if recorded_ms is not None:
        perf.record("show_tooltip", recorded_ms)
    c = doc.check_perf()
    assert c.status == "ok"
    assert expected in c.detail and "rss=42MB" in c.detail
