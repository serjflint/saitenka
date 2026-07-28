"""Score subtitle tokens into per-word colors, reproducing SubMiner's model.

Text-color priority: **N+1 > known > frequency-band > base** (name-match is out of scope). JLPT is an
**additive underline**, and frequency is suppressed when a token has a JLPT level (matching SubMiner's
"frequency only if no other signal"). Coloring keys on the **lemma** with a reading fallback; function
words stay base. The Saitenka edge: `KnownWords` comes from Anki (FSRS-aware later), so forgotten words
re-surface as unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from overlay.app.wordlists import FreqDict, JlptDict, KnownWords

if TYPE_CHECKING:
    from overlay.app.fsrs import KnownSnap
    from overlay.app.tokenize import Token

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


@dataclass(frozen=True)
class Palette:
    """SubMiner defaults (Catppuccin Macchiato)."""

    base: RGBA = _hex("#cad3f5")
    known: RGBA = _hex("#a6da95")
    # Forgotten words resurface between known (green) and base (grey).
    forgotten: RGBA = _hex("#ee99a0")  # Macchiato flamingo — visually distinct from base+known
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


@dataclass(frozen=True)
class TokenStyle:
    color: RGBA
    underline: RGBA | None = None
    tag: str = "base"  # 'n+1' | 'known' | 'freq-N' | 'base' (+ '/jlpt-Nx')


def _is_content(t: Token) -> bool:
    """Content word for coloring/N+1 = anything that isn't a function-word POS (SubMiner blacklist)."""
    return bool(t.surface.strip()) and t.pos not in FUNCTION_POS


@dataclass(frozen=True)
class _StyleCtx:
    """Immutable bundle passed to each `_style_*` rule below — avoids a 5-parameter signature."""

    t: Token
    is_known: bool
    is_n1: bool
    content: bool
    level: str | None
    underline: RGBA | None


def mark_n_plus_one(tokens: list[Token], known: list[bool], min_words: int = 3) -> set[int]:
    """Indices of the single unknown content word in each ≥min_words-content-word sentence."""
    targets: set[int] = set()
    start = 0
    for i in range(len(tokens) + 1):
        boundary = i == len(tokens) or any(c in SENT_BOUNDARY for c in tokens[i].surface)
        if not boundary:
            continue
        sent = range(start, min(i + 1, len(tokens)))
        content = [j for j in sent if _is_content(tokens[j])]
        candidates = [
            j
            for j in content
            if not known[j] and not tokens[j].is_kana_only and not tokens[j].is_proper_noun
        ]
        if len(content) >= min_words and len(candidates) == 1:
            targets.add(candidates[0])
        start = i + 1
    return targets


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
    min_sentence_words: int = 3
    # FSRS knownness snapshot — gives the forgotten tint when set.
    fsrs_snap: KnownSnap | None = None

    def _is_known(self, t: Token) -> bool:
        """True when the word is in KnownWords OR in the FSRS snapshot as 'known'."""
        if not self.enable_known:
            return False
        if self.known.is_known(t.surface, t.lemma, t.reading):
            return True
        if self.fsrs_snap is not None:
            return self.fsrs_snap.is_known(t.surface, t.lemma, t.reading)
        return False

    def _is_forgotten(self, t: Token) -> bool:
        """True when the FSRS snapshot marks the word as 'forgotten'."""
        return (
            self.fsrs_snap is not None
            and self.enable_known
            and self.fsrs_snap.is_forgotten(t.surface, t.lemma, t.reading)
        )

    def score_line(self, tokens: list[Token]) -> list[TokenStyle]:
        known = [self._is_known(t) for t in tokens]
        n1 = (
            mark_n_plus_one(tokens, known, self.min_sentence_words)
            if self.enable_n_plus_one
            else set()
        )
        return [self._style(t, is_known=known[i], is_n1=i in n1) for i, t in enumerate(tokens)]

    def _style_n1(self, p: Palette, ctx: _StyleCtx) -> TokenStyle | None:
        # N+1 needs no `content` gate — mark_n_plus_one already excludes function words.
        if ctx.is_n1:
            return TokenStyle(p.n_plus_one, ctx.underline, self._tag("n+1", ctx.level))
        return None

    def _style_known(self, p: Palette, ctx: _StyleCtx) -> TokenStyle | None:
        # Gates on `content` so a function word stays base even when it lands in KnownWords (the
        # documented model: function words never take a colour).
        if ctx.content and ctx.is_known:
            return TokenStyle(p.known, ctx.underline, self._tag("known", ctx.level))
        return None

    def _style_forgotten(self, p: Palette, ctx: _StyleCtx) -> TokenStyle | None:
        # Forgotten words resurface visibly with the forgotten tint (between known/unknown).
        if ctx.content and self._is_forgotten(ctx.t):
            return TokenStyle(p.forgotten, ctx.underline, self._tag("forgotten", ctx.level))
        return None

    def _style_freq(self, p: Palette, ctx: _StyleCtx) -> TokenStyle | None:
        # Frequency only when there is no other signal (incl. JLPT).
        if not (ctx.content and self.enable_freq and self.freq and ctx.level is None):
            return None
        rank = self.freq.rank(ctx.t.lemma, ctx.t.surface, ctx.t.reading)
        if rank is None:
            return None
        if self.freq_mode == "single":
            return TokenStyle(p.freq_single, ctx.underline, "freq")
        band = FreqDict.band(rank, self.freq_top_x, len(p.freq_bands))
        return TokenStyle(p.freq_bands[band - 1], ctx.underline, f"freq-{band}") if band else None

    _STYLE_RULES: ClassVar = (_style_n1, _style_known, _style_forgotten, _style_freq)

    def _style(self, t: Token, *, is_known: bool, is_n1: bool) -> TokenStyle:
        p = self.palette
        content = _is_content(t)
        level = (
            self.jlpt.level(t.lemma, t.surface, t.reading)
            if (self.enable_jlpt and self.jlpt and content)
            else None
        )
        underline = p.jlpt.get(level) if level else None
        ctx = _StyleCtx(t, is_known, is_n1, content, level, underline)
        for rule in self._STYLE_RULES:
            style = rule(self, p, ctx)
            if style is not None:
                return style
        return TokenStyle(p.base, underline, self._tag("base", level))

    @staticmethod
    def _tag(base: str, level: str | None) -> str:
        return f"{base}/jlpt-{level}" if level else base
