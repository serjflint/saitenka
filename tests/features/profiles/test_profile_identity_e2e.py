"""Profile aliases agree across reader construction and attach-provider selection.

The helper composes the real seams; it does not invoke run or attach commands.
"""

from __future__ import annotations

import pytest
import util
from saitenka_tokenize.registry import register_tokenizer
from session_builder import build_session

from saitenka.app import subselect
from saitenka.app.profiles import resolve_profile
from saitenka.app.session.factory import SessionIdentity

EN = {"id": 1, "type": "sub", "lang": "eng"}  # English-only file → no JP track, so the gate runs


class _FakeIPC(util.FakeIPC):
    """Gateway-wired, serving track-list/path — the same fake test_subselect drives
    prepare_attach_startup through, so this exercises the REAL attach selection, not a stub of it."""

    def __init__(self, tracks=None, path=None):
        super().__init__()
        self.props["track-list"] = tracks or []
        self.props["path"] = path
        util.session_gateway(self)

    @property
    def calls(self) -> list[tuple]:
        return self.commands


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


@pytest.fixture
def _restore_tokenizer_registry():
    import saitenka_tokenize.registry as mod

    saved = dict(mod._FACTORIES)
    yield
    mod._FACTORIES.clear()
    mod._FACTORIES.update(saved)


def _resolve_identity(cfg: dict) -> tuple[str, str, tuple[str, ...]]:
    profile = resolve_profile(cfg)
    reader = build_session(_FakeIPC(), identity=SessionIdentity(profile=profile))
    ipc = _FakeIPC(tracks=[EN], path="/v/Show - 01.mkv")  # no JP track → the provider gate fires
    _startup, _status, providers = subselect.prepare_attach_startup(
        ipc,
        subselect.AttachSubtitleOptions(jimaku=True, tsukihime=True, language=profile.langs.main),
    )
    return (
        reader.graph.profile.profile.tokenizer.name,
        reader.graph.profile.profile.langs.main,
        providers,
    )


@pytest.mark.parametrize("alias", ["jp", "ja", "jpn", "japanese"])
def test_jp_aliases_yield_the_identical_identity_as_canonical_jp(alias):
    """Metamorphic invariant (the direct P1 guard): a profile written with ANY JP alias must drive the
    tokenizer AND the provider gate to the SAME end-to-end identity as the canonical ``jp`` — there is
    no configuration where the tokenizer resolves JP but providers resolve non-JP (or vice-versa)."""
    canonical = _resolve_identity({})
    aliased = _resolve_identity({"profile": {"language": alias}})
    assert aliased == canonical == ("unidic", "jp", ("jimaku", "tsukihime"))


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_non_jp_profile_yields_a_distinct_identity_end_to_end():
    """Proves the oracle distinguishes rather than always-passing: a French profile selects the latin
    tokenizer (NOT unidic) AND the jp-only jimaku provider drops out — end-to-end. TsukiHime survives
    because it is language-agnostic (#495), which is the point: the gate keys on capability, not on a
    provider name."""
    register_tokenizer("latin", _FakeLatin)
    tokenizer, main, providers = _resolve_identity(
        {"profile": {"language": "fr", "tokenizer": "latin"}}
    )
    assert tokenizer == "latin"  # distinct from the JP unidic
    assert main == "fr"
    assert providers == ("tsukihime",)  # jp-only jimaku gated out; the agnostic source remains
