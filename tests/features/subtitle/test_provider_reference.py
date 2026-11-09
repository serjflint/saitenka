"""Provider order, language eligibility, and fallbacks through the provider API."""

import json
from pathlib import Path

import pytest

from saitenka.app import subtitle_providers as providers
from saitenka.app.launch import run
from saitenka.app.subtitle_providers import ProviderContext, SubtitleProvider

DATA = json.loads((Path(__file__).parents[2] / "fixtures/subtitle_reference.json").read_text())


@pytest.fixture
def provider_registry(monkeypatch):
    monkeypatch.setattr(providers, "_REGISTRY", {})

    def install(items):
        for item in items:
            providers.register_provider(
                SubtitleProvider(
                    name=item["name"],
                    languages=frozenset(item["languages"]),
                    candidates=lambda _video, _context: ([], []),
                    fetch_attempt=lambda _video, _context: lambda: (None, "stub"),
                )
            )

    return install


@pytest.mark.parametrize("record", DATA["fetch-first"], ids=lambda r: r["id"])
def test_provider_fetch_falls_back_in_order(record):
    calls = []

    def attempt(item):
        def fetch():
            calls.append(item["name"])
            return (Path(item["path"]) if item["path"] is not None else None), item["status"]

        return item["name"], fetch

    path, status = providers.fetch_first(
        tuple(attempt(item) for item in record["input"]["attempts"])
    )
    assert {
        "calls": calls,
        "path": str(path) if path is not None else None,
        "status": status,
    } == record["expected"]


@pytest.mark.parametrize("record", DATA["providers-for-language"], ids=lambda r: r["id"])
def test_provider_language_order(provider_registry, record):
    v = record["input"]
    provider_registry(v["providers"])
    candidates = v.get("candidates")
    assert (
        list(
            providers.providers_for_language(
                v["language"], candidates=tuple(candidates) if candidates is not None else None
            )
        )
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["enabled-providers"], ids=lambda r: r["id"])
def test_disabled_providers_are_not_selected(provider_registry, record):
    v = record["input"]
    provider_registry(v["providers"])
    assert (
        list(providers.enabled_providers_for(v["language"], tuple((n, e) for n, e in v["flags"])))
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["get-provider"], ids=lambda r: r["id"])
def test_unknown_provider_is_absent(provider_registry, record):
    provider_registry(record["input"]["providers"])
    assert (providers.get_provider(record["input"]["name"]) is not None) == record["expected"]


@pytest.mark.parametrize("record", DATA["duplicate-register"], ids=lambda r: r["id"])
def test_duplicate_provider_is_rejected(provider_registry, record):
    provider_registry(record["input"]["providers"])
    with pytest.raises(ValueError):
        provider_registry([record["input"]["duplicate"]])


@pytest.mark.parametrize("record", DATA["launch-builtins"], ids=lambda r: r["id"])
def test_launch_registers_only_eligible_builtin_providers(provider_registry, record):
    provider_registry([])
    v = record["input"]
    names = run._enabled_provider_names(
        "episode.mkv",
        jimaku=v["jimaku"],
        jimaku_cfg={},
        tsukihime_cfg={"enabled": v["tsukihime"]},
        language=v["language"],
    )
    assert {
        "names": list(names),
        "tsukihime_registered": providers.get_provider("tsukihime") is not None,
    } == record["expected"]


def test_default_provider_context_language():
    assert ProviderContext().language == DATA["context-default"][0]["expected"]
