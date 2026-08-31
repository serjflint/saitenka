"""Score subtitle tokens into per-word colors, reproducing SubMiner's model.

Text-color priority: **N+1 > forgotten > known > learning > young > frequency-band > base**
(name-match is out of scope). JLPT is an **additive underline**, and frequency is suppressed when a
token has a JLPT level (matching SubMiner's "frequency only if no other signal"). Coloring keys on the
**lemma** with a reading fallback; function words stay base.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import starmap
from typing import TYPE_CHECKING

from saitenka.app.wordlists import FreqDict, JlptDict, KnownWords

if TYPE_CHECKING:
    from saitenka.app.fsrs import KnownSnap
    from saitenka.app.tokenize import Token

RGBA = tuple[int, int, int, int]

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


def _hex(s: str) -> RGBA:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)


def _configured_color(raw: dict, name: str, default: RGBA) -> RGBA:
    value = raw.get(name)
    if value is None:
        return default
    if not isinstance(value, str) or len(value.lstrip("#")) != 6:
        raise ValueError(f"palette.{name} must be a six-digit hex color")
    try:
        return _hex(value)
    except ValueError as error:
        raise ValueError(f"palette.{name} must be a six-digit hex color") from error


@dataclass(frozen=True)
class Palette:
    """SubMiner defaults (Catppuccin Macchiato)."""

    base: RGBA = _hex("#cad3f5")
    known: RGBA = _hex("#a6da95")
    forgotten: RGBA = _hex("#ee99a0")
    learning: RGBA = _hex("#eed49f")
    young: RGBA = _hex("#8bd5ca")
    n_plus_one: RGBA = _hex("#c6a0f6")
    hover: RGBA = _hex("#f4dbd6")
    freq_single: RGBA = _hex("#f5a97f")
    freq_bands: tuple[RGBA, ...] = (
        _hex("#ed8796"),
        _hex("#f5a97f"),
        _hex("#f9e2af"),
        _hex("#8bd5ca"),
        _hex("#8aadf4"),
    )
    jlpt: dict = field(
        default_factory=lambda: {
            "N1": _hex("#ed8796"),
            "N2": _hex("#f5a97f"),
            "N3": _hex("#f9e2af"),
            "N4": _hex("#8bd5ca"),
            "N5": _hex("#8aadf4"),
        }
    )

    @classmethod
    def from_config(cls, raw: object) -> Palette:
        palette = cls()
        if not isinstance(raw, dict):
            return palette
        return cls(
            learning=_configured_color(raw, "learning", palette.learning),
            young=_configured_color(raw, "young", palette.young),
        )

    def style_for(self, verdict: TokenVerdict) -> TokenStyle:
        """The drawn style for a verdict — the ONE place a classification becomes a color.

        Priority: N+1 > maturity > known > frequency > base, with JLPT as an additive underline.
        """
        underline = self.jlpt.get(verdict.jlpt) if verdict.jlpt else None
        return TokenStyle(self._color_for(verdict), underline, verdict)

    def _color_for(self, verdict: TokenVerdict) -> RGBA:
        if verdict.is_n_plus_one:
            return self.n_plus_one
        if verdict.is_content and verdict.fsrs_state in MATURITY_STATES:
            return getattr(self, verdict.fsrs_state or "base")
        if verdict.is_mature:
            return self.known
        if verdict.freq_single:
            return self.freq_single
        if verdict.freq_band is not None:
            return self.freq_bands[verdict.freq_band - 1]
        return self.base


#: FSRS states that take their own color, ranked above plain "known".
MATURITY_STATES = frozenset({"forgotten", "learning", "young"})


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


@dataclass(frozen=True)
class TokenStyle:
    """A drawn token: the palette's two colors, plus the verdict they were derived from.

    The verdict rides along because the pipeline that carries styles to the renderer is the same one
    mining and prefetch read to make decisions, and they want the classification, not the color.
    Deriving `tag` from it rather than storing it beside it is what keeps the two from disagreeing.
    """

    color: RGBA
    underline: RGBA | None = None
    verdict: TokenVerdict = TokenVerdict()

    @property
    def tag(self) -> str:  # 'n+1' | 'known' | 'freq-N' | 'base' (+ '/jlpt-Nx')
        return self.verdict.tag


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


@dataclass
class Scorer:
    known: KnownWords
    freq: FreqDict | None = None
    jlpt: JlptDict | None = None
    palette: Palette = field(default_factory=Palette)
    enable_known: bool = True
    enable_n_plus_one: bool = True
    enable_freq: bool = True
    enable_jlpt: bool = True
    freq_mode: str = "banded"  # 'banded' | 'single'
    freq_top_x: int = FREQ_BAND_TOP_X
    # How many bands `band()` splits `freq_top_x` into. Was `len(palette.freq_bands)`, which read the
    # band COUNT off the color table; the two can only ever agree because `Palette.from_config` cannot
    # resize that tuple. Held here so classification owes the palette nothing.
    freq_bands: int = 5
    min_sentence_words: int = 3
    # FSRS state overrides the binary known set when the same card appears in both.
    fsrs_snap: KnownSnap | None = None

    def _fsrs_state(self, t: Token) -> str | None:
        if not self.enable_known or self.fsrs_snap is None:
            return None
        return self.fsrs_snap.state(t.surface, t.lemma, t.reading)

    def _known_for(self, t: Token, fsrs_state: str | None) -> bool:
        if not self.enable_known:
            return False
        if fsrs_state is not None:
            return fsrs_state == "known"
        return self.known.is_known(t.surface, t.lemma, t.reading)

    def _is_known(self, t: Token) -> bool:
        """True for mature FSRS cards, otherwise fall back to the binary known set."""
        fsrs_state = self._fsrs_state(t)
        return self._known_for(t, fsrs_state)

    def is_known(self, t: Token) -> bool:
        """Public knownness seam shared by coloring and whole-track analysis."""
        return self._is_known(t)

    def verdict_line(self, tokens: list[Token]) -> list[TokenVerdict]:
        """Classify a whole line. The sentence-scoped part (N+1) is why this is per line, not per
        token — everything else `verdict` can answer alone."""
        states = [self._fsrs_state(t) for t in tokens]
        known = list(starmap(self._known_for, zip(tokens, states, strict=True)))
        n1 = (
            mark_n_plus_one(tokens, known, self.min_sentence_words)
            if self.enable_n_plus_one
            else set()
        )
        return [
            self._verdict(t, is_known=known[i], fsrs_state=states[i], is_n1=i in n1)
            for i, t in enumerate(tokens)
        ]

    def verdict(self, t: Token) -> TokenVerdict:
        """One token's classification, with no sentence context — so `n_plus` is never set. The
        tooltip's read: it wants the level and the rank, not the N+1 slot."""
        state = self._fsrs_state(t)
        return self._verdict(t, is_known=self._known_for(t, state), fsrs_state=state, is_n1=False)

    def score_line(self, tokens: list[Token]) -> list[TokenStyle]:
        return [self.palette.style_for(v) for v in self.verdict_line(tokens)]

    def _verdict(
        self, t: Token, *, is_known: bool, fsrs_state: str | None, is_n1: bool
    ) -> TokenVerdict:
        content = is_content(t)
        level = (
            self.jlpt.level(t.lemma, t.surface, t.reading)
            if (self.enable_jlpt and self.jlpt and content)
            else None
        )
        rank, band, single = self._frequency(t, content=content, level=level)
        return TokenVerdict(
            is_content=content,
            is_known=is_known,
            fsrs_state=fsrs_state,
            n_plus=1 if is_n1 else None,
            jlpt=level,
            freq_rank=rank,
            freq_band=band,
            freq_single=single,
        )

    def _frequency(
        self, t: Token, *, content: bool, level: str | None
    ) -> tuple[int | None, int | None, bool]:
        """``(rank, band, single)`` — frequency only speaks when there is no other signal (incl. JLPT),
        so a token with a level reports no rank at all."""
        if not (content and self.enable_freq and self.freq and level is None):
            return None, None, False
        rank = self.freq.rank(t.lemma, t.surface, t.reading)
        if rank is None:
            return None, None, False
        if self.freq_mode == "single":
            return rank, None, True
        return rank, FreqDict.band(rank, self.freq_top_x, self.freq_bands), False
