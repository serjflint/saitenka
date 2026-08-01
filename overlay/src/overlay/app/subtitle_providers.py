"""Provider-neutral ordering for Japanese subtitle fallbacks."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

ProviderFetch = Callable[[], tuple[Path | None, str]]


def fetch_first(attempts: Iterable[tuple[str, ProviderFetch]]) -> tuple[Path | None, str]:
    """Try providers in order and return the first success with combined failure context."""
    failures: list[str] = []
    for _name, fetch in attempts:
        path, status = fetch()
        if path is not None:
            return path, status
        failures.append(status)
    return None, "; ".join(failures) or "no Japanese subtitle providers enabled"
