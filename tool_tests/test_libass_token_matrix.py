"""Locked research oracle for #353; token semantics stay outside libasslite."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from libass_token_matrix import (
    MAX_SUPPORT_DISTANCE,
    TokenKey,
    _load_manifest,
    ass_bytes,
    character_mask_signature,
    character_support,
    classification_hash,
    contract_set_hash,
    extract_token_geometry,
    fixture_hash,
    geometry_signature,
    key_set_hash,
    line_count,
    support_bounds,
    supports_within,
)

MANIFEST_PATH = Path(__file__).parents[1] / "tests" / "fixtures" / "libass_token_matrix.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
REQUIRED_CASES = [case for case in MANIFEST["cases"] if case["expectation"] == "required-core"]


@dataclass(frozen=True)
class FakeLayer:
    width: int
    height: int
    bitmap: bytes
    color: int
    dst_x: int
    dst_y: int
    image_type: int = 0


@dataclass(frozen=True)
class FakeResult:
    layers: tuple[FakeLayer, ...]


def _layer(rgb: int, x: int, width: int = 2) -> FakeLayer:
    return FakeLayer(width, 2, bytes([255] * (width * 2)), rgb << 8, x, 4)


def _configured_library() -> Path | None:
    configured = os.environ.get("LIBASSLITE_LIBRARY")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path("/opt/homebrew/lib/libass.dylib")
    return None


def _libasslite():
    return pytest.importorskip(
        "libasslite", reason="matrix requires the compiled research dependency"
    )


def _render(case: dict, variant: str):
    libasslite = _libasslite()
    renderer = libasslite.AssRenderer(
        ass_bytes(case[f"{variant}_events"]), library_path=_configured_library()
    )
    try:
        return renderer.render(case["timestamp_ms"], (1280, 720), (1280, 720))
    finally:
        renderer.close()


def _palette(case: dict) -> list[tuple[int, TokenKey]]:
    return [
        (int(item["rgb"], 16), TokenKey(item["event_id"], item["token_index"]))
        for item in case["palette"]
    ]


def test_manifest_denominator_and_execution_contract_are_locked() -> None:
    cases = MANIFEST["cases"]
    assert len(cases) == MANIFEST["denominator"]["count"]
    assert key_set_hash(case["id"] for case in cases) == MANIFEST["denominator"]["key_set_sha256"]
    assert classification_hash(cases) == MANIFEST["denominator"]["classification_sha256"]
    assert contract_set_hash(cases) == MANIFEST["denominator"]["contract_set_sha256"]
    assert all(
        fixture_hash(case["visible_events"]) != fixture_hash(case["id_events"]) for case in cases
    )
    assert _load_manifest(MANIFEST_PATH) == MANIFEST


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_loader_rejects_changed_case_count(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["cases"].pop()
    with pytest.raises(ValueError, match="case count changed"):
        _load_manifest(_write_manifest(tmp_path, manifest))


def test_loader_rejects_reclassified_case(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["cases"][0]["expectation"] = "fallback-candidate"
    with pytest.raises(ValueError, match="classifications changed"):
        _load_manifest(_write_manifest(tmp_path, manifest))


def test_loader_rejects_changed_fixture(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["cases"][0]["visible_events"][0] += " changed"
    with pytest.raises(ValueError, match="execution contracts changed"):
        _load_manifest(_write_manifest(tmp_path, manifest))


def test_loader_rejects_weakened_execution_threshold(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    wrapping = next(case for case in manifest["cases"] if case["id"] == "wrapping")
    wrapping["min_rows"] = 1
    with pytest.raises(ValueError, match="execution contracts changed"):
        _load_manifest(_write_manifest(tmp_path, manifest))


def test_loader_rejects_weakened_support_threshold(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["denominator"]["maximum_chebyshev_distance_px"] = 2
    with pytest.raises(ValueError, match="support distance threshold changed"):
        _load_manifest(_write_manifest(tmp_path, manifest))


def test_extractor_unions_segments_in_event_token_order() -> None:
    result = FakeResult((_layer(65793, 10), _layer(131586, 20), _layer(65793, 13)))
    geometry = extract_token_geometry(
        result, [(65793, TokenKey("dialogue", 0)), (131586, TokenKey("dialogue", 1))]
    )
    observed = [(item.key, item.bounds.x, item.bounds.width) for item in geometry]
    expected = [(TokenKey("dialogue", 0), 10, 5), (TokenKey("dialogue", 1), 20, 2)]
    assert observed == expected


def test_line_count_merges_glyphs_with_different_tops() -> None:
    result = FakeResult(
        (_layer(65793, 10), FakeLayer(2, 1, b"\xff\xff", 131586 << 8, 20, 5), _layer(197379, 30))
    )
    geometry = extract_token_geometry(
        result,
        [
            (65793, TokenKey("dialogue", 0)),
            (131586, TokenKey("dialogue", 1)),
            (197379, TokenKey("dialogue", 2)),
        ],
    )
    assert line_count(geometry) == 1


def test_support_distance_rejects_two_pixel_shift() -> None:
    left = frozenset({(0, 0), (1, 0)})
    right = frozenset({(2, 0), (3, 0)})
    assert not supports_within(left, right, MAX_SUPPORT_DISTANCE)


def test_extractor_rejects_unknown_character_color() -> None:
    result = FakeResult((_layer(65793, 10), _layer(592137, 20)))
    with pytest.raises(ValueError, match="unknown character color"):
        extract_token_geometry(result, [(65793, TokenKey("dialogue", 0))])


def test_extractor_rejects_missing_token_color() -> None:
    result = FakeResult((_layer(65793, 10),))
    with pytest.raises(ValueError, match="missing token colors"):
        extract_token_geometry(
            result, [(65793, TokenKey("dialogue", 0)), (131586, TokenKey("dialogue", 1))]
        )


def test_extractor_rejects_palette_collision() -> None:
    with pytest.raises(ValueError, match="duplicate token color"):
        extract_token_geometry(
            FakeResult(()), [(65793, TokenKey("a", 0)), (65793, TokenKey("b", 0))]
        )


def test_extractor_rejects_overlapping_painted_pixels() -> None:
    result = FakeResult((_layer(65793, 10, 4), _layer(131586, 12, 4)))
    with pytest.raises(ValueError, match="ambiguous token bounds"):
        extract_token_geometry(result, [(65793, TokenKey("a", 0)), (131586, TokenKey("b", 0))])


@pytest.mark.integration
@pytest.mark.parametrize("case", REQUIRED_CASES, ids=lambda case: case["id"])
def test_required_core_preserves_geometry_mask_and_token_identity(case: dict) -> None:
    visible = _render(case, "visible")
    hit_map = _render(case, "id")
    geometry = extract_token_geometry(
        hit_map, _palette(case), reserved_colors=[int(value, 16) for value in case["reserved_rgb"]]
    )
    visible_support = character_support(visible)
    id_support = character_support(hit_map)
    assert support_bounds(visible_support) == support_bounds(id_support)
    assert supports_within(visible_support, id_support, MAX_SUPPORT_DISTANCE)
    assert [item.key for item in geometry] == sorted((key for _, key in _palette(case)))
    assert line_count(geometry) >= case.get("min_rows", 1)
    assert max(len(item.segments) for item in geometry) >= case.get("min_max_segments", 1)


@pytest.mark.integration
def test_geometry_oracle_detects_deliberate_font_size_change() -> None:
    case = REQUIRED_CASES[0]
    libasslite = _libasslite()
    baseline = libasslite.AssRenderer(
        ass_bytes(case["visible_events"]), library_path=_configured_library()
    )
    changed = libasslite.AssRenderer(
        ass_bytes(case["visible_events"]).replace(b"Arial,48", b"Arial,60"),
        library_path=_configured_library(),
    )
    try:
        baseline_result = baseline.render(case["timestamp_ms"], (1280, 720), (1280, 720))
        changed_result = changed.render(case["timestamp_ms"], (1280, 720), (1280, 720))
    finally:
        changed.close()
        baseline.close()
    assert geometry_signature(baseline_result) != geometry_signature(changed_result)
    assert character_mask_signature(baseline_result) != character_mask_signature(changed_result)
    baseline_support = character_support(baseline_result)
    changed_support = character_support(changed_result)
    assert support_bounds(baseline_support) != support_bounds(changed_support)
    assert not supports_within(baseline_support, changed_support, MAX_SUPPORT_DISTANCE)
