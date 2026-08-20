"""What the session resolved about the word under the cursor, and where `k` is in its kanji.

The cycle is why these are one feature rather than three fields. It restarts on a new word, and
that restart used to be a `= 0` written at three separate sites — the teardown, the show, and the
clear — each of which had to remember. Here a new answer *is* the restart, so a site that forgets
cannot exist. Correcting an answer about the same word (a mine landing on the hovered term) keeps
the cycle, which is the distinction the two verbs carry and the reason a single "set" would be
wrong.

The answer itself rides opaquely: `runtime` cannot name a dictionary term, a phrase span or a mined
flag, and nothing here branches on one. `None` is "nothing resolved" — the app supplies whatever it
spells that as.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class HoveredWord:
    """The lookup's answer, the dictionary-form reading TTS says, and the kanji cycle's position."""

    meta: object | None = None
    reading: str = ""
    kanji: int = 0


@dataclass(frozen=True, slots=True)
class HoveredWordTurn:
    state: HoveredWord


def resolved(state: HoveredWord, meta: object) -> HoveredWordTurn:
    """A lookup answered about the word now under the cursor. The cycle restarts with it."""
    return HoveredWordTurn(replace(state, meta=meta, kanji=0))


def revised(state: HoveredWord, meta: object) -> HoveredWordTurn:
    """A corrected answer about the *same* word, so the cycle is not disturbed."""
    return HoveredWordTurn(replace(state, meta=meta))


def read_as(state: HoveredWord, reading: str) -> HoveredWordTurn:
    """The shown panel named a dictionary reading — what TTS says, not what the surface spells."""
    return HoveredWordTurn(replace(state, reading=reading))


def forgotten() -> HoveredWordTurn:
    """Nothing is hovered, or the hover moved and its answer has not arrived yet. Everything goes:
    a reading held past its word is what TTS would say next, and the cycle indexes a word that is
    no longer there — which is why this takes no prior state: there is nothing in it to keep."""
    return HoveredWordTurn(HoveredWord())


def kanji_advanced(state: HoveredWord) -> HoveredWordTurn:
    """`k` opened one kanji; the next press wants the one after it. Unbounded on purpose — the
    caller knows how many characters the word has and takes it modulo that."""
    return HoveredWordTurn(replace(state, kanji=state.kanji + 1))
