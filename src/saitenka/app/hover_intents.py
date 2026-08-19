"""Pure reducer for the hovered-word commands (WP5.3 of the runtime migration).

Speak, copy and kanji-lookup all answer the same first question — is there a word under the cursor
— and then diverge. Deciding that once, from a snapshot, is what stops each command growing its own
slightly different notion of "hovered".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from saitenka.app.intents import Announce


class HoverCommand(StrEnum):
    """The wire names this reducer owns. Each maps to exactly one intent."""

    SPEAK = "speak"
    COPY = "copy"
    KANJI = "kanji"
    TOGGLE_PAUSE = "toggle-pause"


@dataclass(frozen=True, slots=True)
class HoverInputs:
    """Every fact the hovered-word commands decide from, read once before deciding."""

    #: A token is under the cursor. False makes every command here decide nothing — the commands
    #: stay eligible (they are not cue-gated), so this is where "nothing hovered" is handled.
    hovered: bool = False
    surface: str = ""
    #: Dictionary-form reading (習う → ならう), already resolved by the host. Speaking the surface
    #: reads 習 as しゅう, and the bare token reading gives the stem — both wrong out loud.
    reading: str = ""
    token_reading: str = ""
    #: Ideographs in the hovered surface, in order.
    kanji: tuple[str, ...] = field(default_factory=tuple)
    #: How many times kanji lookup has already fired for this word; `k` cycles.
    kanji_index: int = 0
    has_dictionaries: bool = False
    #: The hovered token has a laid-out box to anchor the popup to.
    anchored: bool = False
    #: Hover auto-pause policy. Unlike everything above it, these are read whether or not a word is
    #: hovered — toggling the setting is exactly what a user does *before* hovering anything.
    pause_on_tooltip: bool = False
    paused_by_tooltip: bool = False


@dataclass(frozen=True, slots=True)
class SpeakText:
    text: str


@dataclass(frozen=True, slots=True)
class CopyToken:
    """Copy the hovered token, formatted by the host as surface【reading】."""


@dataclass(frozen=True, slots=True)
class OpenKanji:
    char: str


@dataclass(frozen=True, slots=True)
class SetHoverPause:
    enabled: bool


@dataclass(frozen=True, slots=True)
class ResumePlayback:
    """Give playback back, because the tooltip that took it no longer may hold it."""


type HoverEffect = SpeakText | CopyToken | OpenKanji | SetHoverPause | ResumePlayback | Announce


def _speak(inputs: HoverInputs) -> tuple[HoverEffect, ...]:
    if not inputs.hovered:
        return ()
    return (SpeakText(inputs.reading or inputs.token_reading or inputs.surface),)


def _copy(inputs: HoverInputs) -> tuple[HoverEffect, ...]:
    return (CopyToken(),) if inputs.hovered else ()


def _kanji(inputs: HoverInputs) -> tuple[HoverEffect, ...]:
    if not inputs.hovered or not inputs.has_dictionaries:
        return ()
    if not inputs.kanji:
        return (Announce("no kanji in this word", "warn"),)
    # Anchoring is checked after the "no kanji" answer on purpose: a word with no kanji should say
    # so whether or not it happens to have a box, and the two failures are not the same thing.
    if not inputs.anchored:
        return ()
    return (OpenKanji(inputs.kanji[inputs.kanji_index % len(inputs.kanji)]),)


def _toggle_pause(inputs: HoverInputs) -> tuple[HoverEffect, ...]:
    enabled = not inputs.pause_on_tooltip
    effects: list[HoverEffect] = [SetHoverPause(enabled=enabled)]
    # Turning the policy off while it is actively holding playback has to hand playback back; the
    # setting alone would leave the user paused with nothing left that would ever resume them.
    if not enabled and inputs.paused_by_tooltip:
        effects.append(ResumePlayback())
    effects.append(Announce(f"hover auto-pause: {'on' if enabled else 'off'}"))
    return tuple(effects)


_REDUCERS = {
    HoverCommand.SPEAK: _speak,
    HoverCommand.COPY: _copy,
    HoverCommand.KANJI: _kanji,
    HoverCommand.TOGGLE_PAUSE: _toggle_pause,
}


def reduce(command: HoverCommand, inputs: HoverInputs) -> tuple[HoverEffect, ...]:
    """Decide one hovered-word command.

    An empty result means "nothing is hovered", which is not worth a toast — the user pressed a
    word key with no word under the cursor and can see that. The command still terminates with an
    outcome in the runtime's command ledger.
    """
    return _REDUCERS[command](inputs)
