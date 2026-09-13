import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


def module():
    path = Path(__file__).resolve().parents[1] / "tools/character_masks.py"
    spec = importlib.util.spec_from_file_location("character_masks", path)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


def test_variation_selectors_and_combining_marks_stay_with_the_base():
    assert module().clusters("葛\U000e0100か\u3099犬") == [(0, 2), (2, 4), (4, 5)]


def test_default_geometry_manifest_survives_checkpoint_round_trip():
    frozen = module().manifest([(1000, 2000, "猫")], {"surface": (1280, 720)})
    assert json.loads(json.dumps(frozen)) == frozen


def test_one_shifted_interior_kanji_fails_with_unchanged_whole_cue_bounds():
    oracle = module()
    target = np.zeros((12, 30), dtype=np.uint8)
    target[3:9, 13:16] = 255
    context = target.copy()
    context[3:9, 2:5] = context[3:9, 24:27] = 255
    assert oracle.compare_mask(target, context, context)["verdict"] == "passed"
    for shift in (-1, 1):
        actual = context.copy()
        actual[3:9, 13:16] = 0
        actual[3:9, 13 + shift : 16 + shift] = 255
        assert np.where(actual)[1].min() == np.where(context)[1].min()
        assert np.where(actual)[1].max() == np.where(context)[1].max()
        assert oracle.compare_mask(target, actual, context)["verdict"] == "failed"


def test_missing_color_and_empty_reference_are_not_passes():
    oracle = module()
    mask = np.full((3, 3), 255, dtype=np.uint8)
    empty = np.zeros_like(mask)
    assert oracle.compare_mask(mask, empty, mask)["verdict"] == "failed"
    assert oracle.compare_mask(empty, mask, mask)["verdict"] == "inconclusive"


def test_translucent_intensity_disagreement_fails():
    oracle = module()
    reference = np.full((3, 3), 128, dtype=np.uint8)
    assert oracle.compare_mask(reference, reference, reference)["verdict"] == "passed"
    assert (
        oracle.compare_mask(reference, np.full_like(reference, 33), reference)["verdict"]
        == "failed"
    )


def test_stray_output_outside_every_character_fails_frame_coverage():
    oracle = module()
    reference = np.zeros((20, 20), dtype=np.uint8)
    reference[2:5, 2:5] = 255
    ours = reference.copy()
    ours[18, 18] = 255
    result = oracle.compare_mask(reference, ours, reference)
    assert result["verdict"] == "failed"
    assert result["frame_spill_pixels"] == 1


def test_checkpoint_keeps_the_full_denominator_on_interruption():
    oracle = module()
    frozen = oracle.manifest([(1000, 2000, "猫と犬"), (3000, 4000, "")], {"source": "hash"})
    first = frozen["coordinates"][0]["key"]
    report = oracle.results_summary(frozen, {first: {"verdict": "passed"}})
    assert report["total"] == 4
    assert report["counts"] == {
        "passed": 1,
        "failed": 0,
        "unsupported": 0,
        "inconclusive": 0,
        "unattempted": 3,
    }
    assert report["kanji"]["unattempted"] == 1
    changed = oracle.manifest([(1000, 2000, "猫と")], {"source": "hash"})
    assert changed["census_sha256"] != frozen["census_sha256"]


def test_neighbor_recoloring_does_not_invalidate_unchanged_target_ink():
    oracle = module()
    original = np.zeros((12, 30, 3), dtype=np.uint8)
    original[3:9, 3:6] = original[3:9, 20:23] = 255
    red = original.copy()
    red[:, :, 1:] = 0
    blue = red.copy()
    blue[3:9, 3:6] = (0, 0, 255)
    blue[3:9, 20:23] = (240, 0, 0)

    mask, reason = oracle.reference_mask(original, red, blue)

    assert reason == ""
    assert np.array_equal(mask, blue[:, :, 2])


def test_target_recoloring_cannot_move_the_native_reference_mask():
    oracle = module()
    original = np.zeros((12, 30, 3), dtype=np.uint8)
    original[3:9, 3:6] = 255
    red = original.copy()
    red[:, :, 1:] = 0
    blue = np.zeros_like(red)
    blue[3:9, 3:8, 2] = 255

    mask, reason = oracle.reference_mask(original, red, blue)

    assert reason == ""
    assert np.array_equal(mask, original[:, :, 0])
    assert oracle.compare_mask(mask, blue[:, :, 2], original[:, :, 0])["verdict"] == "failed"


def test_native_white_ink_cannot_pass_as_green_overpaint():
    oracle = module()
    original = np.full((3, 3, 3), 255, dtype=np.uint8)

    result = oracle.compare_mask(
        original[:, :, 0], oracle.green_coverage(original), original[:, :, 0]
    )

    assert result["verdict"] == "failed"
    assert result["missing_pixels"] == 9


def test_green_coverage_cancels_native_antialiasing():
    # Half-coverage green over half-intensity gray: (64, 192, 64).
    composite = np.full((3, 3, 3), (64, 192, 64), dtype=np.uint8)

    coverage = module().green_coverage(composite)

    assert np.array_equal(coverage, np.full((3, 3), 128, dtype=np.uint8))


def test_disappearing_disconnected_mark_cannot_shrink_the_denominator():
    original = np.zeros((9, 9, 3), dtype=np.uint8)
    original[2, 4] = original[6, 4] = 255
    red = original.copy()
    red[:, :, 1:] = 0
    blue = np.zeros_like(red)
    blue[6, 4, 2] = 255

    oracle = module()
    mask, reason = oracle.reference_mask(original, red, blue)

    assert reason == ""
    result = oracle.compare_mask(mask, blue[:, :, 2], original[:, :, 0])
    assert result["verdict"] == "failed"
    assert result["frame_missing_pixels"] == 1


def test_disconnected_ink_reassigned_by_probe_still_fails_whole_cue():
    oracle = module()
    original = np.zeros((9, 9, 3), dtype=np.uint8)
    original[2, 4] = original[6, 4] = 255
    red = original.copy()
    red[:, :, 1:] = 0
    blue = red.copy()
    blue[6, 4] = (0, 0, 255)
    mask, reason = oracle.reference_mask(original, red, blue)
    assert reason == ""

    result = oracle.compare_mask(mask, blue[:, :, 2], original[:, :, 0])

    assert result["verdict"] == "failed"
    assert result["cue_verdict"] == "failed"
    assert result["frame_missing_pixels"] == 1
