"""Assembled-session wiring for resolved reading profiles."""

import pytest
import util
from saitenka_tokenize.registry import register_tokenizer

from saitenka.app import bindings as app_bindings
from saitenka.app.profiles import resolve_profile
from saitenka.app.session.factory import SessionIdentity


class FakeIPC(util.FakeIPC):
    def __init__(self, props=None):
        super().__init__()
        self.props.update(props or {})


class _FakeLatinTokenizer:
    """A stand-in non-JP strategy so a profile can select a tokenizer that is provably NOT unidic."""

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


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_reader_uses_the_active_profiles_tokenizer_and_languages(make_session):
    register_tokenizer("latin", _FakeLatinTokenizer)
    profile = resolve_profile(
        {"active_profile": "fr", "profiles": {"fr": {"language": "fr", "tokenizer": "latin"}}}
    )
    reader = make_session(FakeIPC(), identity=SessionIdentity(profile=profile))
    assert isinstance(
        reader.graph.profile.profile.tokenizer, _FakeLatinTokenizer
    )  # selected, not unidic
    assert (
        reader.graph.profile.profile.langs.main == "fr"
        and reader.graph.profile.profile.langs.second == "en"
    )
    assert reader.graph.profile.profile.profile == profile


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_profile_rescopes_the_dict_set_live(make_session):
    """A live profile cycle (#254 W3) swaps not just the tokenizer/langs but the dictionary set — the
    scoper the CLI installs is consulted and its result replaces reader.dict_set."""
    register_tokenizer("latin", _FakeLatinTokenizer)
    jp = resolve_profile({})  # default (jp/unidic)
    fr = resolve_profile({"profile": {"language": "fr", "tokenizer": "latin"}})
    jp_dicts, fr_dicts = object(), object()  # sentinels — cycle must select by profile

    reader = make_session(FakeIPC(), identity=SessionIdentity(profile=jp))
    reader.graph.profile.profile.replace_dictionary_set(jp_dicts)
    reader.graph.profile.profile.configure_cycle(
        [jp, fr], lambda p: fr_dicts if p.langs.main == "fr" else jp_dicts
    )

    reader.command(app_bindings.PROFILE_CYCLE_MSG)

    assert reader.graph.profile.profile.profile == fr
    assert reader.graph.profile.profile.langs.main == "fr"
    assert (
        reader.graph.profile.profile.dict_set is fr_dicts
    )  # rescoped, not left on the JP dict set


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_profile_without_a_scoper_keeps_the_dict_set(make_session):
    """No scoper installed (the pre-W3 wiring / single-DB path) → a cycle leaves dict_set untouched,
    so the switcher stays backward-compatible."""
    register_tokenizer("latin", _FakeLatinTokenizer)
    jp = resolve_profile({})
    fr = resolve_profile({"profile": {"language": "fr", "tokenizer": "latin"}})
    reader = make_session(FakeIPC(), identity=SessionIdentity(profile=jp))
    sentinel = object()
    reader.graph.profile.profile.replace_dictionary_set(sentinel)
    reader.graph.profile.profile.configure_cycle([jp, fr])  # no dict_scoper

    reader.command(app_bindings.PROFILE_CYCLE_MSG)

    assert reader.graph.profile.profile.profile == fr
    assert reader.graph.profile.profile.dict_set is sentinel  # unchanged
