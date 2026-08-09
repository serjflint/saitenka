"""Active-profile resolution + consumer routing (#254 phase 2): the ``[profiles.*]`` config surface,
the ``Profile``/``resolve_profile`` seam, and that the reader/provider gating key identity off the
active profile. Japanese stays the byte-identical default when nothing is configured."""

import pytest
from overlay.app.controller import Reader
from overlay.app.languages import DEFAULT_LANGUAGES, MAIN_LANG, SECOND_LANG, ReaderLanguages
from overlay.app.profiles import (
    DEFAULT_PROFILE,
    Profile,
    resolve_profile,
    scope_config,
    validate_language_code,
)
from overlay.app.subtitle_providers import enabled_providers_for, register_provider
from overlay.app.tokenizer import register_tokenizer


class FakeIPC:
    def __init__(self, props=None):
        self.props = props or {}

    def command(self, *args):
        if args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}


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
    import overlay.app.tokenizer as mod

    saved = dict(mod._FACTORIES)
    yield
    mod._FACTORIES.clear()
    mod._FACTORIES.update(saved)


def _isolated_provider_registry(monkeypatch):
    import overlay.app.subtitle_providers as mod

    monkeypatch.setattr(mod, "_REGISTRY", {})
    return mod


def _stub_provider(name, languages):
    from overlay.app.subtitle_providers import ProviderContext, SubtitleProvider

    def candidates(_video, _ctx: ProviderContext):
        return [], []

    def fetch_attempt(_video, _ctx: ProviderContext):
        return lambda: (None, f"{name}: stub")

    return SubtitleProvider(
        name=name, languages=languages, candidates=candidates, fetch_attempt=fetch_attempt
    )


# --- default: Japanese, unchanged, when nothing is configured (characterization) ------------------


def test_unconfigured_resolves_to_the_japanese_default_profile():
    profile = resolve_profile({})
    assert profile == DEFAULT_PROFILE
    assert profile.langs == ReaderLanguages(main=MAIN_LANG, second=SECOND_LANG) == DEFAULT_LANGUAGES
    assert profile.tokenizer == "unidic"


def test_reader_without_a_profile_is_japanese_unidic():
    """The construction default is today's JP profile — so an existing call site (and every golden)
    behaves exactly as before #254."""
    reader = Reader(FakeIPC())
    assert reader.tokenizer.name == "unidic"
    assert reader.langs == ReaderLanguages(main="jp", second="en")


# --- a named profile selects language, tokenizer, and second language -----------------------------


def test_named_profile_selects_language_tokenizer_and_second():
    cfg = {"active_profile": "fr", "profiles": {"fr": {"language": "fr", "tokenizer": "latin"}}}
    profile = resolve_profile(cfg)
    assert profile.name == "fr"
    assert profile.langs.main == "fr"
    assert profile.langs.second == "en"  # per-profile second defaults to English (D7)
    assert profile.tokenizer == "latin"


def test_profile_second_language_is_configurable():
    cfg = {
        "active_profile": "fr",
        "profiles": {"fr": {"language": "fr", "tokenizer": "latin", "second": "de"}},
    }
    assert resolve_profile(cfg).langs.second == "de"


def test_singular_profile_table_supplies_the_default_when_no_selector():
    """A ``[profile]`` table (no ``active_profile``) is the editable default — what `saitenka config`
    writes — resolved even without any named profiles."""
    profile = resolve_profile({"profile": {"language": "fr", "tokenizer": "latin"}})
    assert profile.langs.main == "fr" and profile.tokenizer == "latin"


def test_named_profile_overlays_the_singular_default_table():
    cfg = {
        "active_profile": "fr",
        "profile": {"second": "de"},  # base default table
        "profiles": {"fr": {"language": "fr", "tokenizer": "latin"}},  # overlay
    }
    profile = resolve_profile(cfg)
    assert profile.langs.main == "fr"  # from the overlay
    assert profile.langs.second == "de"  # inherited from the base [profile] table
    assert profile.tokenizer == "latin"


def test_cli_override_wins_over_the_config_selector():
    """The ``--profile`` flag (passed as ``override``) beats a config ``active_profile``."""
    cfg = {
        "active_profile": "ja",
        "profiles": {"ja": {"language": "jp"}, "fr": {"language": "fr", "tokenizer": "latin"}},
    }
    assert resolve_profile(cfg, override="fr").langs.main == "fr"


def test_tokenizer_defaults_by_language_when_omitted():
    assert resolve_profile({"profile": {"language": "jp"}}).tokenizer == "unidic"


