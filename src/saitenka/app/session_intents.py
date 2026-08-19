"""Pure reducer for the session-wide commands (WP5.3 of the runtime migration).

Hiding every saitenka surface is a short sequence whose *order* is the whole content: the tooltip
has to go before the pixels do, the secondary track is released only once nothing is drawing it,
and the subtitle pipeline suspends last. Written as a list of effects that order is a value a test
can read, rather than the order of statements in a method.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from saitenka.app.intents import DismissHover


class SessionCommand(StrEnum):
    """The wire names this reducer owns."""

    TOGGLE_OVERLAY = "toggle-overlay"
    CYCLE_PROFILE = "cycle-profile"


@dataclass(frozen=True, slots=True)
class SessionInputs:
    """Every fact the session commands decide from, read once before deciding."""

    overlay_visible: bool = True
    #: Whether the user wants a secondary-language line. Deliberately not "is it visible": the
    #: overlay is hidden at the moment this is read, so that question answers False for the wrong
    #: reason and the line never comes back.
    translation_wanted: bool = False
    #: Configured reading profiles, and which one is live.
    profile_count: int = 1
    profile_index: int = 0


@dataclass(frozen=True, slots=True)
class SetSurfacesVisible:
    visible: bool


@dataclass(frozen=True, slots=True)
class ReleaseSecondarySubtitles:
    """Hand the secondary track back to mpv; nothing of ours is drawing it."""


@dataclass(frozen=True, slots=True)
class SuspendSubtitles:
    """Stop the subtitle pipeline for as long as the overlay is hidden."""


@dataclass(frozen=True, slots=True)
class ResumeSubtitles:
    pass


@dataclass(frozen=True, slots=True)
class ShowTranslation:
    """Re-acquire the secondary track and draw it."""


@dataclass(frozen=True, slots=True)
class SwitchProfile:
    """Make profile ``index`` live.

    Whether it *can* be made live is the executor's to find out — resolving a tokenizer and
    re-scoping dictionaries is I/O, and a failure there reverts atomically. This says which one to
    try, which is the part that is a decision.
    """

    index: int


type SessionEffect = (
    SetSurfacesVisible
    | ReleaseSecondarySubtitles
    | SuspendSubtitles
    | ResumeSubtitles
    | ShowTranslation
    | SwitchProfile
    | DismissHover
)


def _toggle_overlay(inputs: SessionInputs) -> tuple[SessionEffect, ...]:
    if inputs.overlay_visible:
        return (
            # The tooltip first: it is one of the surfaces about to be hidden, and tearing it down
            # afterwards would resume playback (hover auto-pause) against a session that has just
            # gone dark.
            DismissHover(),
            SetSurfacesVisible(visible=False),
            # Only once nothing of ours draws it — releasing first would let mpv paint the
            # secondary line for the frames before the hide lands.
            ReleaseSecondarySubtitles(),
            SuspendSubtitles(),
        )
    shown: tuple[SessionEffect, ...] = (
        SetSurfacesVisible(visible=True),
        ResumeSubtitles(),
    )
    # After the resume, so the pipeline that lays the cue out is running before the secondary line
    # is drawn against it.
    return (*shown, ShowTranslation()) if inputs.translation_wanted else shown


def _cycle_profile(inputs: SessionInputs) -> tuple[SessionEffect, ...]:
    # Inert on the single-profile path, which is almost every session: there is nothing to cycle
    # to, and announcing that on every keypress would be noise rather than information.
    if inputs.profile_count <= 1:
        return ()
    return (SwitchProfile((inputs.profile_index + 1) % inputs.profile_count),)


_REDUCERS = {
    SessionCommand.TOGGLE_OVERLAY: _toggle_overlay,
    SessionCommand.CYCLE_PROFILE: _cycle_profile,
}


def reduce(command: SessionCommand, inputs: SessionInputs) -> tuple[SessionEffect, ...]:
    """Decide one session command."""
    return _REDUCERS[command](inputs)
