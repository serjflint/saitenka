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
        "capture_estimate_upper_bound": len(coordinates) * 7,
    }
    return {**result, "manifest_sha256": digest(result)}


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
    # The production overprint has a one-pixel border. Account for that decoration, not a shifted glyph.
    padded = np.pad(context > 32, 1)
    permitted = np.logical_or.reduce(
        [
            padded[y : y + target.shape[0], x : x + target.shape[1]]
            for y in range(3)
            for x in range(3)
        ]
    )
    spill = int((region & visible & ~permitted).sum())
    frame_spill = int((visible & ~permitted).sum())
    alpha_error = float(np.abs(ours.astype(float) - reference.astype(float))[target].max())
    failed = missed > 0 or frame_spill > 0 or alpha_error > 0
    return {
        "verdict": "failed" if failed else "passed",
        "reason": "pixel-disagreement" if failed else "",
        "reference_pixels": expected,
        "missing_pixels": missed,
        "spill_pixels": spill,
        "frame_spill_pixels": frame_spill,
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
