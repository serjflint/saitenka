"""Subtitle language roles — the two states the primary track can show, as named constants.

A tiny leaf (no ``app`` imports) so any module can name a language role without an import cycle. The
string VALUES ("jp"/"en") stay stable — they're persisted (session stats, backlog) and matched
against mpv track tags — while call sites read by ROLE. This is the seam a future release makes
configurable: which concrete language is main vs second becomes a setting sourced here.
"""

from __future__ import annotations

from typing import Literal

Language = Literal["jp", "en"]

MAIN_LANG: Language = "jp"  # target language: tokenized, annotated, colored, hoverable
SECOND_LANG: Language = "en"  # known/translation language: drawn plain, non-interactive

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
