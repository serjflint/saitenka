"""The copy-flash pulse and the tooltip's claim on the playback pause, as pure decisions."""

from __future__ import annotations

from saitenka.runtime.hover_pause import PauseClaim, ResumePlayback, claimed, released
from saitenka.runtime.hover_pause import PauseTurn as ClaimTurn
from saitenka.runtime.pulse import PulseState, PulseTurn, Repaint, expired, pulsed

NOTHING = PulseState()
TIP, NESTED = 1, 2


def test_a_pulse_whose_expiry_could_not_be_armed_is_never_drawn() -> None:
    """Fails closed. A border with no deadline to retire it stays on the popup until something
    happens to redraw it, which reads as a rendering bug rather than as missing feedback."""
    assert pulsed(NOTHING, TIP, armed=False) == PulseTurn(NOTHING)


def test_an_armed_pulse_takes_the_slot_and_asks_for_a_repaint() -> None:
    assert pulsed(NOTHING, TIP, armed=True) == PulseTurn(PulseState(TIP), (Repaint(TIP),))


def test_a_second_copy_supersedes_the_first() -> None:
    """One slot, not one per popup: the border follows the latest copy."""
    assert pulsed(PulseState(TIP), NESTED, armed=True).state == PulseState(NESTED)


def test_an_expiry_with_nothing_pulsing_decides_nothing() -> None:
    """Not an error: a superseded deadline can still be the one that fires, and repainting at a
    stale id would redraw a popup that never asked."""
    assert expired(NOTHING) == PulseTurn(NOTHING)


def test_an_expiry_clears_the_slot_and_repaints_what_wore_the_border() -> None:
    assert expired(PulseState(NESTED)) == PulseTurn(NOTHING, (Repaint(NESTED),))


def test_a_show_that_did_not_pause_owes_nothing() -> None:
    """mpv owns "is paused"; this owns "we are why". A hover over an already-paused video must not
    hand playback back when it goes away."""
    assert claimed(PauseClaim(), paused=False) == ClaimTurn(PauseClaim())
    assert released(PauseClaim()) == ClaimTurn(PauseClaim())


def test_a_claim_is_released_exactly_once() -> None:
    held = claimed(PauseClaim(), paused=True).state
    assert held == PauseClaim(held=True)

    first = released(held)
    assert first == ClaimTurn(PauseClaim(), (ResumePlayback(),))
    assert released(first.state) == ClaimTurn(PauseClaim())
