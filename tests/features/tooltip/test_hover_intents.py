"""WP5.3: the hovered-word decisions are a pure function of the facts they read."""

from __future__ import annotations

import pytest

from saitenka.app.features.tooltip.hover_intents import (
    CopyToken,
    HoverCommand,
    HoverInputs,
    OpenKanji,
    ResumePlayback,
    SetHoverPause,
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


#: Everything except the pause policy, which is deliberately answerable with nothing hovered.
_WORD_COMMANDS = [c for c in HoverCommand if c is not HoverCommand.TOGGLE_PAUSE]


@pytest.mark.parametrize("command", _WORD_COMMANDS)
def test_nothing_hovered_decides_nothing(command: HoverCommand) -> None:
    """These keys stay eligible with no word under the cursor — they are not cue-gated — so the
    reducer is what makes pressing one harmless rather than the key routing."""
    assert reduce(command, HoverInputs()) == ()


def test_the_pause_policy_toggles_with_nothing_hovered() -> None:
    """The exception, and the trap it guards: toggling auto-pause is what a user does *before*
    hovering anything, so a host that only fills the hover facts when a word is under the cursor
    hands this a default of False and flips the setting the wrong way."""
    assert reduce(HoverCommand.TOGGLE_PAUSE, HoverInputs(pause_on_tooltip=True)) == (
        SetHoverPause(enabled=False),
        Announce("hover auto-pause: off"),
    )


def test_disabling_the_policy_while_it_holds_playback_hands_it_back() -> None:
    """Setting the flag alone would leave the user paused with nothing left that would resume."""
    assert reduce(
        HoverCommand.TOGGLE_PAUSE, HoverInputs(pause_on_tooltip=True, paused_by_tooltip=True)
    ) == (SetHoverPause(enabled=False), ResumePlayback(), Announce("hover auto-pause: off"))


def test_enabling_the_policy_never_touches_playback() -> None:
    assert reduce(HoverCommand.TOGGLE_PAUSE, HoverInputs(paused_by_tooltip=True)) == (
        SetHoverPause(enabled=True),
        Announce("hover auto-pause: on"),
    )


@pytest.mark.parametrize(
    ("reading", "token_reading", "spoken"),
    [("ならう", "ならわ", "ならう"), ("", "ならわ", "ならわ"), ("", "", "習う")],
)
def test_speaking_prefers_the_dictionary_form_reading(reading, token_reading, spoken) -> None:
    assert reduce(HoverCommand.SPEAK, hovered(reading=reading, token_reading=token_reading)) == (
        SpeakText(spoken),
    )


def test_copying_leaves_the_formatting_to_the_host() -> None:
    """The clipboard form is surface【reading】, which the host already owns; duplicating it in the
    effect would be a second place for that format to drift."""
    assert reduce(HoverCommand.COPY, hovered()) == (CopyToken(),)


@pytest.mark.parametrize(("index", "kanji"), [(0, "勉"), (1, "強"), (2, "勉")])
def test_kanji_lookup_cycles_through_the_word(index, kanji) -> None:
    inputs = hovered(surface="勉強", kanji=("勉", "強"), kanji_index=index)

    assert reduce(HoverCommand.KANJI, inputs) == (OpenKanji(kanji),)


@pytest.mark.parametrize("anchored", [True, False])
def test_a_word_without_kanji_says_so(anchored) -> None:
    assert reduce(HoverCommand.KANJI, hovered(surface="ひらがな", kanji=(), anchored=anchored)) == (
        Announce("no kanji in this word", "warn"),
    )


def test_kanji_lookup_without_dictionaries_decides_nothing() -> None:
    assert reduce(HoverCommand.KANJI, hovered(has_dictionaries=False)) == ()


def test_an_unanchored_word_opens_no_popup() -> None:
    """Without a box there is nothing to anchor the popup to, and the executor asserts one exists."""
    assert reduce(HoverCommand.KANJI, hovered(anchored=False)) == ()
