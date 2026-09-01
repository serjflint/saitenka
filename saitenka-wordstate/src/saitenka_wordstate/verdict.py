"""Classify one token against what the user knows: the SubMiner model, minus every colour.

Priority the classification encodes: **N+1 > forgotten > known > learning > young > frequency-band >
base**, with JLPT as an additive signal and frequency suppressed whenever there is any other one.
Which colours draw that is the consumer's business — see `saitenka.app.scoring.Palette`, which is the
only thing in the tree that turns a `TokenVerdict` into pixels.

Frequency and JLPT arrive as **protocols**, not concrete tables: this package reads the user's
collection, not a dictionary, and taking `FreqDict`/`JlptDict` by type would make it depend on one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from saitenka_tokenize.japanese import Token

# POS excluded from coloring / N+1 — grammatical/affixal tokens that aren't standalone vocabulary:
# particles, auxiliaries (incl. れる/られる), symbols, whitespace, conjunctions, prefixes, and — added
# to stop them consuming the single N+1 slot — suffixes (接尾辞: honorific さん, counter 個) and bare
# interjections (感動詞: ああ, えっ). A suffix's pos2 (名詞的) can't tell さん from a counter, so the
# exclusion is by pos1.
FUNCTION_POS = {
    "助詞",
    "助動詞",
    "補助記号",
    "記号",
    "空白",
    "接続詞",
    "接頭辞",
    "接尾辞",
    "感動詞",
}
SENT_BOUNDARY = set("。？！?!…")

#: FSRS states that take their own colour, ranked above plain "known".
MATURITY_STATES = frozenset({"forgotten", "learning", "young"})

# The banded classification only distinguishes ranks up to here, so a rank beyond it never bands. The
# app caps its FreqDict LOAD at the same value — loading rarer ranks is pure startup cost for zero
# effect. 'single' mode colours on ANY presence, so it needs the full table.
FREQ_BAND_TOP_X = 10000


class FrequencyTable(Protocol):
    """Rank lookup across a token's forms. `FreqDict` satisfies it; so would a remote provider."""

    def rank(self, *forms: str | None) -> int | None: ...


class LevelTable(Protocol):
    """JLPT-level lookup across a token's forms."""

    def level(self, *forms: str | None) -> str | None: ...


def freq_band(rank: int, top_x: int = FREQ_BAND_TOP_X, bands: int = 5) -> int | None:
    """Which of `bands` equal slices of `[1, top_x]` a rank falls in; `None` past the cap."""
    if rank <= 0 or rank > top_x:
        return None
    return min(bands, max(1, math.ceil(rank / top_x * bands)))


@dataclass(frozen=True, slots=True)
class TokenVerdict:
    """What the word-state layer knows about one token: classification only, never a color.

    The palette lives on the other side of this type. Callers that want a *decision* — which word to
    mine, which to prefetch — read the fields; only the renderer converts one into a `TokenStyle`.
    """

    is_content: bool = False
    is_known: bool = False
    fsrs_state: str | None = None  # new | learning | young | forgotten | known
    n_plus: int | None = None  # 1 when the token is its sentence's single eligible unknown
    jlpt: str | None = None  # N1..N5
    freq_rank: int | None = None
    freq_band: int | None = None  # 1..5, banded mode only
    freq_single: bool = False  # 'single' mode hit, which has a band-independent color

    @property
    def is_n_plus_one(self) -> bool:
        return self.n_plus == 1

    @property
    def is_mature(self) -> bool:
        """Reads "known" the way coloring does — a mature FSRS card, or the binary set when no FSRS."""
        return self.is_content and self.is_known

    @property
    def tag(self) -> str:
        """The legacy coloring tag, DERIVED from the fields rather than carried beside them.

        Mirrors `Palette.style_for`'s rule order, so the two cannot disagree about a token. Frequency
        never takes the JLPT suffix because it only fires when there is no level — applying it
        unconditionally is therefore the same string, one branch shorter.
        """
        base = "base"
        if self.is_n_plus_one:
            base = "n+1"
        elif self.is_content and self.fsrs_state in MATURITY_STATES:
            base = self.fsrs_state or base
        elif self.is_mature:
            base = "known"
        elif self.freq_single:
            base = "freq"
        elif self.freq_band is not None:
            base = f"freq-{self.freq_band}"
        return f"{base}/jlpt-{self.jlpt}" if self.jlpt else base


def is_content(t: Token) -> bool:
    """Content word for coloring/N+1 = anything that isn't a function-word POS (SubMiner blacklist)."""
    return bool(t.surface.strip()) and t.pos not in FUNCTION_POS


_is_content = is_content  # compatibility for tooltip's lazy import


@dataclass(frozen=True)
class SentenceProfile:
    """The shared lexical eligibility result for one subtitle sentence."""

    content_indices: tuple[int, ...]
    unknown_indices: tuple[int, ...]


def _sentence_profile(
    tokens: list[Token], known: list[bool], sent: range
) -> SentenceProfile | None:
    if not any(tokens[j].surface.strip() for j in sent):
        return None
    content = tuple(j for j in sent if is_content(tokens[j]))
    unknown = tuple(
        j
        for j in content
        if not known[j] and not tokens[j].is_kana_only and not tokens[j].is_proper_noun
    )
    return SentenceProfile(content, unknown)


def sentence_profiles(tokens: list[Token], known: list[bool]) -> tuple[SentenceProfile, ...]:
    """Split like subtitle coloring and classify the eligible unknowns once for N+1/N+2."""
    profiles: list[SentenceProfile] = []
    start = 0
    for i in range(len(tokens) + 1):
        boundary = i == len(tokens) or any(c in SENT_BOUNDARY for c in tokens[i].surface)
        if not boundary:
            continue
        profile = _sentence_profile(tokens, known, range(start, min(i + 1, len(tokens))))
        if profile is not None:
            profiles.append(profile)
        start = i + 1
    return tuple(profiles)


def mark_n_plus(
    tokens: list[Token], known: list[bool], *, unknowns: int, min_words: int = 3
) -> set[int]:
    """Indices in sentences with exactly ``unknowns`` eligible unknown content words."""
    return {
        index
        for profile in sentence_profiles(tokens, known)
        if len(profile.content_indices) >= min_words and len(profile.unknown_indices) == unknowns
        for index in profile.unknown_indices
    }


def mark_n_plus_one(tokens: list[Token], known: list[bool], min_words: int = 3) -> set[int]:
    """Indices of the single unknown content word in each ≥min_words-content-word sentence."""
    return mark_n_plus(tokens, known, unknowns=1, min_words=min_words)


# The banded coloring only distinguishes ranks up to here (band() returns None past it), so a rank
# beyond it never colors a word. Single-sourced because reader_deps caps the FreqDict LOAD at the same
# value — loading rarer ranks is pure startup cost for zero coloring effect (JPDBv2: 279k rows, only
# ~10k ≤ this). If 'single' freq_mode is ever wired to config, the load cap must go conditional (single
# colors on ANY presence, so it needs the full table) — see reader_deps._load_freq_dict.
FREQ_BAND_TOP_X = 10000
