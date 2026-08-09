"""End-to-end identity agreement (#254): the SAME active language must drive tokenizer selection AND
provider gating consistently through the REAL run/attach wiring — the metamorphic invariant the P1
violated (a ``ja`` alias resolved ``unidic`` while providers silently dropped, because per-component unit
tests never asserted the two AGREE through the real selection path).

These assert OBSERVABLE selection — the constructed ``reader.tokenizer``/``reader.langs`` and the provider
tuple ``prepare_attach_startup`` actually returns — not ``resolve_profile`` in isolation. Default tier: the
only boundary crossed is the in-process ``FakeIPC`` (the project's ``integration`` marker is for a real
subprocess/socket/filesystem, which these don't touch — mirrors test_subselect / test_tokenizer).
"""

from __future__ import annotations

import pytest
from overlay.app import subselect
from overlay.app.controller import Reader
from overlay.app.profiles import resolve_profile
from overlay.app.subtitle_providers import (
    ProviderContext,
    SubtitleProvider,
    enabled_providers_for,
    register_provider,
)
from overlay.app.tokenizer import register_tokenizer

EN = {"id": 1, "type": "sub", "lang": "eng"}  # English-only file → no JP track, so the gate runs


class _FakeIPC:
    """Serves track-list/path and records commands — the same shape test_subselect drives
    prepare_attach_startup through, so this exercises the REAL attach selection, not a stub of it."""

    def __init__(self, tracks=None, path=None):
        self._tracks = tracks or []
        self._path = path
        self.calls: list[tuple] = []

    def command(self, *args):
        self.calls.append(args)
        if args[:2] == ("get_property", "track-list"):
            return {"data": self._tracks}
        if args[:2] == ("get_property", "path"):
            return {"data": self._path}
        return {"data": None}


class _FakeLatin:
    """A non-JP tokenizer strategy a profile can select — provably NOT unidic."""

    name = "latin"

    def tokenize(self, _line, *, _strip_furigana=True, _merge=True):
        return []

    def is_content(self, _token):
        return True

    def is_skippable(self, token):
        return not token.surface.strip()

    def query_token(self, _query):
        return None

    def inflected_in(self, tokens, index):
        return tokens[index].surface

    def phrase_terms(self, _tokens, _index, _has_term):
        return None

    def merge_dict_compounds(self, tokens, _exists):
        return tokens


def _stub_provider(name, languages):
    def candidates(_video, _ctx: ProviderContext):
        return [], []

    def fetch_attempt(_video, _ctx: ProviderContext):
        return lambda: (None, f"{name}: stub")

    return SubtitleProvider(
        name=name, languages=languages, candidates=candidates, fetch_attempt=fetch_attempt
    )


@pytest.fixture
def _restore_tokenizer_registry():
    import overlay.app.tokenizer as mod

    saved = dict(mod._FACTORIES)
    yield
    mod._FACTORIES.clear()
    mod._FACTORIES.update(saved)


def _resolve_identity(cfg: dict) -> tuple[str, str, tuple[str, ...]]:
    """Reproduce how ``run``/``attach`` resolve the reading identity, through the REAL wiring, and
    return what each component OBSERVABLY selected: (active tokenizer name, main language code, the
    enabled attach providers). ``resolve_profile`` → the real ``Reader`` construction line → the real
    ``prepare_attach_startup`` provider gate, all keyed on the one resolved ``langs.main``."""
    profile = resolve_profile(cfg)  # exactly what run_impl / attach do after load_config
    reader = Reader(_FakeIPC(), profile=profile)  # the real Reader(ipc, options, profile=…) line
    ipc = _FakeIPC(tracks=[EN], path="/v/Show - 01.mkv")  # no JP track → the provider gate fires
    _startup, _status, providers = subselect.prepare_attach_startup(
        ipc,
        subselect.AttachSubtitleOptions(jimaku=True, tsukihime=True, language=profile.langs.main),
    )
    return reader.tokenizer.name, reader.langs.main, providers


# --- 1. default scenario: the JP identity resolves consistently, byte-identical to today ----------


def test_default_scenario_resolves_the_japanese_identity_end_to_end():
    """No [profile] configured → the whole JP identity agrees through the real path: unidic tokenizer,
    jp language, jimaku+tsukihime providers. The characterization control for #254."""
    tokenizer, main, providers = _resolve_identity({})
    assert tokenizer == "unidic"
    assert main == "jp"
    assert providers == ("jimaku", "tsukihime")


# --- 2. cross-component consistency oracle: every JP alias ≡ canonical "jp" end-to-end -------------


@pytest.mark.parametrize("alias", ["jp", "ja", "jpn", "japanese"])
def test_jp_aliases_yield_the_identical_identity_as_canonical_jp(alias):
    """Metamorphic invariant (the direct P1 guard): a profile written with ANY JP alias must drive the
    tokenizer AND the provider gate to the SAME end-to-end identity as the canonical ``jp`` — there is
    no configuration where the tokenizer resolves JP but providers resolve non-JP (or vice-versa)."""
    canonical = _resolve_identity({})
    aliased = _resolve_identity({"profile": {"language": alias}})
    assert aliased == canonical == ("unidic", "jp", ("jimaku", "tsukihime"))


# --- 3. negative control: a non-JP profile yields a DISTINCT identity (the oracle has teeth) -------


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_non_jp_profile_yields_a_distinct_identity_end_to_end():
    """Proves the oracle distinguishes rather than always-passing: a French profile selects the latin
    tokenizer (NOT unidic) AND the jp-only jimaku/tsukihime providers drop out — end-to-end."""
    register_tokenizer("latin", _FakeLatin)
    tokenizer, main, providers = _resolve_identity(
        {"profile": {"language": "fr", "tokenizer": "latin"}}
    )
    assert tokenizer == "latin"  # distinct from the JP unidic
    assert main == "fr"
    assert providers == ()  # jp-only providers gated out under a non-JP language


def test_non_jp_language_keeps_language_agnostic_providers(monkeypatch):
    """The shared gating SSOT both run and attach call (``enabled_providers_for``, keyed on the resolved
    ``langs.main``) drops the jp-only providers under a non-JP profile but KEEPS a language-agnostic
    one — so a non-JP profile is served by agnostic providers, not left with nothing by construction."""
    import overlay.app.subtitle_providers as mod

    monkeypatch.setattr(mod, "_REGISTRY", {})
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    register_provider(_stub_provider("universal", frozenset()))  # language-agnostic

    fr = resolve_profile({"profile": {"language": "fr", "tokenizer": "latin"}}).langs.main
    assert enabled_providers_for(fr, (("jimaku", True), ("universal", True))) == ("universal",)
