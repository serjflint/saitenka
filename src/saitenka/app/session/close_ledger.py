"""Isolate each close participant so one failure cannot strand the rest.

Close is a fixed sequence of ~18 participants — capabilities, job lanes, stores, surfaces, the
transport. It ran unguarded, so the FIRST one to raise aborted every later one: a capability that
threw on shutdown left temp dirs, an open transport and a live overlay behind, and the traceback
named only the thrower.

Participants are a declared `CloseStep` table so their fixed order stays visible while each failure
is recorded independently.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from saitenka.runtime.events import ClosePhase, SessionClosing

from saitenka.runtime.events import SessionClosing
from saitenka.runtime.reactor import LifecycleEffectError

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CloseFailure:
    participant: str
    error: BaseException


@dataclass(frozen=True, slots=True)
class CloseStep:
    """One named close participant."""

    name: str
    run: Callable[[], object]
    #: The step is already complete when its optional collaborator is absent.
    present: Callable[[], object] | None = None


class RuntimeCloseTracker:
    """Track which runtime retirements still require their local fallback."""

    def __init__(self, deliver: Callable[[SessionClosing], bool]) -> None:
        self._deliver = deliver
        self._missing: dict[ClosePhase, frozenset[str]] = {}

    def performed(self, phase: ClosePhase, resource: str) -> bool:
        missing = self._missing.get(phase)
        return missing is not None and resource not in missing

    def fallback(
        self,
        phase: ClosePhase,
        resource: str,
        present: Callable[[], object],
    ) -> Callable[[], object]:
        return lambda: None if self.performed(phase, resource) else present()

    def announce(self, phase: ClosePhase, scratch: str | None = None) -> bool:
        try:
            performed = self._deliver(SessionClosing(phase, scratch))
        except LifecycleEffectError as error:
            self._missing[phase] = error.missing_resources
            raise
        except BaseException:
            self._missing.pop(phase, None)
            raise
        if performed:
            self._missing[phase] = frozenset()
        return performed

    def retire(
        self,
        phase: ClosePhase,
        resource: str,
        local: Callable[[], None],
    ) -> None:
        try:
            if self.announce(phase):
                return
        except LifecycleEffectError:
            if not self.performed(phase, resource):
                local()
            raise
        local()


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

    def run(self, steps: Iterable[CloseStep]) -> None:
        """Run every step in order, isolating each."""
        for step in steps:
            if step.present is not None and step.present() is None:
                # Completed, not skipped: an absent collaborator is a participant with nothing to
                # do, and it read that way before the table existed.
                self.completed.append(step.name)
                continue
            with self.participant(step.name):
                step.run()

    def report(self) -> str | None:
        """One line naming every participant that failed, or None when close was clean."""
        if not self.failures:
            return None
        return "close incomplete: " + ", ".join(
            f"{failure.participant} ({type(failure.error).__name__})" for failure in self.failures
        )
