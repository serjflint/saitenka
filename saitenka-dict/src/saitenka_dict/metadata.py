from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FrequencyValue:
    reading: str | None
    value: int | float | None
    display: str | None


def parse_frequency(data: Any) -> FrequencyValue:
    """One ``freq`` term_meta value → ``(reading, rank, display)``.

    Covers the shapes seen in the wild: a plain number, ``{"value", "displayValue"}``,
    ``{"reading", "frequency"}``, and the JLPT ``{"frequency": {"value": -1, "displayValue": "N5"}}``
    sentinel. A *string* value yields its trailing parenthesised number if it has one
    (``"twenty-four (24)"`` → 24), else its LEADING integer — ``"118,121"`` is 118, never the
    comma-stripped 118121, because a grouped ``"rank, occurrences"`` display puts the rank first.

    A value with no number in it has **no rank** (``None``, not ``0``): every consumer treats the rank
    as an ordinal, so a synthetic ``0`` would read as "more frequent than everything". ``bool`` is
    rejected for the same reason — ``True`` is not rank 1.
    """
    reading: str | None = None
    display: str | None = None
    value = data
    if isinstance(data, dict):
        reading = data.get("reading")
        value = data.get("frequency", data)
        if isinstance(value, dict):
            display = value.get("displayValue")
            value = value.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return FrequencyValue(reading, value, display)
    if isinstance(value, str):
        match = re.search(r"\((-?\d+(?:\.\d+)?)\)\s*$", value) or re.match(
            r"\s*(-?\d+(?:\.\d+)?)", value
        )
        if match is None:
            return FrequencyValue(reading, None, display or value)
        number = float(match.group(1)) if "." in match.group(1) else int(match.group(1))
        return FrequencyValue(reading, number, display or value)
    return FrequencyValue(reading, None, display)
