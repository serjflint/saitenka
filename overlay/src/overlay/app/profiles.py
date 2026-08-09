"""Active-profile resolution (#254) — the seam that turns ``[profile]`` / ``[profiles.<name>]`` config
into the single :class:`Profile` value object the reader holds as swappable state.

A profile bundles the *identity* of what's being read: the main/second language CODES, and the
explicit tokenizer strategy name (decoupled from the language code — one ``unidic`` serves ja, a future
``latin`` serves fr/es/…). It does NOT carry the subtitle *role* state (primary/secondary track), which
stays on the reader's ``subtitle_language`` and keeps comparing the ``MAIN_LANG``/``SECOND_LANG``
role sentinels.

Resolution precedence, additive and backward-compatible: no ``active_profile`` and no ``[profile]``
table → the built-in JP default (byte-identical to pre-#254 behaviour). A ``[profile]`` singular table
supplies the default profile's fields; ``active_profile = "<name>"`` overlays ``[profiles.<name>]`` on
top of it. Leaf module: imports ``config`` (schema defaults) and ``languages`` only — never the
tokenizer (that stays a name string here; ``get_tokenizer`` is called later at reader construction), so
resolving a profile never drags in fugashi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from overlay.app.config import ProfileOptions
from overlay.app.languages import MAIN_LANG, ReaderLanguages

# BCP-47-ish shape: a primary subtag of letters, optional hyphen-separated alphanumeric subtags. Open by
# design — this validates SHAPE, never a whitelist, so any real language ("fr", "de-CH", "zh-Hant") is
# accepted while obvious garbage ("", "!!", "1") is rejected loudly at config-load time.
_LANG_CODE = re.compile(r"^[A-Za-z]{2,}(?:-[A-Za-z0-9]+)*$")

# Alias → the canonical internal code the rest of the app already keys on (``MAIN_LANG``/``SECOND_LANG``
# and each provider's ``languages`` set — ``subtitle_providers.py``). Canonicalising ONCE at resolution
# is the SSOT that keeps tokenizer selection and provider gating in agreement: without it a profile
# written ``language = "ja"`` (valid ISO-639-1) tokenizes as unidic yet silently fails the provider gate,
# since providers key on the exact literal ``"jp"``.
_CANONICAL = {
    "ja": "jp",
    "jp": "jp",
    "jpn": "jp",
    "japanese": "jp",
    "en": "en",
    "eng": "en",
    "english": "en",
}


def canonical_language(code: str) -> str:
    """Fold a language alias onto the canonical internal code (``ja``/``jpn`` → ``jp``); pass any other
    code through unchanged. Applied to both main and second so every downstream literal agrees."""
    return _CANONICAL.get(code.lower(), code)


@dataclass(frozen=True)
class Profile:
    """The resolved active profile — the one object the reader holds and every identity consumer reads
    off (tokenizer selection, provider gating, dict scoping). Swappable state for the live switcher (D8)."""

    name: str
    langs: ReaderLanguages
    tokenizer: str
    # Subtitle-track language priority this profile selects (mpv --slang order). ``None`` = the ambient
    # top-level default stands (byte-identical JP path). A non-JP profile derives it from its language so
    # it picks that track, not the JP default; an explicit ``slang`` in the profile table wins.
    slang: str | None = None


def validate_language_code(code: str) -> str:
    """Return ``code`` if it's a plausibly-shaped language code, else raise ``ValueError``. Open, not a
    whitelist: unknown-but-well-formed codes pass (they fall back to language-agnostic providers)."""
    if not _LANG_CODE.match(code):
        raise ValueError(f"malformed language code {code!r} (expected e.g. 'ja', 'fr', 'de-CH')")
    return code


# Language codes (ISO-639-1, region subtags folded off) by primary script. All three groups share the
# whitespace ``latin`` tokenizer (it is script-agnostic — words are space-delimited in each) AND lead the
# font fallback chain with NotoSans (crisp European letterforms). Membership is by *script*, not a promise
# the deinflector ships rules for every one (only fr does today); an unlisted language must name its
# tokenizer explicitly. Onboarding a writing system = extend one set (Cyrillic/Greek already work).
_LATIN_SCRIPT = frozenset(
    {"fr", "es", "de", "it", "pt", "nl", "ca", "ro", "sv", "da", "no", "nb", "nn", "fi", "pl"}
)
_CYRILLIC_SCRIPT = frozenset({"ru", "uk", "be", "bg", "sr", "mk"})
_GREEK_SCRIPT = frozenset({"el"})
# Whitespace-segmented European scripts: `latin` tokenizer + NotoSans-led font chain.
_EUROPEAN_SCRIPTS = _LATIN_SCRIPT | _CYRILLIC_SCRIPT | _GREEK_SCRIPT


def _base_code(language: str) -> str:
    """The primary subtag, lowercased (``de-CH`` → ``de``)."""
    return language.split("-", 1)[0].lower()


def primary_font_for(language: str) -> str | None:
    """The vendored font that should LEAD the fallback chain for ``language``'s script, or ``None`` for
    the JP-universal default (:func:`overlay.fonts.set_primary_font`). European scripts (Latin/Cyrillic/
    Greek — all covered by NotoSans) lead with it; Japanese and any unlisted script keep the default so
    their goldens stay byte-identical."""
    return "NotoSans.ttf" if _base_code(language) in _EUROPEAN_SCRIPTS else None


def default_tokenizer_for(language: str) -> str:
    """The tokenizer a profile gets when it omits ``tokenizer`` (``language`` already canonicalised).
    Japanese → ``unidic``; a whitespace-segmented European script (Latin/Cyrillic/Greek) → ``latin``. Any
    other language must name its tokenizer explicitly — there is no safe guess, and silently falling back
    would mis-segment an unknown script with no signal. Fail fast instead."""
    if language == "jp":
        return "unidic"
    if _base_code(language) in _EUROPEAN_SCRIPTS:
        return "latin"
    raise ValueError(
        f"no default tokenizer for language {language!r}; set a profile tokenizer explicitly "
        f'(e.g. tokenizer = "latin")'
    )


def _table(cfg: dict, key: str) -> dict:
    raw = cfg.get(key)
    return raw if isinstance(raw, dict) else {}


def profile_names(cfg: dict) -> list[str]:
    """Named ``[profiles.*]`` in sorted order (the switcher's cycle order after the base default). Lives
    on this leaf module so both the CLI and doctor read it without importing the CLI (cycle-free)."""
    return sorted(_table(cfg, "profiles"))


def _active_profile_table(cfg: dict, override: str | None = None) -> tuple[str | None, dict]:
    """``(name, raw)`` for the active profile: the ``[profile]`` singular default table (what
    ``saitenka config`` edits) with ``[profiles.<name>]`` overlaid on top. ``name`` is the selector
    (``override`` — the ``--profile`` flag — beats the config's ``active_profile``), or ``None`` for the
    built-in default. Shared by :func:`resolve_profile` (identity fields) and :func:`scope_config`
    (dict/mine scoping) so the profile table is parsed once per concern."""
    name = override or cfg.get("active_profile")
    raw = dict(_table(cfg, "profile"))  # singular default-profile table
    if name:
        named = _table(cfg, "profiles").get(name)
        if isinstance(named, dict):
            raw.update(named)  # named overlay on top of the default table
    return name, raw


# Flat-cfg keys a profile scopes (D4/D6). ``dicts``/``freq``/``pitch`` are dictionary TITLE lists — a
# profile REPLACES them wholesale (a French profile consults only its own dicts, never the JP set).
_LIST_SCOPED = ("dicts", "freq", "pitch")


def scope_config(cfg: dict, override: str | None = None) -> dict:
    """Overlay the active profile's scoped tables onto the flat cfg (design D1-A), returning the cfg the
    dep builders (``build_reader_deps`` / ``_mine_config_from``) should read. ``dicts``/``freq``/``pitch``
    lists replace the top-level lists; a ``[profiles.<name>.mine]`` table merges key-wise OVER ``[mine]``
    (a profile can override just the deck and inherit the rest). Returns the SAME cfg object when the
    active profile scopes none of these — so the default profile (no ``[profiles.*]``) is byte-identical
    and every existing ``cfg.get(...)`` reader stays untouched."""
    _, raw = _active_profile_table(cfg, override)
    lists = {k: list(raw[k]) for k in _LIST_SCOPED if isinstance(raw.get(k), list)}
    mine = raw.get("mine")
    if not lists and not isinstance(mine, dict):
        return cfg
    out = dict(cfg)
    out.update(lists)
    if isinstance(mine, dict):
        base = cfg.get("mine")
        out["mine"] = {**(base if isinstance(base, dict) else {}), **mine}
    return out


def resolve_profile(cfg: dict, override: str | None = None) -> Profile:
    """The active :class:`Profile` for ``cfg``. ``override`` (the ``--profile`` CLI flag) wins over the
    config's ``active_profile`` selector. No profile configured → the built-in JP default."""
    name, raw = _active_profile_table(cfg, override)
    defaults = ProfileOptions()
    language = canonical_language(validate_language_code(str(raw.get("language", MAIN_LANG))))
    second = canonical_language(validate_language_code(str(raw.get("second", defaults.second))))
    tokenizer = str(raw.get("tokenizer") or default_tokenizer_for(language))
    return Profile(
        name=str(name) if name else "default",
        langs=ReaderLanguages(main=language, second=second),
        tokenizer=tokenizer,
        slang=_profile_slang(raw, language),
    )


def effective_slang(profile: Profile, fallback: str) -> str:
    """The subtitle-language priority a launch uses: the active profile's own (a non-JP or slang-set
    profile) if it has one, else the CLI/config ``fallback``. The one place ``run``/``attach`` resolve
    it, so a non-JP profile stops selecting the JP track (#254)."""
    return profile.slang or fallback


def _profile_slang(raw: dict, language: str) -> str | None:
    """The subtitle-track language priority a profile implies (see :attr:`Profile.slang`). Explicit
    ``slang`` wins; else a non-JP profile derives it from its language's primary subtag (``de-CH`` →
    ``de``) so it selects THAT track instead of the JP default; a JP profile returns ``None`` (the
    ambient default stands, byte-identical)."""
    explicit = raw.get("slang")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if language != MAIN_LANG:
        return language.split("-", 1)[0]
    return None


def configured_profiles(cfg: dict) -> list[Profile]:
    """The ordered cycle the live switcher (D8) rotates through: the base ``[profile]`` default first,
    then each ``[profiles.<name>]`` overlay by sorted name (deterministic order). A config with no
    ``[profiles.*]`` (or none at all) yields a single-element list, so the switcher is inert on the
    default path. The base is resolved WITHOUT ``active_profile`` so it's the genuine default table, not
    whichever named profile is currently active."""
    base = resolve_profile({k: v for k, v in cfg.items() if k != "active_profile"})
    profiles = [base]
    profiles.extend(resolve_profile(cfg, override=name) for name in sorted(_table(cfg, "profiles")))
    return profiles


@dataclass(frozen=True)
class LaunchIdentity:
    """The profile-derived identity a ``run``/``attach`` launch needs, resolved ONCE from raw cfg +
    CLI flags by :func:`resolve_launch_identity`. Both entrypoints read off this instead of each
    re-deriving the spine — the recurring run/attach drift (slang, then dict-set language) came from
    duplicating these steps, so a new profile-aware field must be added here, not in two runners."""

    cfg: dict  # profile-scoped (dicts/freq/pitch/mine/slang/jimaku overlaid)
    profile: Profile  # the active profile
    slang: str  # effective subtitle-track priority (profile's own, else the CLI/config fallback)
    profile_cycle: list[Profile]  # the live switcher's cycle order

    @property
    def language(self) -> str:
        """The active profile's main language — routes tokenizer + deinflection + provider gating."""
        return self.profile.langs.main


def resolve_launch_identity(
    cfg: dict, *, profile_override: str | None, slang: str
) -> LaunchIdentity:
    """The shared run/attach spine: apply ``--profile``, resolve the active profile, scope the cfg, and
    derive the effective slang + switcher cycle. The ONE place this happens, so a profile-aware field
    can't drift between the two entrypoints. ``cfg`` is the raw loaded config; the returned ``cfg`` is
    the scoped one the dep builders read."""
    if profile_override:  # --profile beats the config's active_profile selector for this launch
        cfg = {**cfg, "active_profile": profile_override}
    active = resolve_profile(cfg)
    return LaunchIdentity(
        cfg=scope_config(cfg),
        profile=active,
        slang=effective_slang(active, slang),
        profile_cycle=configured_profiles(cfg),
    )


DEFAULT_PROFILE = resolve_profile({})  # the JP default; construction default for a headless reader
