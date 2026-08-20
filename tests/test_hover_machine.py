"""The hover hysteresis, decided rather than performed.

These used to be `Reader` tests that stubbed `set_hover` — the only way to watch a dwell decide
without letting it build a panel. The machine is pure, so the decision is the return value and the
oracle is what it says, not which method got called.

The refusal policy has no `Reader` test at all and could not have one: it fires when a deadline
cannot be armed, which on a real Reader means no timer service.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from saitenka.app.hover_machine import (
    Arm,
    Cancel,
    CloseNested,
    HideNested,
    HideTip,
    HoverDelays,
    HoverObservation,
    HoverState,
    OpenNested,
    OpenScan,
    RetireWord,
    ShowWord,
    SwitchTo,
    decide,
    elapsed,
    refused,
)
from saitenka.app.lifecycle_timers import LifecycleTimerKind

DELAYS = HoverDelays(scan=0.2, hide=0.4, switch=0.3)


def cell(text: str):
    """A scan cell — the machine reads only its ``text``."""
    return SimpleNamespace(text=text, x=0, y=0, w=8, h=8)


FRESH = HoverState()  # nothing armed, nothing pending — the machine's start state


def turn(state: HoverState = FRESH, **obs):
    return decide(state, HoverObservation(**obs), DELAYS)


def kinds(decisions, cls) -> list:
    return [d for d in decisions if isinstance(d, cls)]


# --- switching words -----------------------------------------------------------------------------


def test_the_first_word_opens_instantly_with_no_dwell() -> None:
    """Nothing is up, so there is no tooltip to hijack — the dwell only exists to protect one."""
    result = turn(hover=-1, word=0)

    assert kinds(result.decisions, ShowWord) == [ShowWord(0)]
    assert not kinds(result.decisions, Arm)


def test_switching_to_another_word_arms_a_dwell_instead_of_switching() -> None:
    result = turn(hover=0, word=1)

    assert not kinds(result.decisions, ShowWord)
    assert kinds(result.decisions, Arm) == [Arm(LifecycleTimerKind.HOVER_SWITCH, 0.3, SwitchTo(1))]
    assert result.state.word_target == 1


def test_resting_on_the_same_new_word_does_not_re_arm_its_dwell() -> None:
    """Re-arming every tick would push the switch out forever — the cursor never gets to rest."""
    first = turn(hover=0, word=1)
    second = decide(first.state, HoverObservation(hover=0, word=1), DELAYS)

    assert kinds(first.decisions, Arm)
    assert not kinds(second.decisions, Arm)


def test_a_dwell_that_elapses_on_the_word_it_was_armed_for_switches() -> None:
    armed = turn(hover=0, word=1).state

    result = elapsed(armed, SwitchTo(1))

    assert result.decisions == (ShowWord(1),)
    assert result.state.word_target is None


def test_a_dwell_that_outlived_its_target_switches_nothing() -> None:
    """Brushing word 1 en route to the tooltip arms its dwell; arriving at the tooltip clears the
    target. The late timer must not then hijack the tooltip onto the word the cursor passed over."""
    armed = turn(hover=0, word=1).state
    left = decide(armed, HoverObservation(hover=0, word=-1, over_tip=True), DELAYS).state

    assert left.word_target is None
    assert elapsed(left, SwitchTo(1)).decisions == ()


def test_returning_to_the_shown_word_cancels_the_pending_switch() -> None:
    armed = turn(hover=0, word=1).state

    result = decide(armed, HoverObservation(hover=0, word=0), DELAYS)

    assert result.state.word_target is None
    assert Cancel(LifecycleTimerKind.HOVER_SWITCH) in result.decisions


# --- lingering -----------------------------------------------------------------------------------


def test_leaving_the_word_lingers_rather_than_hiding_at_once() -> None:
    result = turn(hover=0, word=-1)

    assert not kinds(result.decisions, RetireWord)
    assert kinds(result.decisions, Arm) == [Arm(LifecycleTimerKind.TOOLTIP_HIDE, 0.4, HideTip())]
    assert result.state.tip_hide_pending


def test_the_linger_is_armed_once_not_once_per_tick() -> None:
    first = turn(hover=0, word=-1)
    second = decide(first.state, HoverObservation(hover=0, word=-1), DELAYS)

    assert kinds(first.decisions, Arm)
    assert not kinds(second.decisions, Arm)


@pytest.mark.parametrize("region", ["over_tip", "over_nest"])
def test_the_cursor_on_either_popup_keeps_the_tooltip_alive(region: str) -> None:
    """The tooltip is kept up by the cursor being on it — and by being on the nested popup it
    spawned, which sits on top of it."""
    pending = turn(hover=0, word=-1).state

    result = decide(pending, HoverObservation(hover=0, word=-1, **{region: True}), DELAYS)

    assert not result.state.tip_hide_pending
    assert Cancel(LifecycleTimerKind.TOOLTIP_HIDE) in result.decisions


def test_nothing_hovered_decides_nothing() -> None:
    assert turn(hover=-1, word=-1).decisions == (Cancel(LifecycleTimerKind.SCAN_OPEN),)


def test_an_elapsed_linger_retires_the_hover() -> None:
    pending = turn(hover=0, word=-1).state

    result = elapsed(pending, HideTip())

    assert result.decisions == (RetireWord(),)
    assert not result.state.tip_hide_pending


# --- the nested popup ----------------------------------------------------------------------------


def test_resting_on_a_scan_cell_arms_its_open_dwell() -> None:
    scan = cell("本")
    result = turn(hover=0, word=-1, over_tip=True, scan=scan)

    assert kinds(result.decisions, Arm) == [Arm(LifecycleTimerKind.SCAN_OPEN, 0.2, OpenScan(scan))]
    assert result.state.scan_target == "本"


def test_a_cell_whose_popup_is_already_shown_does_not_re_open_it() -> None:
    result = turn(hover=0, word=-1, over_tip=True, scan=cell("本"), nest_tail="本")

    assert not kinds(result.decisions, Arm)
    assert result.state.scan_target == "本"


def test_an_elapsed_scan_dwell_opens_the_popup_for_the_cell_it_was_armed_on() -> None:
    scan = cell("本")
    armed = turn(hover=0, word=-1, over_tip=True, scan=scan).state

    assert elapsed(armed, OpenScan(scan), nest_tail=None).decisions == (OpenNested(scan),)
    # the cursor moved to another cell in the meantime → the stale dwell opens nothing
    assert elapsed(HoverState(scan_target="他"), OpenScan(scan)).decisions == ()


def test_leaving_the_cells_lingers_the_nested_popup_then_hides_it() -> None:
    result = turn(hover=0, word=-1, over_tip=True, nest_open=True)

    assert kinds(result.decisions, Arm) == [Arm(LifecycleTimerKind.NESTED_HIDE, 0.4, HideNested())]
    assert result.state.nest_hide_pending
    assert elapsed(result.state, HideNested()).decisions == (CloseNested(),)


def test_the_cursor_on_the_nested_popup_keeps_it_alive() -> None:
    pending = turn(hover=0, word=-1, over_tip=True, nest_open=True).state

    result = decide(pending, HoverObservation(hover=0, over_nest=True, nest_open=True), DELAYS)

    assert not result.state.nest_hide_pending
    assert Cancel(LifecycleTimerKind.NESTED_HIDE) in result.decisions
    assert result.state.scan_target is None


def test_no_nested_popup_means_nothing_to_linger() -> None:
    result = turn(hover=0, word=-1, over_tip=True, nest_open=False)

    assert not kinds(result.decisions, Arm)


# --- what happens when no timer can be armed -----------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (SwitchTo(1), (ShowWord(1),)),
        (HideTip(), (RetireWord(),)),
        (HideNested(), (CloseNested(),)),
    ],
)
def test_a_switch_or_a_hide_that_cannot_be_armed_happens_at_once(intent, expected) -> None:
    """Fails open. A hide that can never fire leaves a popup on screen for the rest of the session,
    and a switch that never fires strands the tooltip on a word the cursor left."""
    state = HoverState(word_target=1, tip_hide_pending=True, nest_hide_pending=True)

    assert refused(state, intent).decisions == expected


def test_an_open_that_cannot_be_armed_stays_shut() -> None:
    """Fails closed, unlike the others: with no dwell to wait on, dragging across the panel would
    spawn a popup per cell passed over."""
    scan = cell("本")
    state = HoverState(scan_target="本")

    assert refused(state, OpenScan(scan)).decisions == ()
    assert elapsed(state, OpenScan(scan)).decisions == (OpenNested(scan),)  # negative control


@pytest.mark.parametrize("intent", [HideTip(), HideNested()])
def test_a_refused_hide_does_not_leave_a_pending_flag_behind(intent) -> None:
    """The flag means "a deadline is armed". Left set by a refusal, the next tick reads it as armed
    and never arms one — a popup that lingers forever with nothing coming to hide it."""
    state = HoverState(tip_hide_pending=True, nest_hide_pending=True)

    after = refused(state, intent).state

    assert not (after.tip_hide_pending and after.nest_hide_pending)
