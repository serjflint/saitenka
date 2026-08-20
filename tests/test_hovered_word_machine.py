"""What the session resolved about the hovered word, as a pure function of what it held."""

from __future__ import annotations

from saitenka.runtime.hovered_word import (
    HoveredWord,
    HoveredWordTurn,
    forgotten,
    kanji_advanced,
    read_as,
    resolved,
    revised,
)

CYCLING = HoveredWord(meta="cat", reading="ねこ", kanji=2)


def test_a_new_answer_restarts_the_kanji_cycle() -> None:
    """The reset used to be a `= 0` written at three sites, each of which had to remember. A new
    answer *is* the restart, so a site that forgets cannot exist."""
    assert resolved(CYCLING, "dog").state == HoveredWord(meta="dog", reading="ねこ", kanji=0)


def test_a_corrected_answer_about_the_same_word_keeps_the_cycle() -> None:
    """Mining the hovered term revises what is known about it; the word did not change, so where
    `k` had got to must not either."""
    assert revised(CYCLING, "cat-mined").state == HoveredWord(
        meta="cat-mined", reading="ねこ", kanji=2
    )


def test_forgetting_drops_the_reading_with_the_answer() -> None:
    """A reading held past its word is what TTS would say next, and the cycle would index a word
    that is no longer there."""
    assert forgotten() == HoveredWordTurn(HoveredWord())


def test_a_reading_is_recorded_without_disturbing_the_answer() -> None:
    assert read_as(CYCLING, "ねこちゃん").state == HoveredWord(
        meta="cat", reading="ねこちゃん", kanji=2
    )


def test_the_cycle_advances_unbounded() -> None:
    """The caller knows how many kanji the word has and takes this modulo that — wrapping here
    would need the word's length, which is exactly what the slice must not look inside its answer
    to learn."""
    assert kanji_advanced(kanji_advanced(CYCLING).state).state.kanji == 4


def test_the_machine_never_reads_the_answer_it_carries() -> None:
    class Unreadable:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"the slice read {name} off a resolved answer")

    answer = Unreadable()
    state = resolved(HoveredWord(), answer).state
    assert kanji_advanced(state).state.meta is answer
    assert forgotten().state.meta is None
