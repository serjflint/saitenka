"""Whether a tooltip is holding playback paused, and therefore owes a resume.

mpv owns "is paused"; this owns "we are why". The two are not the same fact, and the session cannot
ask for the second one — a hover that paused an already-paused video must not hand playback back
when it goes away, and only the code that paused it knows which case this was.

It is a machine because releasing had to be asked before it was done: both call sites read the flag,
branched, resumed, and then cleared it somewhere else entirely. Here the claim answers with the
resume or with nothing, so there is no state to clear at the call site and no way to resume a pause
that was never ours.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PauseClaim:
    """`held` is "a tooltip show is what paused playback"."""

    held: bool = False


@dataclass(frozen=True, slots=True)
class ResumePlayback:
    """Give playback back. Published only when the claim is ours to release."""


@dataclass(frozen=True, slots=True)
class PauseTurn:
    state: PauseClaim
    decisions: tuple[ResumePlayback, ...] = ()


def claimed(state: PauseClaim, *, paused: bool) -> PauseTurn:
    """A show tried to pause. `paused` is whether *this* call is what paused it — false when the
    policy is off, or when the user had already paused, and in both cases nothing is owed."""
    if not paused:
        return PauseTurn(state)
    return PauseTurn(PauseClaim(held=True))


def released(state: PauseClaim) -> PauseTurn:
    """The tooltip is going away, or the user turned the policy off."""
    if not state.held:
        return PauseTurn(state)
    return PauseTurn(PauseClaim(), (ResumePlayback(),))
