from __future__ import annotations

import pytest
from native_subtitle_integration_benchmark import evaluate, load_manifest


def report() -> dict:
    return {
        "event_count": 101,
        "interaction_p99_ms": 1.0,
        "interaction_delta_p99_ms": 0.5,
        "ready_before_presentation_ratio": 100 / 101,
        "ready_before_presented": 100,
        "geometry_apply_count": 100,
        "hit_test_count": 100,
        "focus_draw_count": 100,
        "tooltip_open_count": 100,
        "tooltip_scroll_count": 100,
        "retained_rss_growth_mib": 32.0,
        "result_cache_entries": 3,
        "prefetch_cache_entries": 3,
        "presented": 101,
        "completed": 101,
        "failures": 0,
        "last_error": None,
        "superseded": 0,
        "prefetch_dropped": 0,
        "cadence_misses": 0,
        "source_clear_current": False,
        "source_clear_hit_count": 0,
        "profile_switch_cache_entries": 0,
        "close_completed": True,
    }


def manifest() -> dict:
    return {
        "event_count": 101,
        "cache_max": 3,
        "budgets": {
            "interaction_p99_ms": 8.0,
            "interaction_delta_p99_ms": 2.0,
            "ready_before_presentation_ratio": 0.99,
            "retained_rss_growth_mib": 256.0,
        },
    }


def test_budget_oracle_accepts_locked_boundary() -> None:
    assert evaluate(report(), manifest())


def test_budget_oracle_rejects_each_regression() -> None:
    controls = {
        "event_count": 100,
        "interaction_p99_ms": 8.01,
        "interaction_delta_p99_ms": 2.01,
        "ready_before_presentation_ratio": 0.989,
        "ready_before_presented": 99,
        "retained_rss_growth_mib": 256.01,
        "result_cache_entries": 4,
        "prefetch_cache_entries": 4,
        "presented": 100,
        "completed": 100,
        "geometry_apply_count": 99,
        "hit_test_count": 99,
        "focus_draw_count": 99,
        "tooltip_open_count": 99,
        "tooltip_scroll_count": 99,
        "failures": 1,
        "last_error": "render failed",
        "superseded": 1,
        "prefetch_dropped": 1,
        "cadence_misses": 1,
        "source_clear_current": True,
        "source_clear_hit_count": 1,
        "profile_switch_cache_entries": 1,
        "close_completed": False,
    }
    for field, value in controls.items():
        mutated = report()
        mutated[field] = value
        assert not evaluate(mutated, manifest()), field


def test_manifest_lock_rejects_budget_weakening(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema": 1, "budgets": {"interaction_p99_ms": 8000}}', encoding="utf-8")

    with pytest.raises(ValueError, match="re-locking"):
        load_manifest(path)
