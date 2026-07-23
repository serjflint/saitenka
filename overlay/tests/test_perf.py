"""In-process op timing ring buffer: recording, percentile snapshot, ring cap, crash/doctor wiring."""

from __future__ import annotations

import time

from overlay.app import perf


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


def test_crash_report_includes_recent_op_timings_and_rss(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    perf.record("show_tooltip", 12.3)
    from overlay.app import crashlog

    p = crashlog.write_report("main-thread", "ValueError: kaboom")
    text = p.read_text(encoding="utf-8")
    assert "recent op timings" in text
    assert "show_tooltip" in text
    assert "rss:" in text


def test_crash_report_includes_rss_even_with_no_ops_recorded(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    from overlay.app import crashlog

    p = crashlog.write_report("main-thread", "ValueError: kaboom")
    text = p.read_text(encoding="utf-8")
    assert "recent op timings" in text  # RSS alone still triggers the section
    assert "rss:" in text


def test_crash_report_omits_perf_section_when_rss_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    from overlay.app import crashlog

    monkeypatch.setattr("overlay.app.perf.rss_mb", lambda: None)
    p = crashlog.write_report("main-thread", "ValueError: kaboom")
    text = p.read_text(encoding="utf-8")
    assert "recent op timings" not in text


def test_doctor_check_perf_reports_snapshot_and_rss() -> None:
    from overlay.app import doctor as doc

    c = doc.check_perf()
    assert c.status == "ok"
    assert "no ops recorded yet" in c.detail
    assert "rss=" in c.detail
    perf.record("show_tooltip", 5.0)
    c = doc.check_perf()
    assert c.status == "ok"
    assert "show_tooltip" in c.detail
    assert "rss=" in c.detail
