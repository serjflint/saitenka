"""The hover-hysteresis state machine, as a pure decision.

Hovering is not "the cursor is over word N": a first open is instant, switching words needs a brief
dwell so dragging up to the tooltip does not hijack it onto every word passed over, leaving lingers
before hiding, and a word *inside* the tooltip opens its own popup after its own dwell. Four dwells,
two popups, and a policy per dwell for what happens when the timer cannot be armed at all.

That policy was spread across six functions that each read the host, armed a timer with a closure
over it, and mutated four fields. Nothing could observe a decision without letting it happen — which
is why the tests for it stubbed `SessionController.set_hover`, the one function item 5 splits.

Here the turn is `observe -> decide -> apply`. :func:`decide` is total and host-free, and the two
re-entries a dwell produces (:func:`elapsed`, :func:`refused`) are the same shape. The mutable
fields on ``TooltipState`` are its storage; the machine never reads them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.model import ScanBox


class Dwell(StrEnum):
    """The four dwells, named by the machine rather than by the timer service.

    The intent types already identify a dwell one for one, but a cancel has no intent instance to
    identify it with — and naming them here is what keeps the machine free of the app's timer
    vocabulary, so the mapping onto named deadlines is one table at the seam.
    """

    SWITCH = "switch"
    HIDE_TIP = "hide-tip"
    OPEN_SCAN = "open-scan"
    HIDE_NESTED = "hide-nested"


# --- what a dwell means when it elapses ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenScan:
    """Rest on a scan cell inside the tooltip long enough → open its nested popup."""

    scan: ScanBox


@dataclass(frozen=True, slots=True)
class SwitchTo:
    """Rest on a different subtitle word long enough → move the tooltip to it."""

    index: int


@dataclass(frozen=True, slots=True)
class HideTip:
    """The cursor left the word and both popups → hide the base tooltip."""


@dataclass(frozen=True, slots=True)
class HideNested:
    """The cursor left the scan cell → hide the nested popup."""


Intent = OpenScan | SwitchTo | HideTip | HideNested


# --- what the turn asks the caller to do --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Arm:
    dwell: Dwell
    delay: float
    intent: Intent


@dataclass(frozen=True, slots=True)
class Cancel:
    dwell: Dwell


@dataclass(frozen=True, slots=True)
class ShowWord:
    index: int


@dataclass(frozen=True, slots=True)
class RetireWord:
    """Nothing is hovered any more — the teardown half of the old `set_hover(-1)`."""


@dataclass(frozen=True, slots=True)
class OpenNested:
    scan: ScanBox


@dataclass(frozen=True, slots=True)
class CloseNested:
    pass


Decision = Arm | Cancel | ShowWord | RetireWord | OpenNested | CloseNested


# --- state and observation ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HoverState:
    """Everything the hysteresis owns, and nothing else.

    `hover` is deliberately absent: the shown word is written by the tooltip build and cleared when
    an anchor disappears, so a copy here would be a second writer of somebody else's fact. It
    arrives as an observation instead.
    """

    word_target: int | None = None  # word whose switch dwell is armed
    scan_target: str | None = None  # scan cell whose open dwell is armed
    tip_hide_pending: bool = False
    nest_hide_pending: bool = False


@dataclass(frozen=True, slots=True)
class HoverObservation:
    """What is true on screen this tick. `scan` is already link-filtered — a cross-reference is
    click-to-open, never hover-scan, so reading past a link must not spawn popups."""

    hover: int = -1  # the word the tooltip is showing, -1 for none
    word: int = -1  # the word under the cursor, -1 for none
    over_tip: bool = False
    over_nest: bool = False
    scan: ScanBox | None = None
    nest_open: bool = False
    nest_tail: str | None = None


@dataclass(frozen=True, slots=True)
class HoverDelays:
    scan: float
    hide: float
    switch: float


@dataclass(frozen=True, slots=True)
class HoverTurn:
    state: HoverState
    decisions: tuple[Decision, ...] = ()


# --- the machine ---------------------------------------------------------------------------------


def decide(state: HoverState, obs: HoverObservation, delays: HoverDelays) -> HoverTurn:
    """One hover tick. The nested half runs first, exactly as it did — the base tooltip's linger
    reads `over_nest`, so the two are ordered, not independent."""
    state, nested = _nested(state, obs, delays)
    state, word = _word(state, obs, delays)
    return HoverTurn(state, (*nested, *word))


def elapsed(state: HoverState, intent: Intent, *, nest_tail: str | None = None) -> HoverTurn:
    """A dwell fired. Every intent re-checks against the state it was armed from: a dwell that
    outlived what armed it must decide nothing, not act on a target the cursor has left."""
    match intent:
        case OpenScan(scan):
            if state.scan_target == scan.text and nest_tail != scan.text:
                return HoverTurn(state, (OpenNested(scan),))
            return HoverTurn(state)
        case SwitchTo(index):
            if state.word_target == index:
                return HoverTurn(replace(state, word_target=None), (ShowWord(index),))
            return HoverTurn(state)
        case HideTip():
            return HoverTurn(replace(state, tip_hide_pending=False), (RetireWord(),))
        case HideNested():
            return HoverTurn(replace(state, nest_hide_pending=False), (CloseNested(),))


def scrolled(state: HoverState, *, nested: bool) -> HoverTurn:
    """A popup was scrolled. Scrolling counts as interacting, so its linger is no longer pending.

    Here rather than as a field write at the scroll site because the hysteresis has one writer:
    a second one leaves the machine deciding against a state it does not hold, and the next tick
    silently re-arms — or fails to — against the stale half.
    """
    if nested:
        return HoverTurn(replace(state, nest_hide_pending=False))
    # The base tooltip also restarts the scan dwell: content moved under a stationary cursor, so
    # the cell it was resting on is not the cell it is on now.
    return HoverTurn(
        replace(state, tip_hide_pending=False, scan_target=None), (Cancel(Dwell.HIDE_TIP),)
    )


def refused(state: HoverState, intent: Intent) -> HoverTurn:
    """No timer could be armed. The whole fail-open/fail-closed policy, in one line.

    A hide that can never fire leaves a popup on screen for the rest of the session, and a switch
    that never fires strands the tooltip on a word the cursor left — so those happen at once. An
    *open* is the opposite: with no dwell to wait on, dragging across the panel would spawn a popup
    per cell passed over, so the popup stays shut and only the linger is lost.
    """
    if isinstance(intent, OpenScan):
        return HoverTurn(state)
    return elapsed(state, intent)


def _nested(
    state: HoverState, obs: HoverObservation, delays: HoverDelays
) -> tuple[HoverState, tuple[Decision, ...]]:
    if obs.scan is not None:
        state = replace(state, nest_hide_pending=False)
        out: tuple[Decision, ...] = (Cancel(Dwell.HIDE_NESTED),)
        text = obs.scan.text
        if text == state.scan_target:
            return state, out  # this cell's dwell is already armed, or already resolved
        state = replace(state, scan_target=text)
        if obs.nest_tail == text:
            return state, out  # already shown
        return state, (*out, Arm(Dwell.OPEN_SCAN, delays.scan, OpenScan(obs.scan)))
    if obs.over_nest:
        state = replace(state, scan_target=None, nest_hide_pending=False)
        return state, (Cancel(Dwell.HIDE_NESTED),)
    state = replace(state, scan_target=None)
    out = (Cancel(Dwell.OPEN_SCAN),)
    if not obs.nest_open or state.nest_hide_pending:
        return state, out
    state = replace(state, nest_hide_pending=True)
    return state, (*out, Arm(Dwell.HIDE_NESTED, delays.hide, HideNested()))


def _word(
    state: HoverState, obs: HoverObservation, delays: HoverDelays
) -> tuple[HoverState, tuple[Decision, ...]]:
    if obs.word >= 0:
        state, out = _switch(state, obs, delays)
        state = replace(state, tip_hide_pending=False)
        return state, (*out, Cancel(Dwell.HIDE_TIP))
    if obs.over_tip or obs.over_nest:  # kept alive while the cursor is on either popup
        state = replace(state, word_target=None, tip_hide_pending=False)
        return state, (Cancel(Dwell.HIDE_TIP),)
    if obs.hover == -1:
        return state, ()
    state = replace(state, word_target=None)
    cancel: tuple[Decision, ...] = (Cancel(Dwell.SWITCH),)
    if state.tip_hide_pending:
        return state, cancel
    state = replace(state, tip_hide_pending=True)
    return state, (*cancel, Arm(Dwell.HIDE_TIP, delays.hide, HideTip()))


def _switch(
    state: HoverState, obs: HoverObservation, delays: HoverDelays
) -> tuple[HoverState, tuple[Decision, ...]]:
    if obs.word == obs.hover:
        return replace(state, word_target=None), (Cancel(Dwell.SWITCH),)
    if obs.hover < 0:  # nothing open yet: no hijack to guard against, so open instantly
        return replace(state, word_target=None), (ShowWord(obs.word),)
    if obs.word == state.word_target:
        return state, ()  # this word's dwell is already armed
    state = replace(state, word_target=obs.word)
    return state, (Arm(Dwell.SWITCH, delays.switch, SwitchTo(obs.word)),)
