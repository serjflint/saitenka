from __future__ import annotations

from native_mask_benchmark import benchmark


def test_synthetic_worker_benchmark_carries_fidelity_and_matched_work():
    result = benchmark(cycles=2, track_copies=1, sizes=((1280, 720),))

    assert result["source_events"] == 12
    assert len(result["rows"]) == 24
    assert all(row["corrected"]["native_differing_pixels"] == 0 for row in result["rows"])
    assert all(row["corrected"]["found_tokens"] == row["eligible_tokens"] for row in result["rows"])
    assert all(row["corrected"]["mask_source"] == "native-original" for row in result["rows"])
    assert all(
        row["identity_hint_baseline"]["found_tokens"] == row["eligible_tokens"]
        for row in result["rows"]
    )
    assert all(
        "subtitle_geometry_native_reference" in row["corrected"]["phases"] for row in result["rows"]
    )
