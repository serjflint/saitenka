"""The device/renderer benchmark's own oracle: that each leg it prices actually did work.

A benchmark reporting a leg that ran empty is worse than no benchmark, because the number looks like
a result. The first draft of this one sized 48×48 coverage masks for 120×48 boxes, `TokenMask.usable`
rejected every mask, `compose` returned `None`, and device 2 came out as the *cheapest* leg — 1.77 µs
per token for composing nothing. These pin the guards that now make that a hard failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import bench_render_devices as bench


def test_a_coverage_mask_matches_the_box_it_is_measured_against() -> None:
    """`TokenMask.usable` demands `len(coverage) == width * height`, and a mask that fails it is
    dropped silently rather than refused — so the fixture's own sizing is the load-bearing part."""
    assert len(bench._coverage(16)) == 16 * 16
    assert len(set(bench._coverage(16))) > 1, (
        "a flat mask is the one shape a tint could special-case"
    )


def test_the_raster_leg_refuses_to_report_a_composite_it_never_made(monkeypatch) -> None:
    """The negative control for the bug that shipped: break the mask sizing and the benchmark must
    fail loudly instead of reporting device 2 as free."""
    monkeypatch.setattr(bench, "_coverage", lambda side: bytes(side * side + 1))

    with pytest.raises(AssertionError, match="composed nothing"):
        bench.benchmark_devices(bench.DeviceBenchmarkConfig(reps=1, tokens=2, mask_side=8))


def test_the_text_leg_refuses_to_report_a_payload_it_never_drew(monkeypatch) -> None:
    """Its mirror: device 1 measured against tokens it cannot draw would time an empty string."""
    monkeypatch.setattr(bench, "CUE_TOKENS", ("{\\b1}",))

    with pytest.raises(AssertionError, match="drew nothing"):
        bench.benchmark_devices(bench.DeviceBenchmarkConfig(reps=1, tokens=2, mask_side=8))


def test_every_device_leg_is_priced_from_work_it_actually_did(tmp_path: Path) -> None:
    """The shape of the result, and that each leg did work — not their ORDERING.

    This asserted device 2 costs more per token than device 1, which is structurally true at a real
    cue (it composites an area where device 1 emits a string) and noise at the sizes a fast test can
    afford: 4 tokens and a 12x12 mask, three reps. It passed until an unrelated change and then
    failed on timing alone. A benchmark's self-test can check that a leg ran; ranking two legs by
    duration is the benchmark's job to report, not a gate's to enforce."""
    output = tmp_path / "bench.json"

    result = bench.run(bench.DeviceBenchmarkConfig(reps=3, tokens=4, mask_side=12), output)

    names = {item["name"] for item in result}
    assert {"device 1: per token", "device 2: per token", "legacy renderer: per token"} <= names
    assert json.loads(output.read_text(encoding="utf-8")) == result
    by_name = {item["name"]: item["value"] for item in result}
    assert by_name["device 1: per token"] > 0
    assert by_name["device 2: per token"] > 0
    assert all(item["value"] >= 0 for item in result)


def test_an_absent_libass_is_reported_as_absent_not_as_zero(monkeypatch, tmp_path: Path) -> None:
    """The measuring renderer is the one leg with an optional native dependency. Reporting 0 ms for
    it would read as "free" rather than "not measured", and it is the most expensive leg there is."""
    monkeypatch.setattr(bench, "benchmark_libasslite", lambda _config: None)

    result = bench.run(
        bench.DeviceBenchmarkConfig(reps=2, tokens=3, mask_side=8), tmp_path / "b.json"
    )

    assert not any(item["name"].startswith("libasslite") for item in result)


def test_the_engine_comparison_measures_both_engines_on_one_cue() -> None:
    """Two sessions cannot be compared — extraction cost scales with painted ink, so a different
    episode moves the number for reasons that are not the engine. One field trace read
    `subtitle_geometry_libass` at p50 1.4 ms where another read 31.7 ms, on identical code.

    Same cue, same process, both engines is what settles it. The ratio is reported so a reader
    cannot take the native total for the whole frame cost: mpv's OSD draw is out of process and
    uncounted, so native is understated by construction.
    """
    result = bench.benchmark_engines(bench.DeviceBenchmarkConfig(reps=2, tokens=4, mask_side=8))

    if result is None:
        pytest.skip("libass unavailable — the native leg cannot be measured here")
    assert result["engine_legacy_p50_ms"] > 0
    assert result["engine_native_in_process_p50_ms"] > 0
    ratio = result["engine_native_in_process_p50_ms"] / result["engine_legacy_p50_ms"]
    assert result["engine_native_over_legacy"] == pytest.approx(ratio)


def test_an_absent_libass_makes_the_engine_comparison_absent_too(
    monkeypatch, tmp_path: Path
) -> None:
    """Reporting only the legacy half would read as "legacy is the whole story", which is the
    opposite of what a missing native measurement means."""
    monkeypatch.setattr(bench, "benchmark_engines", lambda _config: None)

    result = bench.run(
        bench.DeviceBenchmarkConfig(reps=2, tokens=3, mask_side=8), tmp_path / "b.json"
    )

    assert not any(item["name"].startswith("engine") for item in result)
