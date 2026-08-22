"""Pure reducer for the capture commands.

Mining and bookmarking both answer "keep what is on screen for later", and both are gated on
something being there to keep — so they share a reducer. Mining is also the runtime's one command
family reaching an external service, which is why its eligibility is stated separately from the
request it produces: whether Anki is configured at all, and whether there is anything under the
cursor to mine, are different answers with different remedies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from saitenka.app.intents import Announce


class MineCommand(StrEnum):
    """The wire names this reducer owns. Each maps to exactly one intent."""

    #: "word", not "token": a tokenizer token is a different thing in this codebase, and the
    #: linter reads a bare `TOKEN = "token"` as a credential.
    WORD = "word"
    #: The same target with a motion screenshot, whatever `[mine].animated_screenshot` says.
    WORD_VIDEO = "word-video"
    EPISODE = "episode"
    #: Bookmark the cue on screen into the local backlog. Not an Anki path — no `configured` gate.
    BOOKMARK_CUE = "bookmark-cue"


@dataclass(frozen=True, slots=True)
class MineInputs:
    """Every fact the mining commands decide from, read once before deciding."""

    #: Anki *and* a mining profile are both present. Mining is optional, so this is an ordinary
    #: session state, not an error.
    configured: bool = False
    #: The token index the miner would mine, or None when nothing is under the cursor.
    target: int | None = None
    #: A cue with text and timings is on screen — what a bookmark captures.
    has_active_cue: bool = False


@dataclass(frozen=True, slots=True)
class MineToken:
    index: int
    #: None defers to the configured default; True forces a motion screenshot.
    animated: bool | None = None


@dataclass(frozen=True, slots=True)
class MineEpisode:
    """Mine every eligible word in the loaded episode."""


@dataclass(frozen=True, slots=True)
class BookmarkCue:
    """Toggle the on-screen cue in the local backlog."""


type MineEffect = MineToken | MineEpisode | BookmarkCue | Announce


def _token(inputs: MineInputs, *, animated: bool | None) -> tuple[MineEffect, ...]:
    if not inputs.configured:
        # Deliberately quiet, and not a silent no-op: an unconfigured session logs the reason at
        # the executor. Announcing here would toast on every stray key for users who never set
        # Anki up, which the optional-extras contract exists to avoid.
        return ()
    if inputs.target is None:
        return (Announce("no word to mine", "warn"),)
    return (MineToken(inputs.target, animated=animated),)


_REDUCERS = {
    MineCommand.WORD: lambda inputs: _token(inputs, animated=None),
    MineCommand.WORD_VIDEO: lambda inputs: _token(inputs, animated=True),
    # No eligibility of its own: the bulk miner reads the episode index, not the cursor, and
    # answers for itself when there is nothing to do.
    MineCommand.EPISODE: lambda _inputs: (MineEpisode(),),
    MineCommand.BOOKMARK_CUE: lambda inputs: (
        (BookmarkCue(),)
        if inputs.has_active_cue
        else (Announce("no active cue to bookmark", "warn"),)
    ),
}


def reduce(command: MineCommand, inputs: MineInputs) -> tuple[MineEffect, ...]:
    """Decide one mining command."""
    return _REDUCERS[command](inputs)
