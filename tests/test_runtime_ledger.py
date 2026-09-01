"""The runtime's census: what reducers reported, what it controlled, and what was unrouted.

Before this existed the reactor was constructed without a `diagnostics` or a `control` sink, so
`EmitDiagnostic` — which `StartupHintReducer` emits on every hint completion — went nowhere, and a
`CancelEffect`/`ExpireEffect` would have too. All three are one fact about a live session, so they
share one ledger.
"""

from __future__ import annotations

from session_builder import build_session
from util import FakeIPC

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.routes import (
    ControlSink,
    install_session_reactor,
    install_session_runtime,
)
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.diagnostics import RuntimeLedger
from saitenka.runtime.effects import (
    CancelEffect,
    EffectId,
    EmitDiagnostic,
    ExpireEffect,
    Owner,
)
from saitenka.runtime.events import RawMpvEvent


def test_a_live_session_records_what_its_reducers_reported() -> None:
    """The startup hint's `show`/`clear` outcomes are the one diagnostic production emits today.

    Driven through the session install and a real close rather than by calling the reducer, because
    the defect was in the *wiring*: the reducer already emitted these and the reactor dropped them.
    """
    ipc = FakeIPC()
    gateway = install_session_runtime(ipc)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    ledger = gateway.session_ledger
    assert ledger is not None
    try:
        reader.entry.runtime.run_until(lambda: bool(ledger.counts), timeout=1.0)
        reader.close()
    finally:
        gateway.close()

    counts = ledger.counts
    assert any(key.startswith("diagnostic:session:startup.hint,operation=show") for key in counts)


def test_an_event_no_owner_claims_is_counted_rather_than_dropped() -> None:
    """An unrouted event must remain observable instead of disappearing silently."""
    ipc = FakeIPC()
    gateway = install_session_runtime(ipc, startup_hint=False)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    ledger = gateway.session_ledger
    assert ledger is not None

    gateway.publish_session_event(RawMpvEvent("seek"))
    try:
        reader.entry.runtime.run_until(lambda: bool(ledger.counts), timeout=1.0)
    finally:
        reader.close()
        gateway.close()

    assert ledger.counts["unrouted:-:RawMpvEvent"] == 1


def test_an_expiry_reaches_the_gateway_that_holds_the_request() -> None:
    """Only the gateway can turn a passed deadline into a TIMEOUT terminal — it owns the in-flight
    request. A sink that counted the control without forwarding it would leave the effect pending
    forever, which is what dropping it silently already did."""
    expired: list[ExpireEffect] = []

    class _Gateway:
        def expire(self, control: ExpireEffect) -> None:
            expired.append(control)

    ledger = RuntimeLedger()
    sink = ControlSink(_Gateway(), ledger)  # type: ignore[arg-type]  # only `expire` is reached
    control = ExpireEffect(EffectId(7), 12.5)

    sink(control)

    assert expired == [control]
    assert ledger.counts["control:expire"] == 1


def test_a_cancel_for_an_effect_the_reactor_never_dispatched_is_not_retired() -> None:
    """The same guard `_finish` applies. Retiring is a claim of ownership, and the loser of that
    race never finds out — so a cancel must not be able to retire somebody else's effect."""
    from saitenka.app.session.mpv_gateway import install_gateway

    gateway = install_gateway(FakeIPC())
    reactor = install_session_reactor(gateway, startup_hint=False)
    ledger = RuntimeLedger()
    sink = ControlSink(gateway, ledger)
    sink.reactor = reactor

    sink(CancelEffect(EffectId(99), Owner.SESSION, "nobody's"))
    gateway.close()

    assert ledger.counts["control:cancel:session"] == 1
    assert reactor.snapshot.pending_effects == ()


def test_the_sink_survives_a_cancel_before_the_reactor_is_bound() -> None:
    """The binding is late by construction — the reactor takes the sink. A control arriving in that
    window must be counted and dropped, not raise."""
    ledger = RuntimeLedger()
    sink = ControlSink(None, ledger)  # type: ignore[arg-type]  # never reached for a cancel

    sink(CancelEffect(EffectId(1), Owner.PLAYBACK, "early"))

    assert ledger.counts["control:cancel:playback"] == 1


def test_the_census_refuses_to_grow_without_bound() -> None:
    """Bounded like every other runtime queue: an unmigrated event stream must not become a leak.
    A saturated ledger says so rather than lying by omission."""
    ledger = RuntimeLedger(capacity=2)

    for index in range(5):
        ledger.unrouted(f"-:Event{index}")
    ledger.unrouted("-:Event0")

    counts = ledger.counts
    assert len(counts) == 3  # two admitted keys plus the overflow tally
    assert counts["unrouted:-:Event0"] == 2
    assert counts["ledger:overflow"] == 3


def test_a_diagnostic_keeps_its_fields_so_two_outcomes_are_two_rows() -> None:
    """`show` accepted and `show` rejected are different facts. Folding the fields away would make
    the census count how often the hint completed, which nobody needs."""
    ledger = RuntimeLedger()

    ledger.diagnostic(EmitDiagnostic("startup.hint", Owner.SESSION, (("outcome", "accepted"),)))
    ledger.diagnostic(EmitDiagnostic("startup.hint", Owner.SESSION, (("outcome", "rejected"),)))

    assert ledger.counts == {
        "diagnostic:session:startup.hint,outcome=accepted": 1,
        "diagnostic:session:startup.hint,outcome=rejected": 1,
    }
