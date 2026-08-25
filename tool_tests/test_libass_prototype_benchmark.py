from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from libass_prototype_benchmark import (
    _bgr_to_rgb,
    _cold_samples,
    _render_activated,
    _run_and_persist,
    contract_hash,
    evaluate_budgets,
    load_manifest,
    percentile,
    prepare_case,
)

MANIFEST = Path(__file__).parents[1] / "tests" / "fixtures" / "libass_prototype_benchmark.json"


def test_manifest_locks_the_full_execution_contract(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["budgets"]["warm_samples"] = 999
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="execution contract"):
        load_manifest(path)


def test_contract_hash_does_not_hash_itself() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    changed = copy.deepcopy(manifest)
    changed["denominator"]["contract_sha256"] = "different"

    assert contract_hash(changed) == contract_hash(manifest)


def test_ass_bgr_ids_match_public_layer_rgb() -> None:
    assert _bgr_to_rgb(0x000001) == 0x010000
    assert _bgr_to_rgb(0x112233) == 0x332211


def test_warm_render_forces_cue_activation_before_timing() -> None:
    class FakeRenderer:
        def __init__(self) -> None:
            self.timestamps: list[int] = []

        def render(
            self, timestamp_ms: int, _frame_size: tuple[int, int], _storage_size: tuple[int, int]
        ):
            self.timestamps.append(timestamp_ms)
            return object()

    ticks = iter((1_000_000, 1_009_000))
    renderer = FakeRenderer()

    _result, elapsed_us = _render_activated(renderer, 1500, lambda: next(ticks))

    assert renderer.timestamps == [0, 1500]
    assert elapsed_us == 9


def test_warm_render_rejects_a_nonpositive_active_timestamp() -> None:
    class UnusedRenderer:
        def render(self, *_args):
            raise AssertionError("invalid timestamp must fail before rendering")

    with pytest.raises(ValueError, match="must follow the inactive frame"):
        _render_activated(UnusedRenderer(), 0, lambda: 0)


def test_source_marked_auxiliary_is_visible_but_not_interactive() -> None:
    manifest = load_manifest(MANIFEST)
    case = next(case for case in manifest["cases"] if case["id"] == "source-marked-auxiliary")

    prototype = prepare_case(case)
    primary_only = copy.deepcopy(case)
    primary_only["events"] = [case["events"][0]]
    primary_prototype = prepare_case(primary_only)

    assert case["events"][1]["line"].encode() in prototype.visible_ass
    assert "にほんご".encode() in prototype.styled_ass
    assert "にほんご".encode() not in prototype.shadow_ass
    assert prototype.shadow_ass == primary_prototype.shadow_ass
    assert prototype.interactive_tokens == primary_prototype.interactive_tokens
    assert prototype.excluded_events == 1
    assert prototype.reserved_rgb == (0xFFFFFF,)


def test_ambiguous_event_is_preserved_and_disables_interaction() -> None:
    manifest = load_manifest(MANIFEST)
    case = next(case for case in manifest["cases"] if case["id"] == "ambiguous-positioned-text")

    prototype = prepare_case(case)

    assert case["events"][0]["line"].encode() in prototype.visible_ass
    assert "東京都".encode() not in prototype.shadow_ass
    assert prototype.interactive_tokens == 0
    assert prototype.excluded_events == 1


def test_percentile_uses_nearest_rank() -> None:
    samples = list(range(1, 101))

    assert percentile(samples, 95) == 95
    assert percentile(samples, 99) == 99
    assert percentile([7], 99) == 7


@pytest.mark.parametrize(("samples", "value"), [([], 99), ([1], 0), ([1], 101)])
def test_percentile_rejects_undefined_inputs(samples: list[int], value: int) -> None:
    with pytest.raises(ValueError, match="percentile needs samples"):
        percentile(samples, value)


def _passing_report(budgets: dict) -> dict:
    return {
        "warm_samples": [
            {"render_us": budgets["static_render_p99_us"]} for _ in range(budgets["warm_samples"])
        ],
        "cold_geometry_samples": [
            {
                "latency_us": budgets["cold_geometry_p95_us"],
                "library_path": "/libass",
                "library_version": "0x1",
            }
            for _ in range(budgets["cold_process_starts"])
        ],
        "animated_cadence": {
            "frame_count": 48,
            "active_frame_count": 48,
            "unique_geometry_frames": budgets["cadence_min_unique_frames"],
            "skipped_budget_count": budgets["cadence_max_skipped_frames"],
        },
    }


def test_budget_boundaries_are_inclusive() -> None:
    budgets = load_manifest(MANIFEST)["budgets"]

    assert evaluate_budgets(_passing_report(budgets), budgets) == ()


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("short-warm", "warm sample count"),
        ("short-cold", "cold process sample count"),
        ("slow-render", "static render p99"),
        ("slow-cold", "cold geometry p95"),
        ("short-animation", "animated frame count"),
        ("empty-animation", "animated active frames"),
        ("frozen-animation", "animated geometry changes"),
        ("slow-animation", "animated cadence skips"),
    ],
)
def test_each_budget_control_can_fail(mutation: str, failure: str) -> None:
    budgets = load_manifest(MANIFEST)["budgets"]
    report = _passing_report(budgets)
    if mutation == "short-warm":
        report["warm_samples"].pop()
    elif mutation == "short-cold":
        report["cold_geometry_samples"].pop()
    elif mutation == "slow-render":
        for sample in report["warm_samples"][-11:]:
            sample["render_us"] += 1
    elif mutation == "slow-cold":
        for sample in report["cold_geometry_samples"][-2:]:
            sample["latency_us"] += 1
    elif mutation == "short-animation":
        report["animated_cadence"]["frame_count"] = 24
        report["animated_cadence"]["active_frame_count"] = 24
    elif mutation == "empty-animation":
        report["animated_cadence"]["active_frame_count"] = 0
    elif mutation == "frozen-animation":
        report["animated_cadence"]["unique_geometry_frames"] = 1
    else:
        report["animated_cadence"]["skipped_budget_count"] = 1

    assert failure in evaluate_budgets(report, budgets)


def test_cold_samples_reject_a_different_loaded_library(monkeypatch, tmp_path: Path) -> None:
    sample = {
        "latency_us": 1,
        "library_path": "/other/libass",
        "library_version": "0x1",
    }
    monkeypatch.setattr(
        "libass_prototype_benchmark.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(sample)),
    )

    with pytest.raises(RuntimeError, match="different libass binary"):
        list(_cold_samples(tmp_path / "manifest.json", 1, "/expected/libass", "0x1"))


def test_failed_run_preserves_completed_samples(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    def fail_after_sample(progress):
        progress({"status": "running", "warm_samples": [{"render_us": 7}]})
        raise RuntimeError("cold worker failed")

    with pytest.raises(RuntimeError, match="cold worker failed"):
        _run_and_persist(output, fail_after_sample)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["warm_samples"] == [{"render_us": 7}]
    assert report["error"] == "RuntimeError: cold worker failed"
