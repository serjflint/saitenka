from pathlib import Path

import saitenka.app.subselect  # noqa: F401  # import registers the built-in providers before any patch
from saitenka.app.subtitle_providers import (
    ProviderContext,
    SubtitleProvider,
    enabled_providers_for,
    fetch_first,
    get_provider,
    providers_for_language,
    register_provider,
)


def _stub_provider(name: str, languages: frozenset[str]) -> SubtitleProvider:
    def candidates(_video: str, _ctx: ProviderContext) -> tuple[list, list]:
        return [], []

    def fetch_attempt(_video: str, _ctx: ProviderContext):
        return lambda: (None, f"{name}: stub")

    return SubtitleProvider(
        name=name, languages=languages, candidates=candidates, fetch_attempt=fetch_attempt
    )


def test_provider_chain_stops_at_first_success():
    calls: list[str] = []

    def miss():
        calls.append("jimaku")
        return None, "jimaku: miss"

    def hit():
        calls.append("tsukihime")
        return Path("episode.ja.ass"), "tsukihime: added"

    path, status = fetch_first((("jimaku", miss), ("tsukihime", hit)))

    assert calls == ["jimaku", "tsukihime"]
    assert path == Path("episode.ja.ass") and status == "tsukihime: added"


def test_provider_chain_does_not_call_later_provider_after_success():
    calls: list[str] = []

    def hit():
        calls.append("jimaku")
        return Path("episode.ja.srt"), "jimaku: added"

    def unexpected():
        calls.append("tsukihime")
        return None, "unexpected"

    path, _status = fetch_first((("jimaku", hit), ("tsukihime", unexpected)))

    assert path == Path("episode.ja.srt")
    assert calls == ["jimaku"]


def test_empty_provider_chain_performs_no_request():
    assert fetch_first(()) == (None, "no Japanese subtitle providers enabled")


def _isolated_registry(monkeypatch):
    """Registration mutates a module-level dict — patch it to a fresh copy so a test's stub
    providers never leak into another test (AGENTS.md: no unrestored shared mutable state)."""
    import saitenka.app.subtitle_providers as mod

    monkeypatch.setattr(mod, "_REGISTRY", {})
    return mod


def test_providers_for_language_filters_by_capability(monkeypatch):
    mod = _isolated_registry(monkeypatch)
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    register_provider(_stub_provider("tsukihime", frozenset({"jp"})))
    register_provider(_stub_provider("universal", frozenset()))  # language-agnostic

    assert set(mod.providers_for_language("jp")) == {"jimaku", "tsukihime", "universal"}
    assert mod.providers_for_language("fr") == ("universal",)


def test_provider_capabilities_match_language_aliases_and_regions(monkeypatch):
    mod = _isolated_registry(monkeypatch)
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    register_provider(_stub_provider("german", frozenset({"de"})))

    assert mod.providers_for_language("JA-jp") == ("jimaku",)
    assert mod.providers_for_language("de-CH") == ("german",)


def test_providers_for_language_drops_unregistered_candidate(monkeypatch):
    _isolated_registry(monkeypatch)
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))

    assert providers_for_language("jp", candidates=("jimaku", "ghost")) == ("jimaku",)


def test_enabled_providers_for_combines_config_flag_and_language_capability(monkeypatch):
    _isolated_registry(monkeypatch)
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    register_provider(_stub_provider("tsukihime", frozenset({"jp"})))

    # both configured "on", but only jp providers qualify for a jp session:
    assert enabled_providers_for("jp", (("jimaku", True), ("tsukihime", True))) == (
        "jimaku",
        "tsukihime",
    )
    # an unknown/other main language excludes both jp-only providers, even when configured on:
    assert enabled_providers_for("fr", (("jimaku", True), ("tsukihime", True))) == ()
    # a provider left off in config never appears regardless of language:
    assert enabled_providers_for("jp", (("jimaku", False), ("tsukihime", True))) == ("tsukihime",)


def test_get_provider_returns_none_for_unregistered_name(monkeypatch):
    _isolated_registry(monkeypatch)
    assert get_provider("jimaku") is None


def test_register_provider_rejects_a_duplicate_name(monkeypatch):
    import pytest

    _isolated_registry(monkeypatch)
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    with pytest.raises(ValueError, match="already registered"):
        register_provider(_stub_provider("jimaku", frozenset({"fr"})))


def test_real_registry_offers_jimaku_and_tsukihime_for_japanese():
    """Characterization: importing subselect registers the real jimaku/tsukihime providers, and
    both remain available for 'jp' — the #254 phase-1 rewrite must not change JP behaviour."""
    import saitenka.app.subselect as _  # noqa: F401  # side-effecting import: populates the registry

    assert set(providers_for_language("jp", candidates=("jimaku", "tsukihime"))) == {
        "jimaku",
        "tsukihime",
    }
    assert get_provider("jimaku") is not None


def test_run_launch_registers_builtin_providers_from_an_empty_registry(monkeypatch):
    from saitenka.app.launch import run

    _isolated_registry(monkeypatch)

    assert run._enabled_provider_names(
        "episode.mkv",
        jimaku=True,
        jimaku_cfg={},
        tsukihime_cfg={"enabled": True},
        language="jp",
    ) == ("jimaku", "tsukihime")
    assert get_provider("tsukihime") is not None
