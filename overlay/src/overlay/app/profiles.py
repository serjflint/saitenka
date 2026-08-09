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


def validate_language_code(code: str) -> str:
    """Return ``code`` if it's a plausibly-shaped language code, else raise ``ValueError``. Open, not a
    whitelist: unknown-but-well-formed codes pass (they fall back to language-agnostic providers)."""
    if not _LANG_CODE.match(code):
        raise ValueError(f"malformed language code {code!r} (expected e.g. 'ja', 'fr', 'de-CH')")
    return code


def default_tokenizer_for(language: str) -> str:
    """The tokenizer a profile gets when it omits ``tokenizer`` (``language`` already canonicalised).
    Only Japanese has a built-in default (``unidic``); any other language must name its tokenizer
    explicitly — there is no safe guess, and silently falling back to the JP tokenizer would mis-segment
    a non-JP script with no signal. Fail fast instead."""
    if language == "jp":
        return "unidic"
    raise ValueError(
        f"no default tokenizer for language {language!r}; set a profile tokenizer explicitly "
        f'(e.g. tokenizer = "latin")'
    )


def _table(cfg: dict, key: str) -> dict:
    raw = cfg.get(key)
    return raw if isinstance(raw, dict) else {}


def resolve_profile(cfg: dict, override: str | None = None) -> Profile:
    """The active :class:`Profile` for ``cfg``. ``override`` (the ``--profile`` CLI flag) wins over the
    config's ``active_profile`` selector. No profile configured → the built-in JP default."""
    name = override or cfg.get("active_profile")
    raw = dict(
        _table(cfg, "profile")
    )  # singular default-profile table (what `saitenka config` edits)
    if name:
        named = _table(cfg, "profiles").get(name)
        if isinstance(named, dict):
            raw.update(named)  # named overlay on top of the default table
    defaults = ProfileOptions()
    language = canonical_language(validate_language_code(str(raw.get("language", MAIN_LANG))))
    second = canonical_language(validate_language_code(str(raw.get("second", defaults.second))))
    tokenizer = str(raw.get("tokenizer") or default_tokenizer_for(language))
    return Profile(
        name=str(name) if name else "default",
        langs=ReaderLanguages(main=language, second=second),
        tokenizer=tokenizer,
    )


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


DEFAULT_PROFILE = resolve_profile({})  # the JP default; construction default for a headless reader
