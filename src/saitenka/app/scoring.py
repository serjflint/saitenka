"""Paint a token verdict. The classification is `saitenka_wordstate`; this is the colour half.

Text-color priority: **N+1 > forgotten > known > learning > young > frequency-band > base**
(name-match is out of scope). JLPT is an **additive underline**, and frequency is suppressed when a
token has a JLPT level (matching SubMiner's "frequency only if no other signal").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from saitenka_wordstate.verdict import MATURITY_STATES, TokenVerdict

if TYPE_CHECKING:
    from saitenka_tokenize.japanese import Token
    from saitenka_wordstate.scorer import Scorer

RGBA = tuple[int, int, int, int]


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


@dataclass(frozen=True)
class Coloring:
    """The application's scorer: a word-state `Scorer` plus the palette that draws its verdicts.

    The two are separate because a scorer holding a config-loaded colour table is not a library —
    that is the edge the extraction cut. They are paired *here*, at the app boundary, because every
    consumer that wants one wants the other in the same breath.

    The forwarding below is deliberate and finite: it is exactly what callers read, spelled out, so
    the surface this facade owes the package is visible rather than resolved by attribute magic.
    """

    scorer: Scorer
    palette: Palette = field(default_factory=Palette)

    def score_line(self, tokens: list[Token]) -> list[TokenStyle]:
        """Classify the line, then colour it."""
        return [self.palette.style_for(v) for v in self.scorer.verdict_line(tokens)]

    def verdict(self, token: Token) -> TokenVerdict:
        return self.scorer.verdict(token)

    def verdict_line(self, tokens: list[Token]) -> list[TokenVerdict]:
        return self.scorer.verdict_line(tokens)

    def is_known(self, token: Token) -> bool:
        return self.scorer.is_known(token)

    @property
    def known(self):
        return self.scorer.known

    @property
    def freq(self):
        return self.scorer.freq

    @property
    def jlpt(self):
        return self.scorer.jlpt

    @property
    def fsrs_snap(self):
        return self.scorer.fsrs_snap

    @property
    def freq_top_x(self) -> int:
        return self.scorer.freq_top_x

    @property
    def min_sentence_words(self) -> int:
        return self.scorer.min_sentence_words

    @property
    def enable_freq(self) -> bool:
        return self.scorer.enable_freq

    @property
    def enable_jlpt(self) -> bool:
        return self.scorer.enable_jlpt
