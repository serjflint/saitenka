"""Local character-mask census and opaque-coloring oracle. Inputs and images stay local."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass

import numpy as np

ORACLE_CONTRACT = "opaque-coloring-v2"
AA_SUPPORT_MAX = 32
CENTROID_TOLERANCE = 0.25
MAX_ORACLE_INK_PIXELS = 262_144
MAX_ORACLE_COMPONENTS = 4_096
FAULT_CONTROLS = {
    "positive": "passed",
    "displaced": "failed",
    "missing-stroke": "failed",
    "absent-output": "failed",
    "wrong-color": "failed",
}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def clusters(text: str) -> list[tuple[int, int]]:
    """Conservative base+combining/variation-selector ranges; ZWJ sequences remain one unit."""
    ranges: list[tuple[int, int]] = []
    for index, char in enumerate(text):
        continuation = (
            unicodedata.combining(char) != 0
            or unicodedata.category(char).startswith("M")
            or char == "\u200d"
            or (index > 0 and text[index - 1] == "\u200d")
        )
        if continuation and ranges:
            ranges[-1] = ranges[-1][0], index + 1
        else:
            ranges.append((index, index + 1))
    return ranges


def is_kanji(text: str) -> bool:
    return any(
        "CJK UNIFIED IDEOGRAPH" in unicodedata.name(char, "")
        or "CJK COMPATIBILITY IDEOGRAPH" in unicodedata.name(char, "")
        for char in text
    )


@dataclass(frozen=True)
class Coordinate:
    event: int
    sample_ms: int
    start: int
    end: int
    kanji: bool
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.event}:{self.sample_ms}:{self.start}:{self.end}"


def census(events: list[tuple[int, int, str]]) -> list[Coordinate]:
    result = []
    for index, (start, end, text) in enumerate(events):
        sample = start + max(0, end - start) // 2
        ranges = clusters(text) or [(0, 0)]
        for left, right in ranges:
            reason = (
                "invalid-timing"
                if end <= start
                else "non-ink"
                if not text[left:right].strip()
                else ""
            )
            result.append(
                Coordinate(index, sample, left, right, is_kanji(text[left:right]), reason)
            )
    return result


def manifest(events: list[tuple[int, int, str]], provenance: dict) -> dict:
    coordinates = census(events)
    rows = [
        {**asdict(item), "key": item.key, "text": events[item.event][2][item.start : item.end]}
        for item in coordinates
    ]
    result = {
        "schema": 2,
        "oracle_contract": ORACLE_CONTRACT,
        "provenance": json.loads(json.dumps(provenance)),
        "events": len(events),
        "coordinates": rows,
        "census_sha256": digest([item.key for item in coordinates]),
        "sampling": "one interior midpoint per event; animation states not qualified",
        "capture_estimate_upper_bound": len(coordinates) * 11,
    }
    return {**result, "manifest_sha256": digest(result)}


def reference_mask(
    original: np.ndarray, red: np.ndarray, blue: np.ndarray
) -> tuple[np.ndarray, str]:
    """Use the color probe only to identify an isolated cell of unchanged native ink."""
    mask = np.zeros(original.shape[:2], dtype=np.uint8)
    if not np.array_equal(original[:, :, 0], original[:, :, 1]):
        return mask, "native-reference-has-red-green-chroma"
    if np.any(red[:, :, 1:]) or np.any(blue[:, :, 1]):
        return mask, "non-primary-reference-channels"
    if not np.array_equal(original[:, :, 0], red[:, :, 0]):
        return mask, "whole-cue-recoloring-changes-native-ink"
    ys, xs = np.where(blue[:, :, 2])
    if not len(xs):
        return mask, "empty-reference-mask"
    ink = original[:, :, 0] > 0
    top, bottom = int(ys.min()), int(ys.max()) + 1
    while top > 0 and ink[top - 1].any():
        top -= 1
    while bottom < ink.shape[0] and ink[bottom].any():
        bottom += 1
    columns = ink[top:bottom].any(axis=0)
    other_columns = (blue[top:bottom, :, 0] > 0).any(axis=0)
    left, right = int(xs.min()), int(xs.max()) + 1
    while left > 0 and columns[left - 1] and not other_columns[left - 1]:
        left -= 1
    while right < ink.shape[1] and columns[right] and not other_columns[right]:
        right += 1
    region = np.s_[top:bottom, left:right]
    # Red inside this cell belongs to another character: touching/overlapping ink is ambiguous.
    if blue[:, :, 0][region].any():
        return mask, "native-ink-cell-has-other-characters"
    mask[region] = original[:, :, 0][region]
    return mask, ""


def green_coverage(composite: np.ndarray) -> np.ndarray:
    """When native R=G, G-R cancels the subtitle and leaves green overlay coverage."""
    return np.maximum(composite[:, :, 1].astype(np.int16) - composite[:, :, 0], 0).astype(np.uint8)


def isolation_preserves_ink(original: np.ndarray, isolated: np.ndarray) -> bool:
    coverage = np.minimum(isolated[:, :, 0].astype(np.uint16) + isolated[:, :, 2], 255)
    return bool(np.array_equal(original[:, :, 0], coverage))


def _components(mask: np.ndarray) -> list[np.ndarray] | None:
    pending = {tuple(int(value) for value in point) for point in np.argwhere(mask)}
    result = []
    height, width = mask.shape
    while pending:
        start = pending.pop()
        cells = [start]
        stack = [start]
        while stack:
            y, x = stack.pop()
            for yy in range(max(0, y - 1), min(height, y + 2)):
                for xx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = (yy, xx)
                    if neighbor in pending:
                        pending.remove(neighbor)
                        cells.append(neighbor)
                        stack.append(neighbor)
        result.append(np.asarray(cells, dtype=np.int32))
        if len(result) > MAX_ORACLE_COMPONENTS:
            return None
    return result


def _dilate(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1)
    return np.logical_or.reduce(
        [padded[y : y + mask.shape[0], x : x + mask.shape[1]] for y in range(3) for x in range(3)]
    )


def _erode(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1)
    return np.logical_and.reduce(
        [padded[y : y + mask.shape[0], x : x + mask.shape[1]] for y in range(3) for x in range(3)]
    )


def _centroid(values: np.ndarray, selected: np.ndarray) -> tuple[float, float] | None:
    weights = np.where(selected, values, 0).astype(float)
    total = float(weights.sum())
    if not total:
        return None
    ys, xs = np.indices(values.shape)
    return float((xs * weights).sum() / total), float((ys * weights).sum() / total)


def _opaque_geometry(reference: np.ndarray, actual: np.ndarray) -> dict:
    occupied = (reference > 0) | (actual > 0)
    occupied_count = int(occupied.sum())
    if occupied_count > MAX_ORACLE_INK_PIXELS:
        return {
            "failed": True,
            "inconclusive": True,
            "support_policy_violation_pixels": occupied_count - MAX_ORACLE_INK_PIXELS,
            "opaque_core_missing_pixels": 0,
            "opaque_core_added_pixels": 0,
            "component_failures": 1,
            "component_centroid_error": 0.0,
            "component_mean_alpha_error": 0.0,
        }
    if occupied.any():
        ys, xs = np.where(occupied)
        crop = np.s_[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]
        reference, actual = reference[crop], actual[crop]
    support = reference > 0
    visible = actual > 0
    lost = support & ~visible
    added = visible & ~support
    support_violations = int((lost & (reference > AA_SUPPORT_MAX)).sum()) + int(
        (added & ((actual > AA_SUPPORT_MAX) | ~_dilate(support))).sum()
    )
    support_violations += int((lost & ~_dilate(visible)).sum())
    opaque = reference == 255
    actual_opaque = actual == 255
    opaque_core = _erode(opaque)
    actual_core = _erode(actual_opaque)
    core_missing = int((opaque_core & ~actual_opaque).sum())
    core_added = int((actual_core & ~opaque).sum())
    component_failures = 0
    centroid_error = 0.0
    component_mean_error = 0.0
    components = _components(support)
    if components is None:
        return {
            "failed": True,
            "inconclusive": True,
            "support_policy_violation_pixels": 0,
            "opaque_core_missing_pixels": 0,
            "opaque_core_added_pixels": 0,
            "component_failures": 1,
            "component_centroid_error": 0.0,
            "component_mean_alpha_error": 0.0,
        }
    for coordinates in components:
        top, left = coordinates.min(axis=0)
        bottom, right = coordinates.max(axis=0) + 1
        top, left = max(0, int(top) - 1), max(0, int(left) - 1)
        bottom = min(reference.shape[0], int(bottom) + 1)
        right = min(reference.shape[1], int(right) + 1)
        component = np.zeros((bottom - top, right - left), dtype=bool)
        component[coordinates[:, 0] - top, coordinates[:, 1] - left] = True
        neighborhood = _dilate(component)
        reference_view = reference[top:bottom, left:right]
        actual_view = actual[top:bottom, left:right]
        candidate = (actual_view > 0) & neighborhood
        source_centroid = _centroid(reference_view, component)
        actual_centroid = _centroid(actual_view, candidate)
        if source_centroid is None or actual_centroid is None:
            component_failures += 1
            continue
        error = max(abs(a - b) for a, b in zip(source_centroid, actual_centroid, strict=True))
        centroid_error = max(centroid_error, error)
        comparison = component | candidate
        mean_error = float(
            np.abs(actual_view.astype(float) - reference_view.astype(float))[comparison].mean()
        )
        component_mean_error = max(component_mean_error, mean_error)
        component_failures += int(error > CENTROID_TOLERANCE or mean_error > AA_SUPPORT_MAX)
    return {
        "failed": bool(support_violations or core_missing or core_added or component_failures),
        "inconclusive": False,
        "support_policy_violation_pixels": support_violations,
        "opaque_core_missing_pixels": core_missing,
        "opaque_core_added_pixels": core_added,
        "component_failures": component_failures,
        "component_centroid_error": centroid_error,
        "component_mean_alpha_error": component_mean_error,
    }


def compare_mask(reference: np.ndarray, ours: np.ndarray, context: np.ndarray) -> dict:
    """Reference ownership is independent of our boxes; missing output never shrinks the mask."""
    if reference.shape != ours.shape or reference.shape != context.shape or reference.ndim != 2:
        raise ValueError("mask dimensions must match")
    target = reference > 0
    expected = int(target.sum())
    if not expected:
        return {"verdict": "inconclusive", "reason": "empty-reference-mask"}
    visible = ours > 0
    missed = int((target & ~visible).sum())
    ys, xs = np.where(target)
    region = np.zeros_like(target)
    region[
        max(0, int(ys.min()) - 2) : int(ys.max()) + 3, max(0, int(xs.min()) - 2) : int(xs.max()) + 3
    ] = True
    permitted = context > 0
    spill = int((region & visible & ~permitted).sum())
    frame_spill = int((visible & ~permitted).sum())
    alpha_error = float(np.abs(ours.astype(float) - reference.astype(float))[target].max())
    cue_target = context > 0
    frame_missing = int((cue_target & ~visible).sum())
    frame_error = float(np.abs(ours.astype(float) - context.astype(float))[cue_target].max())
    character_scope = _dilate(target) & ~(permitted & ~target)
    character = _opaque_geometry(reference, np.where(character_scope, ours, 0))
    cue = _opaque_geometry(context, ours)
    if character["inconclusive"] or cue["inconclusive"]:
        return {
            "verdict": "inconclusive",
            "reason": "oracle-ink-budget",
            "oracle_contract": ORACLE_CONTRACT,
        }
    character_failed = character["failed"] or (
        spill > 0 and bool(np.any(ours[region & visible & ~permitted] > AA_SUPPORT_MAX))
    )
    cue_failed = cue["failed"]
    return {
        "verdict": "failed" if character_failed or cue_failed else "passed",
        "reason": "pixel-disagreement" if character_failed or cue_failed else "",
        "character_verdict": "failed" if character_failed else "passed",
        "cue_verdict": "failed" if cue_failed else "passed",
        "reference_pixels": expected,
        "missing_pixels": missed,
        "spill_pixels": spill,
        "frame_spill_pixels": frame_spill,
        "frame_missing_pixels": frame_missing,
        "frame_intensity_error": frame_error,
        "exact_alpha": not bool(
            missed or spill or frame_missing or frame_spill or alpha_error or frame_error
        ),
        "oracle_contract": ORACLE_CONTRACT,
        "opaque_core_missing_pixels": character["opaque_core_missing_pixels"],
        "opaque_core_added_pixels": character["opaque_core_added_pixels"],
        "support_policy_violation_pixels": character["support_policy_violation_pixels"],
        "component_failures": character["component_failures"],
        "component_centroid_error": character["component_centroid_error"],
        "component_mean_alpha_error": character["component_mean_alpha_error"],
        "frame_opaque_core_missing_pixels": cue["opaque_core_missing_pixels"],
        "frame_opaque_core_added_pixels": cue["opaque_core_added_pixels"],
        "frame_support_policy_violation_pixels": cue["support_policy_violation_pixels"],
        "frame_component_failures": cue["component_failures"],
        "frame_component_centroid_error": cue["component_centroid_error"],
        "frame_component_mean_alpha_error": cue["component_mean_alpha_error"],
        "coverage": (expected - missed) / expected,
        "intensity_error": alpha_error,
        "reference_centroid": [float(xs.mean()), float(ys.mean())],
    }


def results_summary(frozen: dict, results: dict[str, dict]) -> dict:
    rows = [
        {
            **coordinate,
            **results.get(coordinate["key"], {"verdict": "unattempted", "reason": "not-executed"}),
        }
        for coordinate in frozen["coordinates"]
    ]
    verdicts = ("passed", "failed", "unsupported", "inconclusive", "unattempted")
    return {
        "schema": 2,
        "oracle_contract": ORACLE_CONTRACT,
        "manifest_sha256": frozen["manifest_sha256"],
        "total": len(rows),
        "counts": {verdict: sum(row["verdict"] == verdict for row in rows) for verdict in verdicts},
        "kanji": {
            verdict: sum(row["kanji"] and row["verdict"] == verdict for row in rows)
            for verdict in verdicts
        },
        "results": rows,
    }


def qualification_counts(frozen: dict, result: dict) -> dict:
    """Account executed controls separately from reported fidelity; never shrink the census."""
    identity = frozen.get("manifest_sha256")
    if (
        type(frozen.get("schema")) is not int
        or frozen["schema"] != 2
        or frozen.get("oracle_contract") != ORACLE_CONTRACT
        or identity
        != digest({key: value for key, value in frozen.items() if key != "manifest_sha256"})
        or result.get("manifest_sha256") != identity
        or result.get("schema") != 2
        or result.get("oracle_contract") != ORACLE_CONTRACT
    ):
        raise ValueError("qualification manifest mismatch")
    coordinates, rows = frozen.get("coordinates"), result.get("results")
    if not isinstance(coordinates, list) or not isinstance(rows, list):
        raise TypeError("qualification census missing")
    keys = [row.get("key") for row in coordinates if isinstance(row, dict)]
    observed = [row.get("key") for row in rows if isinstance(row, dict)]
    if (
        len(keys) != len(coordinates)
        or len(observed) != len(rows)
        or not all(isinstance(key, str) for key in [*keys, *observed])
        or len(set(keys)) != len(keys)
        or len(set(observed)) != len(observed)
        or set(keys) != set(observed)
    ):
        raise ValueError("qualification census differs")
    counts = dict.fromkeys(("passed", "failed", "unsupported", "inconclusive", "unattempted"), 0)
    controlled = 0
    non_ink = {row["key"] for row in coordinates if row.get("reason") == "non-ink"}
    excluded = 0
    for row in rows:
        verdict = row.get("verdict")
        if verdict not in counts:
            raise ValueError("qualification verdict missing")
        valid_controls = row.get("controls") == FAULT_CONTROLS
        controlled += valid_controls
        if verdict in {"passed", "failed"} and not valid_controls:
            verdict = "inconclusive"
        if verdict == "passed" and not _opaque_result(row):
            verdict = "inconclusive"
        counts[verdict] += 1
        excluded += (
            row["key"] in non_ink and verdict == "unsupported" and row.get("reason") == "non-ink"
        )
    return {
        "scope": "supplied local character artifact; structural validation, not a new render",
        "denominator": len(keys),
        "required_ink_coordinates": len(keys) - len(non_ink),
        "accounted_non_ink": excluded,
        "oracle_execution": {
            "valid_controls": controlled,
            "without_valid_controls": len(keys) - controlled,
        },
        "fidelity": counts,
        "fault_controls": {
            "displacement": controlled,
            "missing_stroke": controlled,
            "absent_output": controlled,
            "wrong_color": controlled,
        },
        "qualified": bool(keys)
        and counts["passed"] == len(keys) - len(non_ink)
        and excluded == len(non_ink),
    }


def _opaque_result(row: dict) -> bool:
    return (
        row.get("oracle_contract") == ORACLE_CONTRACT
        and row.get("character_verdict") == row.get("cue_verdict") == "passed"
        and type(row.get("reference_pixels")) is int
        and row["reference_pixels"] > 0
        and all(
            type(row.get(key)) in {int, float} and row[key] == 0
            for key in (
                "opaque_core_missing_pixels",
                "opaque_core_added_pixels",
                "frame_opaque_core_missing_pixels",
                "frame_opaque_core_added_pixels",
                "support_policy_violation_pixels",
                "component_failures",
                "frame_support_policy_violation_pixels",
                "frame_component_failures",
            )
        )
        and all(
            type(row.get(key)) in {int, float} and row[key] <= CENTROID_TOLERANCE
            for key in ("component_centroid_error", "frame_component_centroid_error")
        )
        and all(
            type(row.get(key)) in {int, float} and row[key] <= AA_SUPPORT_MAX
            for key in ("component_mean_alpha_error", "frame_component_mean_alpha_error")
        )
    )
