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
        number = (
            float(match.group(1))
            if match is not None and "." in match.group(1)
            else int(match.group(1))
            if match is not None
            else 0
        )
        return FrequencyValue(reading, number, display or value)
    return FrequencyValue(reading, None, display)
