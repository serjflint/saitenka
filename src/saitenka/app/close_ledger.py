"""Isolate each participant in `Reader.close` so one failure cannot strand the rest.

Close is a fixed sequence of ~18 participants — capabilities, job lanes, stores, surfaces, the
transport. It ran unguarded, so the FIRST one to raise aborted every later one: a capability that
threw on shutdown left temp dirs, an open transport and a live overlay behind, and the traceback
named only the thrower.

The ledger is deliberately *not* a registry of callables. Each `close_lane(...)` and `.close()` call
site is duty evidence that `tools/runtime_migration_check.py` matches by name AND by pairwise order;
routing them through one loop erases that evidence and reads as a lost close. So participants stay
written out in order, and the ledger only wraps them.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CloseFailure:
    participant: str
    error: BaseException


@dataclass(slots=True)
class CloseLedger:
    """Records what happened to each participant. Truthy failures mean close was not clean."""

    failures: list[CloseFailure] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)

    @contextmanager
    def participant(self, name: str):
        """Run one participant, recording its outcome and never letting it stop the sequence.

        `BaseException` on purpose: a `KeyboardInterrupt` during teardown is exactly when the
        remaining participants matter most, and re-raising it here would strand them. It is
        recorded, not swallowed silently.
        """
        try:
            yield
        except BaseException as error:  # teardown must continue; see docstring
            self.failures.append(CloseFailure(name, error))
            log.warning("close participant %s failed", name, exc_info=error)
        else:
            self.completed.append(name)

    def report(self) -> str | None:
        """One line naming every participant that failed, or None when close was clean."""
        if not self.failures:
            return None
        return "close incomplete: " + ", ".join(
            f"{failure.participant} ({type(failure.error).__name__})" for failure in self.failures
        )
