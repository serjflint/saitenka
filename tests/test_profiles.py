"""Active-profile resolution + consumer routing (#254 phase 2): the ``[profiles.*]`` config surface,
the ``Profile``/``resolve_profile`` seam, and that the reader/provider gating key identity off the
active profile. Japanese stays the byte-identical default when nothing is configured."""

import pytest
import util
from session_builder import build_session

from saitenka.app import bindings as app_bindings
from saitenka.app.languages import DEFAULT_LANGUAGES, MAIN_LANG, SECOND_LANG, ReaderLanguages
from saitenka.app.profiles import (
    DEFAULT_PROFILE,
    Profile,
    configured_profiles,
    resolve_launch_identity,
    resolve_profile,
    scope_config,
    validate_language_code,
)
from saitenka.app.session.factory import SessionIdentity
from saitenka.app.subtitle_providers import enabled_providers_for, register_provider
from saitenka.app.tokenizer import register_tokenizer


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
    import saitenka.app.tokenizer as mod

    saved = dict(mod._FACTORIES)
    yield
    mod._FACTORIES.clear()
    mod._FACTORIES.update(saved)


def _isolated_provider_registry(monkeypatch):
    import saitenka.app.subtitle_providers as mod

    monkeypatch.setattr(mod, "_REGISTRY", {})
    return mod


def _stub_provider(name, languages):
    from saitenka.app.subtitle_providers import ProviderContext, SubtitleProvider

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
    reader = build_session(FakeIPC())
    assert reader.profile_session.profile.tokenizer.name == "unidic"
    assert reader.profile_session.profile.langs == ReaderLanguages(main="jp", second="en")


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


def test_unknown_script_language_without_a_tokenizer_fails_fast():
    """P2 regression: no silent unidic fallback for a language with no known script — omitting
    ``tokenizer`` raises a clear error instead of mis-segmenting it as Japanese. A KNOWN Latin-script
    code (fr/es/…) now defaults to ``latin`` (see below); only genuinely-unknown scripts fail fast."""
    with pytest.raises(ValueError, match="no default tokenizer"):
        resolve_profile({"profile": {"language": "zh"}})


def test_latin_script_language_defaults_to_the_latin_tokenizer():
    """A Latin-script language resolves the ``latin`` strategy with no explicit ``tokenizer`` (#254 W1)."""
    profile = resolve_profile({"profile": {"language": "fr"}})
    assert profile.tokenizer == "latin"
    assert profile.langs.main == "fr"


# --- the reader keys tokenizer + language identity off the active profile -------------------------


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_reader_uses_the_active_profiles_tokenizer_and_languages():
    register_tokenizer("latin", _FakeLatinTokenizer)
    profile = resolve_profile(
        {"active_profile": "fr", "profiles": {"fr": {"language": "fr", "tokenizer": "latin"}}}
    )
    reader = build_session(FakeIPC(), identity=SessionIdentity(profile=profile))
    assert isinstance(
        reader.profile_session.profile.tokenizer, _FakeLatinTokenizer
    )  # selected, not unidic
    assert (
        reader.profile_session.profile.langs.main == "fr"
        and reader.profile_session.profile.langs.second == "en"
    )
    assert reader.profile_session.profile.profile is profile


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_profile_rescopes_the_dict_set_live():
    """A live profile cycle (#254 W3) swaps not just the tokenizer/langs but the dictionary set — the
    scoper the CLI installs is consulted and its result replaces reader.dict_set."""
    register_tokenizer("latin", _FakeLatinTokenizer)
    jp = resolve_profile({})  # default (jp/unidic)
    fr = resolve_profile({"profile": {"language": "fr", "tokenizer": "latin"}})
    jp_dicts, fr_dicts = object(), object()  # sentinels — cycle must select by profile

    reader = build_session(FakeIPC(), identity=SessionIdentity(profile=jp))
    reader.profile_session.profile.replace_dictionary_set(jp_dicts)
    reader.profile_session.profile.configure_cycle(
        [jp, fr], lambda p: fr_dicts if p.langs.main == "fr" else jp_dicts
    )

    reader.command_runtime.handle(app_bindings.PROFILE_CYCLE_MSG)

    assert reader.profile_session.profile.profile is fr
    assert reader.profile_session.profile.langs.main == "fr"
    assert (
        reader.profile_session.profile.dict_set is fr_dicts
    )  # rescoped, not left on the JP dict set


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_profile_without_a_scoper_keeps_the_dict_set():
    """No scoper installed (the pre-W3 wiring / single-DB path) → a cycle leaves dict_set untouched,
    so the switcher stays backward-compatible."""
    register_tokenizer("latin", _FakeLatinTokenizer)
    jp = resolve_profile({})
    fr = resolve_profile({"profile": {"language": "fr", "tokenizer": "latin"}})
    reader = build_session(FakeIPC(), identity=SessionIdentity(profile=jp))
    sentinel = object()
    reader.profile_session.profile.replace_dictionary_set(sentinel)
    reader.profile_session.profile.configure_cycle([jp, fr])  # no dict_scoper

    reader.command_runtime.handle(app_bindings.PROFILE_CYCLE_MSG)

    assert reader.profile_session.profile.profile is fr
    assert reader.profile_session.profile.dict_set is sentinel  # unchanged


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
    from saitenka.app.launch.run import _resolve_names

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


