"""Subtitle-provider registry — language capability + fetch dispatch.

Each provider declares the language(s) it serves; callers ask "which providers are available for
language L" instead of branching on provider identity (``if provider == "jimaku"``). A provider with
an empty ``languages`` set is language-agnostic and always available.

Leaf module (no ``subselect`` import — the registry direction is one-way, ``subselect`` → here, to keep
``saitenka.app`` acyclic). The built-in jimaku/tsukihime providers self-register at ``subselect`` import;
launch paths import ``subselect`` before querying the registry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.subselect import SubtitleCandidate

ProviderFetch = Callable[[], tuple[Path | None, str]]


@dataclass(frozen=True)
class ProviderContext:
    """Shared inputs a provider's candidates/fetch step may need. Kept uniform across providers (not
    every field is read by every provider) so registry dispatch needs no per-provider signature."""

    jimaku_key: str | None = None
    title_override: str | None = None
    tsukihime_config: dict | None = None
    episode: int | None = None
    resync: bool = True
    force: bool = False


CandidatesFn = Callable[[str, ProviderContext], "tuple[list[SubtitleCandidate], list[str]]"]
FetchAttemptFn = Callable[[str, ProviderContext], ProviderFetch]


@dataclass(frozen=True)
class SubtitleProvider:
    """A subtitle source's capability + dispatch — a registry entry, not an ``if provider ==`` branch."""

    name: str
    languages: frozenset[str]
    candidates: CandidatesFn
    fetch_attempt: FetchAttemptFn


_REGISTRY: dict[str, SubtitleProvider] = {}


def register_provider(provider: SubtitleProvider) -> None:
    if (
        provider.name in _REGISTRY
    ):  # loud-on-mistake: a silent overwrite hides a double-register bug
        raise ValueError(f"subtitle provider {provider.name!r} already registered")
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> SubtitleProvider | None:
    return _REGISTRY.get(name)


def providers_for_language(
    language: str, *, candidates: Iterable[str] | None = None
) -> tuple[str, ...]:
    """Registered provider names available for ``language``, preserving ``candidates`` order (default:
    registry order). A provider not yet registered is silently dropped."""
    names = candidates if candidates is not None else tuple(_REGISTRY)
    return tuple(
        name
        for name in names
        if (p := _REGISTRY.get(name)) is not None and (not p.languages or language in p.languages)
    )


def enabled_providers_for(language: str, flags: Iterable[tuple[str, bool]]) -> tuple[str, ...]:
    """Turn ``(provider_name, enabled)`` config flags into the enabled-and-language-eligible provider
    tuple — the one place that replaces the duplicated enablement tuples in both launch paths."""
    configured = tuple(name for name, enabled in flags if enabled)
    return providers_for_language(language, candidates=configured)


def fetch_first(attempts: Iterable[tuple[str, ProviderFetch]]) -> tuple[Path | None, str]:
    """Try providers in order and return the first success with combined failure context."""
    failures: list[str] = []
    for _name, fetch in attempts:
        path, status = fetch()
        if path is not None:
            return path, status
        failures.append(status)
    return None, "; ".join(failures) or "no Japanese subtitle providers enabled"
