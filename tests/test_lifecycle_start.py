"""Setup is a sequence the runtime owns, phase by phase — `test_close_ledger`'s mirror.

The motivating shape is the same one close had: `SessionController.run` named every step itself, so a step's
lifetime was split between the subsystem that owns it and a line in a setup sequence far away.
"""

from __future__ import annotations

import pytest
from util import FakeIPC, runtime_gateway

from saitenka.app.session_controller import SessionController
from saitenka.app.session_routes import install_session_reactor
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.effects import STARTUP_EFFECTS
from saitenka.runtime.events import ConnectionReplaced, SessionStarting, StartPhase
from saitenka.runtime.lifecycle_start import LifecycleStartState, reduce_lifecycle_start


def test_every_phase_brings_up_exactly_one_thing_and_no_two_share_it() -> None:
    """The whole setup table as one assertion: a duty lands in exactly one phase.

    Two phases running the same step would run it twice — the second against collaborators the
    first already built — and no single-phase test can see that.
    """
    state = LifecycleStartState()
    seen: list[type] = []
    for phase in StartPhase:
        result = reduce_lifecycle_start(state, SessionStarting(phase))
        state = result.state
        seen.extend(type(effect) for effect in result.effects)

    assert seen == list(STARTUP_EFFECTS)  # every effect, in phase order, once


def test_a_re_announced_phase_does_not_rebuild_what_it_already_built() -> None:
    """Latched for close's reason inverted: a reconnect must not re-register a section or reopen a
    history row somebody has since replaced."""
    first = reduce_lifecycle_start(LifecycleStartState(), SessionStarting())
    second = reduce_lifecycle_start(first.state, SessionStarting())

    assert first.effects  # negative control: the phase does bring something up the first time
    assert second.effects == ()


def test_the_start_feature_ignores_the_events_it_shares_a_slot_with() -> None:
    """It sits in `Owner.SESSION`'s slice, which broadcasts, so every session event reaches it."""
    result = reduce_lifecycle_start(LifecycleStartState(), ConnectionReplaced(1))

    assert result.effects == ()


@pytest.mark.parametrize("with_runtime", [True, False])
def test_setup_brings_the_same_session_up_with_or_without_a_runtime(
    request, *, with_runtime: bool
) -> None:
    """The differential, and the reason the fallback exists at all.

    A screenshot capture and most unit tests are a `SessionController` with no runtime. The two paths run the
    same steps in the same order or the migration changed behaviour for exactly those sessions.
    """
    ipc = FakeIPC()
    if with_runtime:
        gateway = runtime_gateway(ipc)
        request.addfinalizer(gateway.close)
        install_session_reactor(gateway)
    reader = SessionController(ipc, prefetch=False, renderer=NullRenderer())
    request.addfinalizer(reader.close)

    for phase in StartPhase:
        reader._announce_start(phase)

    assert reader._observing  # reads are event-driven from here on
    assert any(c and c[0] == "define-section" for c in ipc.commands)  # input routes to us
    assert "lifecycle:startup-health" in ipc.timers  # diagnostics armed


def test_a_step_that_fails_stops_the_phase_rather_than_reporting_it_up() -> None:
    """Setup is not teardown: a half-built session must not carry on as if the phase happened.

    `_retire` isolates each close participant because teardown has to continue at all costs. The
    setup half deliberately does not — the phases behind a step depend on it.
    """
    from saitenka.app.session_resources import Starting
    from saitenka.app.session_routes import OBSERVERS_PARTICIPANT, _dispatcher
    from saitenka.runtime.diagnostics import RuntimeLedger
    from saitenka.runtime.effects import StartPropertyObservation

    def boom() -> None:
        raise RuntimeError("the observer set was refused")

    gateway = runtime_gateway(FakeIPC())
    try:
        gateway.session_resources[OBSERVERS_PARTICIPANT] = Starting(boom)
        dispatch = _dispatcher(gateway, RuntimeLedger())

        with pytest.raises(RuntimeError):
            dispatch(StartPropertyObservation())
    finally:
        gateway.close()


def test_an_unregistered_step_is_reported_rather_than_silently_skipped() -> None:
    """False, not an exception: a session whose owner never registered a step still runs its own."""
    from saitenka.app.session_routes import _dispatcher
    from saitenka.runtime.diagnostics import RuntimeLedger
    from saitenka.runtime.effects import OpenSessionHistory

    gateway = runtime_gateway(FakeIPC())
    try:
        dispatch = _dispatcher(gateway, RuntimeLedger())

        assert dispatch(OpenSessionHistory()) is False
    finally:
        gateway.close()


def test_the_render_guard_is_disarmed_by_the_session_that_armed_it() -> None:
    """`_GUARD_MAIN_RENDER` is process-global, so its owner is the session, not the process.

    The failure it caused is worth naming: a session that ended without disarming left every later
    panel raster in the same interpreter tripping the guard on a render loop that was no longer
    anybody's — an intermittent that named a different innocent test each run.
    """
    from saitenka.render import banded

    reader = SessionController(FakeIPC(), prefetch=False, renderer=NullRenderer())
    try:
        reader._announce_start(StartPhase.PROCESS)
        assert banded._GUARD_MAIN_RENDER  # negative control: the oracle can fail
    finally:
        reader.close()

    assert not banded._GUARD_MAIN_RENDER
