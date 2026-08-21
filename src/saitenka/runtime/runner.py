"""The shared session-driving core.

One loop, owned here, so a feature that needs to wait for something does not grow its own. A
feature-local spin is a second event loop: it has its own idea of ordering, its own timeout
handling, and its own way to hang — and the two drift, because only one of them is exercised by
the interactive path.

`run_until` is the bounded mode: pump until a predicate holds or a deadline passes. The blocking
mode `run`/`attach` use is the same loop with `predicate=lambda: closed`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class SessionRunner:
    """Drive a session one step per wake until a predicate holds."""

    def __init__(
        self,
        step: Callable[[float | None], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        #: Consumes one turn, blocking up to the timeout it is given. `SessionLoop` passes a step
        #: that receives from the mailbox; a session with no runtime passes its own drain.
        self._step = step
        self._clock = clock

    def run_until(self, predicate: Callable[[], bool], *, deadline: float | None = None) -> bool:
        """Pump until ``predicate`` holds. ``True`` if it did, ``False`` on the deadline.

        The predicate is checked *before* the first step, so a caller whose condition already holds
        does no work — and, more importantly, cannot block on a wake that will never come because
        the thing it was waiting for already happened.
        """
        while not predicate():
            if deadline is None:
                self._step(None)
                continue
            remaining = deadline - self._clock()
            if remaining <= 0:
                return False
            self._step(remaining)
        return True