def test_ja_alias_canonicalizes_so_it_keeps_jp_tokenizer_and_providers(monkeypatch):
    """P1 regression: a profile written ``language = "ja"`` (canonical ISO-639-1) must resolve to the
    SAME internal ``jp`` code the tokenizer default AND the provider gate key on — never a unidic
    tokenizer paired with silently-dropped jp providers."""
    _isolated_provider_registry(monkeypatch)
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    register_provider(_stub_provider("tsukihime", frozenset({"jp"})))

    profile = resolve_profile({"profile": {"language": "ja"}})
    assert profile.langs.main == "jp"  # 'ja' folded onto the internal 'jp'
    assert profile.tokenizer == "unidic"
    assert enabled_providers_for(profile.langs.main, (("jimaku", True), ("tsukihime", True))) == (
        "jimaku",
        "tsukihime",
    )


def test_non_jp_language_without_a_tokenizer_fails_fast():
    """P2 regression: no silent unidic fallback for a non-JP language — omitting ``tokenizer`` raises a
    clear error instead of mis-segmenting a non-JP script as Japanese."""
    with pytest.raises(ValueError, match="no default tokenizer"):
        resolve_profile({"profile": {"language": "fr"}})


# --- the reader keys tokenizer + language identity off the active profile -------------------------


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_reader_uses_the_active_profiles_tokenizer_and_languages():
    register_tokenizer("latin", _FakeLatinTokenizer)
    profile = resolve_profile(
        {"active_profile": "fr", "profiles": {"fr": {"language": "fr", "tokenizer": "latin"}}}
    )
    reader = Reader(FakeIPC(), profile=profile)
    assert isinstance(reader.tokenizer, _FakeLatinTokenizer)  # selected, not unidic
    assert reader.langs.main == "fr" and reader.langs.second == "en"
    assert reader.profile is profile


# --- open language codes: accepted (not whitelisted), agnostic-provider fallback ------------------


@pytest.mark.parametrize("code", ["fr", "de-CH", "zh-Hant", "xyz"])
def test_open_and_unknown_language_codes_are_accepted(code):
    """Codes are shape-validated, never whitelisted — an unknown-but-well-formed code resolves fine
    (a tokenizer is named so resolution doesn't fail-fast on the missing default)."""
    profile = resolve_profile({"profile": {"language": code, "tokenizer": "latin"}})
    assert profile.langs.main == code  # non-JP codes pass through canonicalisation unchanged


@pytest.mark.parametrize("bad", ["", "!!", "1", "a b"])
def test_malformed_language_code_is_rejected(bad):
    with pytest.raises(ValueError, match="malformed language code"):
        validate_language_code(bad)


def test_active_profile_language_gates_providers(monkeypatch):
    """A non-JP profile's language drops the jp-only providers but keeps a language-agnostic one —
    the D5 capability gate, driven off the active profile's main language."""
    _isolated_provider_registry(monkeypatch)
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    register_provider(_stub_provider("universal", frozenset()))  # language-agnostic

    ja = resolve_profile({}).langs.main
    fr = resolve_profile({"profile": {"language": "fr", "tokenizer": "latin"}}).langs.main
    flags = (("jimaku", True), ("universal", True))

    assert enabled_providers_for(ja, flags) == ("jimaku", "universal")  # JP unchanged
    assert enabled_providers_for(fr, flags) == ("universal",)  # jp-only dropped, agnostic survives


def test_profile_is_an_immutable_value_object():
    profile = Profile(name="fr", langs=ReaderLanguages("fr", "en"), tokenizer="latin")
    with pytest.raises((AttributeError, TypeError)):
        profile.name = "de"  # frozen — swappable by replacement, never mutation


# --- #254 phase 4: dictionary + Anki mining config scoped per profile (D4/D6) ---------------------
# scope_config overlays the active profile's dict/freq/pitch title lists + [mine] table onto the flat
# cfg the dep builders read; the default profile is byte-identical, a non-JP profile selects its own.


def test_default_profile_leaves_dicts_and_mine_byte_identical():
    """No [profiles.*] scoping the dict lists or [mine] → scope_config returns the SAME object, so the
    dep build reads exactly today's config (the byte-identical-default contract)."""
    cfg = {"dicts": ["JMdict"], "freq": ["JPDB"], "mine": {"deck": "Saitenka::Mining"}}
    assert scope_config(cfg) is cfg


