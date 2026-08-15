from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import mpv_shadow_geometry as oracle
import pytest
from mpv_shadow_geometry import (
    assess_masks,
    build_contract_report,
    exercise_controls,
    layer_support,
    load_manifest,
    shifted,
)

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "tests/fixtures/mpv_shadow_geometry.json"


@dataclass(frozen=True)
class Layer:
    width: int
    height: int
    bitmap: bytes
    color: int
    dst_x: int
    dst_y: int


@dataclass(frozen=True)
class Result:
    layers: tuple[Layer, ...]


def test_locked_manifest_covers_gate_a_by_gate_b_by_profiles_by_contracts() -> None:
    manifest = load_manifest(MANIFEST, repo_root=ROOT)
    assert manifest["denominator"]["matrix_count"] == 12 * 3 * 5 * 2


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("thresholds", "minimum_mask_iou"), 0.0, "IoU"),
        (("profiles", 0, "frame_size"), [0, 720], "positive"),
        (("denominator", "matrix_count"), 1, "matrix count"),
    ],
)
def test_manifest_rejects_weakened_execution_contract(
    tmp_path: Path, path: tuple[str | int, ...], value: object, reason: str
) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    target = manifest
    for item in path[:-1]:
        target = target[item]
    target[path[-1]] = value
    candidate = tmp_path / "manifest.json"
    candidate.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        load_manifest(candidate, repo_root=ROOT)


def test_every_locked_negative_control_proves_the_oracle_can_fail() -> None:
    manifest = load_manifest(MANIFEST, repo_root=ROOT)
    controls = exercise_controls(manifest["thresholds"])
    assert set(controls) == set(manifest["required_controls"])
    assert all(controls.values())


def test_mask_contract_accepts_one_pixel_support_drift() -> None:
    reference = frozenset((x, y) for x in range(5) for y in range(5))
    observed = reference | {(5, 2)}
    assessment = assess_masks(reference, observed, minimum_iou=0.8, maximum_distance=1)
    assert not assessment.passed
    assert not assessment.outer_bounds_equal
    assert assessment.within_maximum_distance


def test_mask_contract_rejects_two_pixel_shift() -> None:
    reference = frozenset((x, y) for x in range(5) for y in range(5))
    assessment = assess_masks(
        reference, shifted(reference, 2, 0), minimum_iou=0.95, maximum_distance=1
    )
    assert not assessment.passed
    assert not assessment.within_maximum_distance


def test_layer_support_uses_every_visible_libass_layer() -> None:
    result = Result(
        (
            Layer(2, 1, b"\xff\x00", 0xFFFFFF00, 10, 20),
            Layer(1, 1, b"\xff", 0x00000000, 9, 20),
            Layer(1, 1, b"\xff", 0x000000FF, 30, 30),
        )
    )
    assert layer_support(result) == {(9, 20), (10, 20)}


def test_contract_report_is_publishable_only_after_controls_pass() -> None:
    report = build_contract_report(MANIFEST, ROOT)
    assert report["contract_ready"] is True
    assert report["matrix_count"] == 360


def test_live_runner_emits_every_locked_matrix_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mask = frozenset({(10, 20), (11, 20)})

    class Renderer:
        def close(self) -> None:
            pass

    class Session:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def capture(
            self, _ass_path: Path, _timestamp_ms: int, _label: str
        ) -> tuple[frozenset[tuple[int, int]], tuple[int, int]]:
            sizes = {
                "baseline-720p": (1280, 720),
                "resize-480p": (854, 480),
                "retina-1080p": (1920, 1080),
                "wide-pixel-aspect": (1280, 720),
                "tall-pixel-aspect": (1280, 720),
            }
            return mask, next(size for name, size in sizes.items() if _label.startswith(name))

        def close(self) -> str:
            return "fake mpv log"

    result = Result((Layer(2, 1, b"\xff\xff", 0xFFFFFF00, 10, 20),))
    monkeypatch.setattr(oracle, "_MpvSession", Session)
    monkeypatch.setattr(oracle, "_write_clip", lambda *_args, **_kwargs: tmp_path / "clip.mkv")
    monkeypatch.setattr(
        oracle,
        "_shadow_result",
        lambda *_args, **_kwargs: (Renderer(), result),
    )
    monkeypatch.setattr(
        oracle,
        "extract_token_geometry",
        lambda _result, palette: tuple(palette),
    )
    monkeypatch.setattr(
        oracle.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="mpv fake\n"),
    )
    monkeypatch.setattr(oracle.platform, "platform", lambda: "test-platform")
    report = oracle.run_live_matrix(MANIFEST, ROOT)
    manifest = load_manifest(MANIFEST, repo_root=ROOT)
    assert report["matrix_passed"] is True
    assert len(report["cases"]) == 360
    assert all(row["frame_size_matches"] for row in report["cases"])
    assert {
        (row["case_id"], row["source_class"], row["profile_id"], row["contract"])
        for row in report["cases"]
    } == {
        (case, source, profile["id"], contract)
        for case in manifest["required_case_ids"]
        for source in manifest["source_classes"]
        for profile in manifest["profiles"]
        for contract in manifest["contracts"]
    }
