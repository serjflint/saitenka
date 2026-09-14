"""Local character-mask census and strict pixel oracle. Inputs and images are never uploaded."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass

import numpy as np


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
        "schema": 1,
        "provenance": json.loads(json.dumps(provenance)),
        "events": len(events),
        "coordinates": rows,
        "census_sha256": digest([item.key for item in coordinates]),
        "sampling": "one interior midpoint per event; animation states not qualified",
        "capture_estimate_upper_bound": len(coordinates) * 9,
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
    left, right = int(xs.min()), int(xs.max()) + 1
    while left > 0 and columns[left - 1]:
        left -= 1
    while right < ink.shape[1] and columns[right]:
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


def compare_mask(reference: np.ndarray, ours: np.ndarray, context: np.ndarray) -> dict:
    """Reference ownership is independent of our boxes; missing output never shrinks the mask."""
    if reference.shape != ours.shape or reference.shape != context.shape or reference.ndim != 2:
        raise ValueError("mask dimensions must match")
    target = reference > 32
    expected = int(target.sum())
    if not expected:
        return {"verdict": "inconclusive", "reason": "empty-reference-mask"}
    visible = ours > 32
    missed = int((target & ~visible).sum())
    ys, xs = np.where(target)
    region = np.zeros_like(target)
    region[
        max(0, int(ys.min()) - 2) : int(ys.max()) + 3, max(0, int(xs.min()) - 2) : int(xs.max()) + 3
    ] = True
    permitted = context > 32
    spill = int((region & visible & ~permitted).sum())
    frame_spill = int((visible & ~permitted).sum())
    alpha_error = float(np.abs(ours.astype(float) - reference.astype(float))[target].max())
    cue_target = context > 32
    frame_missing = int((cue_target & ~visible).sum())
    frame_error = float(np.abs(ours.astype(float) - context.astype(float))[cue_target].max())
    character_failed = missed > 0 or spill > 0 or alpha_error > 0
    cue_failed = frame_missing > 0 or frame_spill > 0 or frame_error > 0
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
        "manifest_sha256": frozen["manifest_sha256"],
        "total": len(rows),
        "counts": {verdict: sum(row["verdict"] == verdict for row in rows) for verdict in verdicts},
        "kanji": {
            verdict: sum(row["kanji"] and row["verdict"] == verdict for row in rows)
            for verdict in verdicts
        },
        "results": rows,
    }
