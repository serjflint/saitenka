"""Copy-pulse and hover-pause decisions as complete state traces."""

from __future__ import annotations

from saitenka.runtime.hover_pause import PauseClaim, PauseTurn, ResumePlayback, claimed, released
from saitenka.runtime.pulse import PulseState, PulseTurn, Repaint, expired, pulsed


def test_pulse_state_trace() -> None:
    p0 = expired(PulseState())
    p1 = pulsed(p0.state, 1, armed=False)
    p2 = pulsed(p1.state, 1, armed=True)
    p3 = pulsed(p2.state, 2, armed=True)
    p4 = expired(p3.state)
    p5 = expired(p4.state)

    assert [p0, p1, p2, p3, p4, p5] == [
        PulseTurn(PulseState()),
        PulseTurn(PulseState()),
        PulseTurn(PulseState(1), (Repaint(1),)),
        PulseTurn(PulseState(2), (Repaint(2),)),
        PulseTurn(PulseState(), (Repaint(2),)),
        PulseTurn(PulseState()),
    ]


def test_pause_claim_state_trace() -> None:
    c0 = released(PauseClaim())
    c1 = claimed(c0.state, paused=False)
    c2 = claimed(c1.state, paused=True)
    c3 = released(c2.state)
    c4 = released(c3.state)

    assert [c0, c1, c2, c3, c4] == [
        PauseTurn(PauseClaim()),
        PauseTurn(PauseClaim()),
        PauseTurn(PauseClaim(held=True)),
        PauseTurn(PauseClaim(), (ResumePlayback(),)),
        PauseTurn(PauseClaim()),
    ]
