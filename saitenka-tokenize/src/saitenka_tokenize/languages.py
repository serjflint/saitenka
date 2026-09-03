"""Subtitle language roles — the two states the primary track can show, as named constants.

A tiny leaf (no ``app`` imports) so any module can name a language role without an import cycle. The
string VALUES ("jp"/"en") stay stable — they're persisted (session stats, backlog) and matched
against mpv track tags — while call sites read by ROLE. This is the seam a future release makes
configurable: which concrete language is main vs second becomes a setting sourced here.
"""

from __future__ import annotations

from dataclasses import dataclass

# Open BCP-47-ish code, not a closed 2-value enum (#254): a profile's language is user-supplied
# ("fr", "de-CH", …). Kept as a transparent alias for readability at call sites — no validation gate
# here (shape-checked once at profile resolution, app/profiles.py).
type Language = str

MAIN_LANG: Language = "jp"  # target language: tokenized, annotated, colored, hoverable
SECOND_LANG: Language = "en"  # known/translation language: drawn plain, non-interactive


@dataclass(frozen=True)
class ReaderLanguages:
    """The concrete main/second language CODES a reader is currently reading, resolved from the active
    profile (#254). Distinct from the subtitle *role* sentinels (``MAIN_LANG``/``SECOND_LANG``, which the
    primary/secondary-track state machine still compares by role): this is the identity layer the
    tokenizer, provider gating, and dict scoping key off. Held as swappable reader state so a live
    profile switch (D8) can re-resolve it without a restart."""

    main: str = MAIN_LANG
    second: str = SECOND_LANG


DEFAULT_LANGUAGES = ReaderLanguages()  # today's JP-main / EN-second default

_ISO_639_ALIASES = {
    "be": ("bel",),
    "bg": ("bul",),
    "ca": ("cat",),
    "da": ("dan",),
    "de": ("deu", "ger"),
    "el": ("ell", "gre"),
    "en": ("eng", "english"),
    "es": ("spa",),
    "fi": ("fin",),
    "fr": ("fra", "fre"),
    "it": ("ita",),
    "jp": ("jpn", "ja", "japanese"),
    "mk": ("mkd", "mac"),
    "nb": ("nob",),
    "nl": ("nld", "dut"),
    "nn": ("nno",),
    "no": ("nor",),
    "pl": ("pol",),
    "pt": ("por",),
    "ro": ("ron", "rum"),
    "ru": ("rus",),
    "sr": ("srp",),
    "sv": ("swe",),
    "uk": ("ukr",),
}


def equivalent_language_bases(code: str) -> tuple[str, ...]:
    """Known ISO/legacy spellings for one primary language subtag, input spelling first."""
    base = code.lower()
    aliases = _ISO_639_ALIASES.get(base)
    if aliases is not None:
        return (base, *aliases)
    for canonical, candidates in _ISO_639_ALIASES.items():
        if base in candidates:
            return (base, canonical, *(candidate for candidate in candidates if candidate != base))
    return (base,)


def language_base(code: str) -> str:
    """Canonical primary subtag for a language tag (``fra-CA`` -> ``fr``)."""
    base = code.partition("-")[0].lower()
    equivalents = equivalent_language_bases(base)
    return next((candidate for candidate in equivalents if candidate in _ISO_639_ALIASES), base)


def canonical_language_tag(code: str) -> str:
    """Canonicalize a known primary subtag while preserving its region/script suffix."""
    _base, separator, suffix = code.partition("-")
    canonical = language_base(code)
    return f"{canonical}{separator}{suffix}" if separator else canonical


# Unicode blocks that mark text as Japanese — content-based language ID when a subtitle track carries
# no (or a wrong) language tag. Kana is unambiguously Japanese; CJK ideographs are shared with Chinese,
# but for a JP-immersion tool choosing between Japanese and a Latin-script fallback their presence,
# absent any tag, is treated as Japanese. Half-width katakana (FF66–FF9D) covers older/ripped subs.
_JAPANESE_RANGES: tuple[tuple[int, int], ...] = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x31F0, 0x31FF),  # Katakana phonetic extensions
    (0xFF66, 0xFF9D),  # Half-width katakana
    (0x4E00, 0x9FFF),  # CJK unified ideographs (kanji)
)


def looks_japanese(text: str) -> bool:
    """True if ``text`` contains any Japanese script (kana or kanji). Used to classify a subtitle
    track whose language tag is missing or unknown by its actual content, so an untagged Japanese
    track colors and an untagged English one does not."""
    return any(any(lo <= ord(ch) <= hi for lo, hi in _JAPANESE_RANGES) for ch in text)