def test_profilemine_targets_its_own_deck_model_and_fields_in_a_built_mineconfig():
    """The end-to-end observable for D6: a MineConfig built from the profile-scoped [mine] targets the
    profile's deck/model/field-map — the deck a mined note lands in, and the fields it writes."""
    from saitenka.app.mining_config import mine_config_from

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
    mine_conf = mine_config_from(cfg["mine"])
    assert mine_conf.deck == "French::Mining"
    assert mine_conf.model == "FrenchNote"
    assert mine_conf.fields == {"expression": "Mot", "sentence": "Phrase"}


def test_defaultmine_target_prefers_explicit_then_preset_then_lapis():
    """The (deck, model) a [mine] table implies with no CLI flag — the shared default the run signature
    AND the profile-scoped fallback use so a profile's deck/model isn't clobbered by a still-default flag."""
    from saitenka.app.launch.run import defaultmine_target

    assert defaultmine_target({}) == ("Saitenka::Mining", "Lapis")
    assert defaultmine_target({"preset": "Kiku"}) == ("Saitenka::Mining", "Kiku")
    assert defaultmine_target({"deck": "D", "model": "M", "preset": "Kiku"}) == ("D", "M")


def _scope_and_mine(cfg, mine_deck, mine_model):
    """The run seam, post-dedup: the shared identity spine scopes the cfg, then _resolvemine_target
    resolves the effective deck/model off it. Mirrors run_impl's two lines."""
    from saitenka.app.launch.run import _resolvemine_target

    scoped = resolve_launch_identity(cfg, profile_override=None, slang="ja,jpn,jp").cfg
    deck, model = _resolvemine_target(scoped, mine_deck, mine_model)
    return scoped, deck, model


def test_run_path_scoping_applies_the_profile_deck_when_the_flag_is_unset():
    """The run seam: a not-passed --mine-deck/--mine-model (the None sentinel) resolves to the active
    profile's own deck/model — they'd otherwise fall back to the base [mine]."""
    cfg = {
        "mine": {"deck": "Saitenka::Mining", "model": "Lapis"},
        "active_profile": "fr",
        "profiles": {
            "fr": {
                "language": "fr",
                "tokenizer": "latin",
                "mine": {"deck": "French::Mining", "model": "FrenchNote"},
            }
        },
    }
    scoped, deck, model = _scope_and_mine(cfg, None, None)  # None = flag not passed
    assert (deck, model) == ("French::Mining", "FrenchNote")
    assert scoped["mine"]["deck"] == "French::Mining"


def test_run_path_scoping_keeps_an_explicit_flag_over_the_profile():
    """An explicitly-passed --mine-deck (a non-None value) still wins over the profile — the flag is the
    user's deliberate override for this launch."""
    cfg = {
        "mine": {"deck": "Saitenka::Mining", "model": "Lapis"},
        "active_profile": "fr",
        "profiles": {
            "fr": {"language": "fr", "tokenizer": "latin", "mine": {"deck": "French::Mining"}}
        },
    }
    _scoped, deck, _model = _scope_and_mine(cfg, "CLI::Explicit", None)
    assert deck == "CLI::Explicit"  # explicit flag beats the profile deck


def test_run_path_scoping_honors_config_top_level_mine_over_the_import_default():
    """P1 regression: `saitenka run --config other.toml` whose top-level [mine].deck differs from the
    import-time default config's deck, PLUS an active profile with its OWN deck. The profile's deck must
    win — never the --config top-level, never the import-time default. The old comparison-baseline guard
    misfired here (it compared against the import-time default, misreading an unset flag as explicit)."""
    # This is what `load_config(--config other.toml)` yields at RUNTIME (deck ≠ the import-time default).
    runtime_cfg = {
        "mine": {"deck": "Other::TopLevel", "model": "Lapis"},
        "active_profile": "fr",
        "profiles": {
            "fr": {"language": "fr", "tokenizer": "latin", "mine": {"deck": "French::Mining"}}
        },
    }
    _scoped, deck, model = _scope_and_mine(runtime_cfg, None, None)  # neither flag passed
    assert (
        deck == "French::Mining"
    )  # the profile deck — not "Other::TopLevel", not the import default
    assert model == "Lapis"  # profile omits model → inherited from the --config top-level [mine]

    # And with NO active profile, an unset flag resolves to the --config top-level [mine] (honors --config,
    # not the import-time default) — the second half of the same P1.
    _s2, deck2, _m2 = _scope_and_mine({"mine": {"deck": "Other::TopLevel"}}, None, None)
    assert deck2 == "Other::TopLevel"


# --- configured_profiles: the ordered cycle the live switcher (D8) rotates through ----------------


