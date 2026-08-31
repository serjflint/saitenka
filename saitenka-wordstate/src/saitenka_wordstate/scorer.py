"""The word-state scorer: everything the user knows about the tokens of one line.

Palette-free by construction. It answers *what a token is*; `saitenka.app.scoring.Palette` answers
what colour that is. Splitting them is what lets this ship as a library — a scorer whose output is
RGBA read off a config-loaded colour table is an application.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import starmap
from typing import TYPE_CHECKING

from saitenka_wordstate.verdict import (
    FREQ_BAND_TOP_X,
    TokenVerdict,
    freq_band,
    is_content,
    mark_n_plus_one,
)

if TYPE_CHECKING:
    from saitenka_tokenize.japanese import Token

    from saitenka_wordstate.fsrs import KnownSnap
    from saitenka_wordstate.known import KnownWords
    from saitenka_wordstate.verdict import FrequencyTable, LevelTable


@dataclass
class Scorer:
    known: KnownWords
    freq: FrequencyTable | None = None
    jlpt: LevelTable | None = None
    enable_known: bool = True
    enable_n_plus_one: bool = True
    enable_freq: bool = True
    enable_jlpt: bool = True
    freq_mode: str = "banded"  # 'banded' | 'single'
    freq_top_x: int = FREQ_BAND_TOP_X
    # How many bands `freq_band` splits `freq_top_x` into. Held here rather than read off a colour
    # table's length, which is what tied classification to the palette.
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
        return rank, freq_band(rank, self.freq_top_x, self.freq_bands), False
