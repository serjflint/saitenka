import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from character_masks import compare_mask, manifest, qualification_counts, results_summary


def qualified_artifact():
    frozen = manifest([(1000, 2000, "猫")], {})
    alpha = np.full((2, 2), 255, dtype=np.uint8)
    row = compare_mask(alpha, alpha, alpha)
    row["controls"] = {
        "positive": "passed",
        "displaced": "failed",
        "missing-stroke": "failed",
        "absent-output": "failed",
        "wrong-color": "failed",
    }
    return frozen, results_summary(frozen, {frozen["coordinates"][0]["key"]: row})


def test_antialiased_fringe_variation_is_diagnostic_but_can_qualify_opaque_ink():
    reference = np.array([[0, 16, 255, 16, 0]], dtype=np.uint8)
    actual = np.array([[16, 0, 255, 0, 16]], dtype=np.uint8)

    result = compare_mask(reference, actual, reference)

    assert result["verdict"] == "passed"
    assert result["exact_alpha"] is False
    assert result["missing_pixels"] == result["frame_spill_pixels"] == 2
    assert result["intensity_error"] == 16

    frozen = manifest([(1000, 2000, "猫")], {})
    result["controls"] = {
        "positive": "passed",
        "displaced": "failed",
        "missing-stroke": "failed",
        "absent-output": "failed",
        "wrong-color": "failed",
    }
    report = results_summary(frozen, {frozen["coordinates"][0]["key"]: result})
    assert qualification_counts(frozen, report)["qualified"] is True


@pytest.mark.parametrize(
    "fault", ["none", "missing-control", "numeric-disagreement", "unattempted"]
)
def test_execution_controls_and_pixel_numbers_are_separate_qualification_axes(fault):
    frozen, result = qualified_artifact()
    row = result["results"][0]
    if fault == "missing-control":
        del row["controls"]
    elif fault == "numeric-disagreement":
        row["frame_missing_pixels"] = 1
    elif fault == "unattempted":
        row["verdict"] = "unattempted"
        del row["controls"]

    evidence = qualification_counts(frozen, result)

    assert evidence["denominator"] == 1
    assert evidence["qualified"] == (fault in {"none", "numeric-disagreement"})
    assert evidence["oracle_execution"]["valid_controls"] == int(
        fault in {"none", "numeric-disagreement"}
    )


@pytest.mark.parametrize("fault", ["missing", "duplicate", "other-manifest", "old-contract"])
def test_qualification_rejects_shrunk_duplicated_or_mismatched_census(fault):
    frozen, result = qualified_artifact()
    if fault == "missing":
        result["results"] = []
    elif fault == "duplicate":
        result["results"] *= 2
    elif fault == "other-manifest":
        result["manifest_sha256"] = "other"
    else:
        result["schema"] = 1
        result.pop("oracle_contract")

    with pytest.raises(ValueError, match="qualification"):
        qualification_counts(frozen, result)


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


@pytest.mark.parametrize("intensity", [224, 128])
def test_dimmed_opaque_interior_fails_even_when_support_and_centroid_match(intensity):
    oracle = module()
    reference = np.full((7, 7), 255, dtype=np.uint8)

    result = oracle.compare_mask(reference, np.full_like(reference, intensity), reference)

    assert result["verdict"] == "failed"
    assert result["opaque_core_missing_pixels"] > 0


def test_faint_disconnected_mark_cannot_disappear_or_move():
    oracle = module()
    reference = np.zeros((9, 9), dtype=np.uint8)
    reference[6:8, 3:6] = 180
    reference[2, 4] = 24
    assert oracle.compare_mask(reference, reference, reference)["verdict"] == "passed"

    missing = reference.copy()
    missing[2, 4] = 0
    shifted = reference.copy()
    shifted[2, 4] = 0
    shifted[2, 5] = 24

    assert oracle.compare_mask(reference, missing, reference)["verdict"] == "failed"
    assert oracle.compare_mask(reference, shifted, shifted)["verdict"] == "failed"


def test_faint_connected_stroke_cannot_disappear_as_an_antialias_edge():
    oracle = module()
    reference = np.zeros((9, 12), dtype=np.uint8)
    reference[3:7, 3:7] = 255
    reference[4, 7:10] = 24
    missing = reference.copy()
    missing[4, 7:10] = 0

    result = oracle.compare_mask(reference, missing, reference)

    assert result["verdict"] == "failed"
    assert result["support_policy_violation_pixels"] > 0


def test_oracle_refuses_unbounded_ink_before_component_materialization(monkeypatch):
    oracle = module()
    monkeypatch.setattr(oracle, "MAX_ORACLE_INK_PIXELS", 8)
    reference = np.full((3, 3), 255, dtype=np.uint8)

    result = oracle.compare_mask(reference, reference, reference)

    assert result == {
        "verdict": "inconclusive",
        "reason": "oracle-ink-budget",
        "oracle_contract": "opaque-coloring-v2",
    }


def test_stray_output_outside_every_character_fails_frame_coverage():
    oracle = module()
    reference = np.zeros((20, 20), dtype=np.uint8)
    reference[2:5, 2:5] = 255
    ours = reference.copy()
    ours[18, 18] = 255
    result = oracle.compare_mask(reference, ours, reference)
    assert result["verdict"] == "failed"
    assert result["frame_spill_pixels"] == 1


def test_adjacent_extra_ink_cannot_pass_as_an_overprint_border():
    oracle = module()
    reference = np.zeros((5, 5), dtype=np.uint8)
    reference[2, 2] = 255
    ours = reference.copy()
    ours[2, 3] = 255

    result = oracle.compare_mask(reference, ours, reference)

    assert result["verdict"] == "failed"
    assert result["spill_pixels"] == 1
    assert result["frame_spill_pixels"] == 1


def test_faint_output_is_only_an_edge_when_adjacent_to_reference_ink():
    oracle = module()
    reference = np.zeros((9, 9), dtype=np.uint8)
    reference[3:6, 3:6] = 255
    actual = reference.copy()
    actual[0, 0] = 16

    assert oracle.compare_mask(reference, actual, reference)["verdict"] == "failed"


@pytest.mark.parametrize("fault", ["displaced", "missing-stroke", "absent-output"])
def test_opaque_fault_controls_discriminate(fault):
    oracle = module()
    reference = np.zeros((9, 12), dtype=np.uint8)
    reference[2:7, 3:8] = 255
    actual = reference.copy()
    if fault == "displaced":
        actual = np.roll(actual, 1, axis=1)
    elif fault == "missing-stroke":
        actual[4, 5] = 0
    else:
        actual[:] = 0

    assert oracle.compare_mask(reference, actual, reference)["verdict"] == "failed"


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


def test_adjacent_ink_columns_do_not_merge_distinct_characters():
    original = np.zeros((8, 12, 3), dtype=np.uint8)
    original[1:4, 2:5] = 255
    original[4:7, 5:8] = 255
    red = original.copy()
    red[:, :, 1:] = 0
    blue = red.copy()
    blue[1:4, 2:5] = (0, 0, 255)

    mask, reason = module().reference_mask(original, red, blue)

    assert reason == ""
    assert np.array_equal(mask, blue[:, :, 2])


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


def test_isolation_control_rejects_color_run_phase_change_with_unchanged_bounds():
    original = np.full((3, 3, 3), 255, dtype=np.uint8)
    isolated = np.zeros_like(original)
    isolated[:, :, 2] = 255
    assert module().isolation_preserves_ink(original, isolated)
    isolated[1, 1, 2] = 254
    assert not module().isolation_preserves_ink(original, isolated)
