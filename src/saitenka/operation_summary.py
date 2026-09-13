"""Bounded text-free operation census, including installations without the tracing extra."""

from __future__ import annotations

import threading
from collections import Counter


class OperationSummary:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Counter[str] = Counter()
        self._outcomes: Counter[tuple[str, str]] = Counter()

    def start(self, name: str) -> str:
        with self._lock:
            key = name if name in self._pending or len(self._pending) < 128 else "other"
            self._pending[key] += 1
            return key

    def finish(self, name: str, outcome: str) -> None:
        with self._lock:
            self._pending[name] -= 1
            key = (name, outcome)
            if key not in self._outcomes and len(self._outcomes) >= 256:
                key = ("other", "other")
            self._outcomes[key] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "pending": {name: count for name, count in self._pending.items() if count},
                "outcomes": [
                    {"operation": name, "outcome": outcome, "count": count}
                    for (name, outcome), count in sorted(self._outcomes.items())
                ],
                "scope": "deferred-operation boundaries; not all product operations",
            }


operations = OperationSummary()
