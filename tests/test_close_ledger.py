"""Close must complete even when a participant raises, and must say which one did.

The motivating shape: `Reader.close` ran its ~18 participants unguarded, so the first to raise
aborted every later one — leaving the transport open, the overlay live and the scratch dir on disk,
with a traceback naming only the thrower.
"""

from __future__ import annotations

import pytest
from util import FakeIPC

from saitenka.app.close_ledger import CloseLedger
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NullRenderer


def test_a_failing_participant_does_not_strand_the_ones_after_it() -> None:
    ledger = CloseLedger()
    order: list[str] = []

    with ledger.participant("first"):
        order.append("first")
    with ledger.participant("thrower"):
        order.append("thrower")
        raise RuntimeError("boom")
    with ledger.participant("last"):
        order.append("last")

    assert order == ["first", "thrower", "last"]
    assert ledger.completed == ["first", "last"]
    assert [failure.participant for failure in ledger.failures] == ["thrower"]


def test_a_clean_close_reports_nothing() -> None:
    """The negative control: `report` must be able to say "fine", or it is not an oracle."""
    ledger = CloseLedger()
    with ledger.participant("only"):
        pass
    assert ledger.report() is None
    assert ledger.completed == ["only"]


def test_the_report_names_every_failure_and_its_type() -> None:
    ledger = CloseLedger()
    for name, error in (("a", RuntimeError), ("b", ValueError)):
        with ledger.participant(name):
            raise error("x")
    report = ledger.report()
    assert report is not None
    assert "a (RuntimeError)" in report
    assert "b (ValueError)" in report


def test_an_interrupt_during_teardown_still_runs_the_rest() -> None:
    """A Ctrl-C mid-close is exactly when the remaining participants matter most.

    `BaseException` rather than `Exception` is therefore deliberate — re-raising here would strand
    the transport and the scratch dir, which is the bug this exists to prevent.
    """
    ledger = CloseLedger()
    reached = False

    with ledger.participant("interrupted"):
        raise KeyboardInterrupt
    with ledger.participant("after"):
        reached = True

    assert reached
    assert ledger.completed == ["after"]
    assert [type(failure.error) for failure in ledger.failures] == [KeyboardInterrupt]


def test_close_returns_a_clean_ledger_for_a_healthy_reader() -> None:
    """Against the real `Reader`, so the participant list cannot drift away from the wrapper."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())
    ledger = reader.close()
    assert ledger.report() is None
    assert "transport" in ledger.completed
    assert "temporary-artifacts" in ledger.completed


def test_a_wedged_participant_is_reported_and_close_still_finishes() -> None:
    """The end-to-end claim: one broken collaborator cannot cost us the transport."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())

    class Wedged:
        def close(self) -> None:
            raise RuntimeError("wedged")

        def cancel_all(self) -> None:
            raise RuntimeError("wedged")

    reader._interaction_jobs = Wedged()  # type: ignore[assignment]
    ledger = reader.close()

    report = ledger.report()
    assert report is not None
    assert "interaction-jobs" in report
    assert "transport" in ledger.completed  # the point: teardown continued past the failure
    assert "temporary-artifacts" in ledger.completed


@pytest.mark.parametrize("attribute", ["lifecycle_timers", "lifecycle_surfaces"])
def test_a_late_participant_failing_does_not_lose_the_scratch_directory(attribute: str) -> None:
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())
    scratch = reader._tmp

    class Wedged:
        def close(self) -> None:
            raise RuntimeError("wedged")

    setattr(reader, attribute, Wedged())
    ledger = reader.close()

    assert ledger.report() is not None
    assert "temporary-artifacts" in ledger.completed
    assert not scratch.exists()


def test_the_lane_budget_is_armed_after_the_capabilities_come_down() -> None:
    """The 2s lane budget starts once the capabilities are down, not at table-build time.

    Hoisting it would spend that window on capability teardown, and the only symptom is lanes
    getting less time to drain on a slow machine — invisible until a close silently truncates.
    """
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())

    order = reader.close().completed

    assert order.index("stop-workers") < order.index("lane-budget")
    assert order.index("lane-budget") < order.index("lane:subtitle-fetch")


def test_every_close_participant_runs_and_keeps_its_declared_order() -> None:
    """The table is a sequence, not a set: the checker pins several pairs by order, and the
    hazards it pins are real (a geometry job admitted after its provider closed, a lane drained
    after the store it writes to went away)."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())

    ledger = reader.close()

    assert ledger.report() is None
    order = ledger.completed
    assert order.index("lane:geometry") < order.index("subtitle-close")
    assert order.index("mask-atlas-startup") < order.index("lane:mask-atlas-startup")
    assert order.index("lane:mask-atlas-startup") < order.index("mask-atlas-uninstall")
    assert order[-1] == "temporary-artifacts"
