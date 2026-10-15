"""Direct retirement behavior for one composed session."""

from __future__ import annotations

import pytest
from session_builder import build_session
from util import FakeIPC, bare_gateway

from saitenka.app.config import ReaderOptions
from saitenka.app.session.close_ledger import CloseLedger
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime import EventOrigin, MailboxFull, RawMpvEvent, TrafficClass


def _session(ipc: FakeIPC | None = None):
    session = build_session(
        ipc or FakeIPC(),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
        options=ReaderOptions().with_overrides(prefetch=False),
    )
    session.start()
    return session


def test_the_report_names_every_failure_and_its_type() -> None:
    ledger = CloseLedger()
    for name, error in (("a", RuntimeError), ("b", ValueError)):
        with ledger.participant(name):
            raise error("x")

    assert ledger.report() == "close incomplete: a (RuntimeError), b (ValueError)"


def test_close_returns_a_clean_complete_ledger() -> None:
    ledger = _session().close()

    assert ledger.report() is None
    assert "transport" in ledger.completed
    assert "temporary-artifacts" in ledger.completed
    assert ledger.completed[-1] == "session-runtime"


def test_close_is_idempotent() -> None:
    session = _session()
    first = session.close()

    assert session.close() is first


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize(
    ("owner", "method", "participant", "error_type"),
    [
        ("tooltip", "cancel_jobs", "interaction-jobs", RuntimeError),
        ("tooltip", "cancel_jobs", "interaction-jobs", KeyboardInterrupt),
        ("lifecycle_surfaces", "close", "lifecycle-surfaces", RuntimeError),
    ],
)
def test_a_failing_close_participant_does_not_strand_later_retirement(
    monkeypatch, owner, method, participant, error_type
) -> None:
    session = _session()

    def fail() -> None:
        raise error_type("wedged")

    monkeypatch.setattr(getattr(session.graph, owner), method, fail)

    ledger = session.close()

    assert [failure.participant for failure in ledger.failures] == [participant]
    assert [type(failure.error) for failure in ledger.failures] == [error_type]
    assert participant not in ledger.completed
    assert {"transport", "temporary-artifacts", "session-runtime"} <= set(ledger.completed)


def test_capabilities_stop_before_the_shared_lane_budget_starts() -> None:
    ipc = FakeIPC()
    timeouts: list[tuple[str, float]] = []
    ipc.close_runtime_job_lane = lambda name, timeout: bool(timeouts.append((name, timeout)))
    session = _session(ipc)

    order = session.close().completed

    assert order.index("capability:anki") < order.index("lanes:stop-workers")
    assert order.index("lanes:stop-workers") < order.index("lanes:subtitle-fetch")
    subtitle_fetch = next(timeout for name, timeout in timeouts if name == "subtitle-fetch")
    assert 0.0 < subtitle_fetch <= 2.0


def test_rendering_and_surface_teardown_keep_their_dependency_order() -> None:
    order = _session().close().completed

    assert order.index("lanes:geometry") < order.index("subtitle-close")
    assert order.index("subtitle-close") < order.index("lifecycle-surfaces")
    assert order.index("lifecycle-surfaces") < order.index("transport")


def test_close_closes_the_runtime_mailbox() -> None:
    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    session = _session(ipc)

    session.close()

    with pytest.raises(MailboxFull):
        gateway.mailbox.publish(
            RawMpvEvent("after-close"),
            origin=EventOrigin.MPV,
            traffic=TrafficClass.NORMAL,
        )


def test_every_runtime_resource_is_reachable_from_an_effect() -> None:
    from saitenka.app.session.routes import (
        _PARTICIPANT_OF,
        _PERFORMER_OF,
        _RESOURCE_OF,
        install_session_reactor,
    )

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    session = _session(ipc)
    registered = set(gateway.session_resources)
    named = (
        {name for names in _RESOURCE_OF.values() for name in names}
        | set(_PARTICIPANT_OF.values())
        | set(_PERFORMER_OF.values())
    )
    session.close()

    assert registered == named
