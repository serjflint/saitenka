"""WP5.3: the hovered-word decisions are a pure function of the facts they read."""

from __future__ import annotations

import pytest

from saitenka.app.hover_intents import (
    CopyToken,
    HoverCommand,
    HoverInputs,
    OpenKanji,
    SpeakText,
    reduce,
)
from saitenka.app.intents import Announce


def hovered(**overrides) -> HoverInputs:
    base = {
        "hovered": True,
        "surface": "習う",
        "reading": "ならう",
        "token_reading": "ならわ",
        "kanji": ("習",),
        "has_dictionaries": True,
        "anchored": True,
    }
    return HoverInputs(**(base | overrides))


@pytest.mark.parametrize("command", list(HoverCommand))
def test_nothing_hovered_decides_nothing(command: HoverCommand) -> None:
    """These keys stay eligible with no word under the cursor — they are not cue-gated — so the
    reducer is what makes pressing one harmless rather than the key routing."""
    assert reduce(command, HoverInputs()) == ()


def test_speaking_prefers_the_dictionary_form_reading() -> None:
    """The surface reads 習 as しゅう and the token reading gives the stem ならわ; only the
    dictionary form is right out loud, which is why all three are carried separately."""
    assert reduce(HoverCommand.SPEAK, hovered()) == (SpeakText("ならう"),)


def test_speaking_falls_back_through_token_reading_to_surface() -> None:
    assert reduce(HoverCommand.SPEAK, hovered(reading="")) == (SpeakText("ならわ"),)
    assert reduce(HoverCommand.SPEAK, hovered(reading="", token_reading="")) == (SpeakText("習う"),)


def test_copying_leaves_the_formatting_to_the_host() -> None:
    """The clipboard form is surface【reading】, which the host already owns; duplicating it in the
    effect would be a second place for that format to drift."""
    assert reduce(HoverCommand.COPY, hovered()) == (CopyToken(),)


def test_kanji_lookup_cycles_through_the_word() -> None:
    inputs = hovered(surface="勉強", kanji=("勉", "強"))

    assert reduce(HoverCommand.KANJI, inputs) == (OpenKanji("勉"),)
    assert reduce(HoverCommand.KANJI, hovered(kanji=("勉", "強"), kanji_index=1)) == (
        OpenKanji("強"),
    )
    assert reduce(HoverCommand.KANJI, hovered(kanji=("勉", "強"), kanji_index=2)) == (
        OpenKanji("勉"),
    )


def test_a_word_without_kanji_says_so() -> None:
    assert reduce(HoverCommand.KANJI, hovered(surface="ひらがな", kanji=())) == (
        Announce("no kanji in this word", "warn"),
    )


def test_a_word_without_kanji_says_so_even_unanchored() -> None:
    """Ordering that is easy to get backwards: "no kanji here" is true whether or not the word has
    a laid-out box, and the two failures are not the same thing to report."""
    assert reduce(HoverCommand.KANJI, hovered(kanji=(), anchored=False)) == (
        Announce("no kanji in this word", "warn"),
    )


def test_kanji_lookup_without_dictionaries_decides_nothing() -> None:
    assert reduce(HoverCommand.KANJI, hovered(has_dictionaries=False)) == ()


def test_an_unanchored_word_opens_no_popup() -> None:
    """Without a box there is nothing to anchor the popup to, and the executor asserts one exists."""
    assert reduce(HoverCommand.KANJI, hovered(anchored=False)) == ()


def test_the_reducer_reads_its_inputs_without_mutating_them() -> None:
    given = hovered()

    for command in HoverCommand:
        reduce(command, given)

    assert given == hovered()
