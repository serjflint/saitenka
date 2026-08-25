"""Isolate each participant in `SessionController.close` so one failure cannot strand the rest.

Close is a fixed sequence of ~18 participants — capabilities, job lanes, stores, surfaces, the
transport. It ran unguarded, so the FIRST one to raise aborted every later one: a capability that
threw on shutdown left temp dirs, an open transport and a live overlay behind, and the traceback
named only the thrower.

Participants are a declared `CloseStep` table so the runtime can take them over one at a time. That
was previously ruled out on the grounds that routing them through a loop would erase the duty
evidence `tools/runtime_migration_check.py` matches by name AND pairwise order — which turned out
not to hold: its `Scanner` opens a scope for `FunctionDef` and not for `Lambda`, so a call inside a
step's lambda is still attributed to the enclosing method, `order:` constraints included. A nested
`def` *would* lose it, which is why step bodies stay lambdas.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CloseFailure:
    participant: str
    error: BaseException


@dataclass(frozen=True, slots=True)
class CloseStep:
    """One named participant. `run` is a lambda by contract — see the module docstring."""

    name: str
    run: Callable[[], object]
    #: Runtime assertion that `run`'s target exists; the step is skipped when it returns None.
    #: A lambda cannot hold an `assert`, and the step body must name the attribute literally (the
    #: migration checker matches `call:self._x.close`, and any wrapper collapses it to `call:close`)
    #: — so the check lives here and `run` carries a `union-attr` marker pointing at it.
    present: Callable[[], object] | None = None


def _absent() -> None:
    """A participant the runtime already retired — `present` skips on None."""
    return


def fallback_for(present: Callable[[], object], *, owned: bool) -> Callable[[], object]:
    """`CloseStep.present` for a step the runtime performs when a runtime owns the session.

    `present` skips on `None`, not on falsy, so "the runtime owns this" has to *be* None: a
    `not owned` bool keeps the fallback running beside the effect it is a fallback for.
    """
    return _absent if owned else present


def fallback_after(performed: Callable[[], bool], present: Callable[[], object]):
    """`fallback_for` for a decision only the announcement can make.

    Registering a participant is not the same as something performing it: a session with a gateway
    but no reactor registers everything and runs nothing, so a fallback gated on registration alone
    is skipped by a runtime that never acted. The phase's announcement is what actually answers,
    which is why it has to run *before* the steps it might replace.
    """
    return lambda: _absent() if performed() else present()


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
        """Run every step in order, isolating each. The sequence the runtime will eventually own."""
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