def test_configured_profiles_is_a_single_default_when_nothing_is_configured():
    """No ``[profiles.*]`` → one profile (the JP default), so the live switcher is inert."""
    profiles = configured_profiles({})
    assert profiles == [DEFAULT_PROFILE]


def test_configured_profiles_lists_the_base_then_named_by_sorted_name():
    """The base ``[profile]`` default first, then each ``[profiles.<name>]`` by sorted name — a
    deterministic cycle order independent of ``active_profile``."""
    cfg = {
        "active_profile": "fr",  # must NOT reorder the cycle — the base stays first
        "profiles": {
            "fr": {"language": "fr", "tokenizer": "latin"},
            "de": {"language": "de", "tokenizer": "latin"},
        },
    }
    names = [p.name for p in configured_profiles(cfg)]
    assert names == ["default", "de", "fr"]  # base, then sorted names


def test_configured_profiles_base_is_the_default_table_not_the_active_named_profile():
    """The base is resolved WITHOUT ``active_profile`` — it's the genuine ``[profile]`` default, not
    whichever named profile is active."""
    cfg = {
        "active_profile": "fr",
        "profile": {"second": "de"},  # the base default table
        "profiles": {"fr": {"language": "fr", "tokenizer": "latin"}},
    }
    base = configured_profiles(cfg)[0]
    assert base.name == "default" and base.langs.main == "jp" and base.langs.second == "de"


def test_non_jp_profile_derives_slang_from_its_language():
    # #254: a French profile selects the French subtitle track, not the JP default (which used to let a
    # cached JP jimaku srt hijack the track). Derived from the language's primary subtag.
    cfg = {"active_profile": "fr", "profiles": {"fr": {"language": "fr", "tokenizer": "latin"}}}
    assert resolve_profile(cfg).slang == "fr"


def test_profile_region_subtag_is_folded_off_for_slang():
    cfg = {"active_profile": "ch", "profiles": {"ch": {"language": "de-CH", "tokenizer": "latin"}}}
    assert resolve_profile(cfg).slang == "de"


def test_explicit_profile_slang_wins_over_the_derived_one():
    # A profile can pin its own track priority (e.g. a release that tags French as the 3-letter "fra").
    cfg = {
        "active_profile": "fr",
        "profiles": {"fr": {"language": "fr", "tokenizer": "latin", "slang": "fra,fr"}},
    }
    assert resolve_profile(cfg).slang == "fra,fr"


def test_japanese_default_leaves_slang_unset_for_the_ambient_default():
    # None → run/attach keep the top-level slang (byte-identical JP path); no track-selection change.
    assert resolve_profile({}).slang is None
    assert DEFAULT_PROFILE.slang is None


# --- resolve_launch_identity: the shared run/attach spine ------------------------------------------


def test_launch_identity_resolves_scoped_cfg_slang_and_language_for_a_profile():
    # The one seam run + attach both call: --profile override, active profile, scoped cfg, effective
    # slang, language. Extracting it is what stops slang/language drifting between the two entrypoints.
    cfg = {
        "slang": "ja,jpn,jp",
        "dicts": ["JP dict"],
        "profiles": {"fr": {"language": "fr", "tokenizer": "latin", "dicts": ["FR dict"]}},
    }
    ident = resolve_launch_identity(cfg, profile_override="fr", slang="ja,jpn,jp")
    assert ident.profile.name == "fr"
    assert ident.language == "fr"
    assert ident.slang == "fr"  # derived from the profile language, not the JP fallback
    assert ident.cfg["dicts"] == ["FR dict"]  # scoped to the profile's dicts


def test_launch_identity_default_profile_keeps_jp_and_the_passed_slang():
    # No profile → JP language, slang stays whatever the CLI/config passed (byte-identical launch).
    ident = resolve_launch_identity({"slang": "ja,jpn,jp"}, profile_override=None, slang="en")
    assert ident.language == "jp"
    assert ident.slang == "en"  # profile derives nothing → the passed fallback stands


# --- primary_font_for: per-script font chain lead (generalizes past the Latin/JP binary) ------------


def test_primary_font_for_european_scripts_leads_with_notosans():
    from saitenka.app.profiles import primary_font_for

    assert primary_font_for("fr") == "NotoSans.ttf"  # Latin
    assert primary_font_for("ru") == "NotoSans.ttf"  # Cyrillic — generalizes for free
    assert primary_font_for("el") == "NotoSans.ttf"  # Greek
    assert primary_font_for("de-CH") == "NotoSans.ttf"  # region subtag folded off


def test_primary_font_for_japanese_and_unknown_keep_the_default_lead():
    from saitenka.app.profiles import primary_font_for

    assert primary_font_for("jp") is None  # JP-universal default → goldens unchanged
    assert (
        primary_font_for("zh") is None
    )  # unlisted script → default (system fallback handles glyphs)


def test_cyrillic_language_defaults_to_the_latin_whitespace_tokenizer():
    # The whitespace tokenizer is script-agnostic, so a Cyrillic profile needs no explicit tokenizer.
    from saitenka.app.profiles import default_tokenizer_for

    assert default_tokenizer_for("ru") == "latin"