def test_named_profile_replaces_dict_freq_pitch_wholesale():
    """A French profile consults ONLY its own dicts — the top-level JP list is replaced, not merged
    (a JP dict must never leak into a French lookup)."""
    cfg = {
        "dicts": ["JMdict", "daijirin"],
        "freq": ["JPDB"],
        "pitch": ["NHK"],
        "active_profile": "fr",
        "profiles": {
            "fr": {
                "language": "fr",
                "tokenizer": "latin",
                "dicts": ["Le Grand Robert"],
                "freq": ["French Freq"],
            }
        },
    }
    scoped = scope_config(cfg)
    assert scoped["dicts"] == ["Le Grand Robert"]  # JP dicts gone, not appended
    assert scoped["freq"] == ["French Freq"]
    assert scoped["pitch"] == ["NHK"]  # profile omits pitch → top-level survives untouched
    assert cfg["dicts"] == ["JMdict", "daijirin"]  # original cfg not mutated


def test_profile_mine_merges_key_wise_over_top_level():
    """[profiles.<x>.mine] overlays [mine]: the profile overrides just the deck; every other [mine] key
    (model, fields, animated_*) is inherited from the top-level table."""
    cfg = {
        "mine": {"deck": "Saitenka::Mining", "model": "Lapis", "animated_screenshot": True},
        "active_profile": "fr",
        "profiles": {
            "fr": {"language": "fr", "tokenizer": "latin", "mine": {"deck": "French::Mining"}}
        },
    }
    scoped = scope_config(cfg)
    assert scoped["mine"] == {
        "deck": "French::Mining",  # overridden by the profile
        "model": "Lapis",  # inherited from [mine]
        "animated_screenshot": True,  # inherited from [mine]
    }
    assert cfg["mine"]["deck"] == "Saitenka::Mining"  # top-level cfg untouched


def test_profile_override_selects_scoping_like_resolve_profile():
    """The ``--profile`` override wins over the config selector for dict/mine scoping too."""
    cfg = {
        "dicts": ["JMdict"],
        "active_profile": "ja",
        "profiles": {
            "ja": {"language": "jp"},
            "fr": {"language": "fr", "tokenizer": "latin", "dicts": ["Le Grand Robert"]},
        },
    }
    assert scope_config(cfg, override="fr")["dicts"] == ["Le Grand Robert"]


def test_scoped_dicts_are_what_the_run_path_resolves():
    """Observable through the actual run-path seam: with no --dict flag, ``_resolve_names`` reads the
    profile-scoped dict list (which dictionaries load)."""
    from overlay.app.cli_run import _resolve_names

    cfg = scope_config(
        {
            "dicts": ["JMdict"],
            "active_profile": "fr",
            "profiles": {
                "fr": {"language": "fr", "tokenizer": "latin", "dicts": ["Le Grand Robert"]}
            },
        }
    )
    assert _resolve_names(None, cfg, "dicts") == ["Le Grand Robert"]  # profile dicts, not JP


def test_profile_mine_targets_its_own_deck_model_and_fields_in_a_built_mineconfig():
    """The end-to-end observable for D6: a MineConfig built from the profile-scoped [mine] targets the
    profile's deck/model/field-map — the deck a mined note lands in, and the fields it writes."""
    from overlay.app.reader_deps import _mine_config_from

    cfg = scope_config(
        {
            "mine": {"deck": "Saitenka::Mining", "model": "Lapis"},
            "active_profile": "fr",
            "profiles": {
                "fr": {
                    "language": "fr",
                    "tokenizer": "latin",
                    "mine": {
                        "deck": "French::Mining",
                        "model": "FrenchNote",
                        "fields": {"expression": "Mot", "sentence": "Phrase"},
                    },
                }
            },
        }
    )
    mine_conf = _mine_config_from(cfg["mine"])
    assert mine_conf.deck == "French::Mining"
    assert mine_conf.model == "FrenchNote"
    assert mine_conf.fields == {"expression": "Mot", "sentence": "Phrase"}


def test_default_mine_target_prefers_explicit_then_preset_then_lapis():
    """The (deck, model) a [mine] table implies with no CLI flag — the shared default the run signature
    AND the profile-scoped fallback use so a profile's deck/model isn't clobbered by a still-default flag."""
    from overlay.app.cli_run import default_mine_target

    assert default_mine_target({}) == ("Saitenka::Mining", "Lapis")
    assert default_mine_target({"preset": "Kiku"}) == ("Saitenka::Mining", "Kiku")
    assert default_mine_target({"deck": "D", "model": "M", "preset": "Kiku"}) == ("D", "M")
