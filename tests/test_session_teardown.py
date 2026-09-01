"""Sessions handed out by the suite get closed, so a worker stops accumulating whole graphs.

A session owns worker threads, and a live thread is a GC root — an unclosed one keeps its panel
caches, stores, timers and transport reachable for the rest of the process. Measured at ~14.6 MB
retained per leaked session against ~0.4 MB closed, over a suite that builds ~330 of them.
"""

from __future__ import annotations

import session_builder
from util import FakeIPC

from saitenka.app.session.lifecycle import LiveState


def test_the_sweep_closes_what_it_drained_and_empties_the_registry(make_session):
    registry: list[session_builder.TestSession] = []
    first, second = make_session(FakeIPC()), make_session(FakeIPC())
    registry.extend((first, second))

    assert session_builder.drain_and_close(registry) == 2

    assert registry == []  # emptied before closing, so a close cannot re-enter the sweep
    assert first.graph.lifecycle.state is LiveState.CLOSED
    assert second.graph.lifecycle.state is LiveState.CLOSED


def test_the_sweep_survives_a_session_that_refuses_to_close():
    """Teardown must not rewrite a test's failure as an error — the later sessions still close."""

    class Refuses:
        def close(self):
            raise RuntimeError("close failed")

    good = session_builder.build_session(FakeIPC())
    registry = [good, Refuses()]

    assert session_builder.drain_and_close(registry) == 2
    assert good.graph.lifecycle.state is LiveState.CLOSED


def test_make_session_hands_out_a_live_session(make_session):
    """The factory is a seam, not a wrapper: what it returns is the same live session."""
    session = make_session(FakeIPC())
    session.start()

    assert session.graph.lifecycle.state is LiveState.RUNNING
    assert session.pump() is True
