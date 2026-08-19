"""The composition seam the noninteractive entry modes drive (WP5.5).

Demo and screenshot used to reach straight into `Reader` and the IPC transport: a cue read was
`reader._get("sub-text")`, a capture was `ipc.command("screenshot-to-file", ...)`. That coupling is
why they could not be switched to the blocking runner with `run`/`attach` — they did not share a
driver with it, they shared a *host object*, and every private they touched was one more thing WP6
would have to keep alive.

So the entry modes name operations here instead. What each one costs today (a Reader call, a
correlated command) is this module's business; when WP6 repoints them at reducers and ports, the
entry modes do not change. The seam is the point — `SessionRuntime` holding a `Reader` is the debt
that remains, and it is one row instead of one per helper.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from saitenka.app.mpv_egress import send_correlated
from saitenka.runtime.effects import Owner
from saitenka.runtime.runner import SessionRunner

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: One hop's worth of waiting. Bounded per attempt so a cue search keeps seeking rather than parking
#: on the first wake; the overall bound is the caller's deadline, not a retry count.
CUE_HOP_SECONDS = 0.12


def choose_demo_token(tokens: Sequence[Any], target: str, is_content: Callable[[Any], bool]) -> int:
    """Index of the token a demo should hover: the requested surface, else the first content word.

    Pure, so the fallback is testable without a session. `is_content` is passed rather than read off
    a tokenizer because not every tokenizer has it, and resolving it eagerly turned a miss into an
    `AttributeError` on a path that previously never looked.
    """
    for index, token in enumerate(tokens):
        if target in token.surface:
            return index
    return next((index for index, token in enumerate(tokens) if is_content(token)), 0)


class SessionRuntime:
    """Drive one session through named operations instead of `Reader` internals."""

    def __init__(self, reader, ipc, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._reader = reader
        self._ipc = ipc
        self._clock = clock
        # Resolved per step, not bound here: a mode that never waits must not require the host to
        # carry a driver, and eager binding turns "never looked" into an AttributeError at compose.
        self._runner = SessionRunner(self._drive, clock=clock)

    def _drive(self, timeout: float | None) -> None:
        self._reader._drive_annotation_once(timeout)

    # --- driving -----------------------------------------------------------------------------

    def run_until(self, predicate: Callable[[], bool], *, timeout: float) -> bool:
        """Pump the session until `predicate` holds. `False` if the timeout expired first."""
        return self._runner.run_until(predicate, deadline=self._clock() + timeout)

    def await_paint(self, *, timeout: float) -> bool:
        """Wait for every staged surface to be acknowledged.

        A capture taken while a slot is still PENDING photographs whatever was on screen before it,
        and a sleep long enough to be safe on a slow machine is dead time on every other one.
        """
        return self.run_until(self._painted, timeout=timeout)

    def _painted(self) -> bool:
        reader = self._reader
        return reader.lifecycle_surfaces.settled() and reader.interaction_surfaces.settled()

    # --- cue ---------------------------------------------------------------------------------

    def refresh_render_space(self) -> None:
        self._reader.refresh_osd()

    def cue_text(self) -> str:
        return self._reader._get("sub-text") or ""

    def seek_next_cue(self) -> None:
        send_correlated(self._ipc, "demo-cue-hop", "sub-seek", 1, owner=Owner.SUBTITLE)

    def await_cue(self, *, timeout: float) -> str:
        """Hop forward until a cue lands or the deadline passes; the text either way.

        Each hop drives the session, so a cue that arrives is seen when it arrives rather than at
        the end of a fixed nap — and the bound is a deadline the caller can reason about rather than
        a retry count that means nothing on a slow machine.
        """
        deadline = self._clock() + timeout

        def hop(remaining: float | None) -> None:
            self.seek_next_cue()
            self._reader._drive_annotation_once(
                CUE_HOP_SECONDS if remaining is None else min(remaining, CUE_HOP_SECONDS)
            )

        SessionRunner(hop, clock=self._clock).run_until(
            lambda: bool(self.cue_text()), deadline=deadline
        )
        return self.cue_text()

    def prepare_cue(self, text: str) -> None:
        self._reader.prepare_subtitle_blocking(text)

    def tokens(self) -> Sequence[Any]:
        return self._reader.tokens

    def is_content_token(self, token: Any) -> bool:
        return self._reader.tokenizer.is_content(token)

    def prepare_hover(self, index: int) -> None:
        self._reader.prepare_hover_blocking(index)

    def mark_ready(self) -> None:
        self._reader._mark_interactive_ready()

    # --- interaction -------------------------------------------------------------------------

    #: A scroll step as a fraction of the panel height — the demo scrolls by a proportion of what is
    #: on screen, so the same spec produces the same visual result at any resolution.
    SCROLL_FRACTION = 0.12

    def scroll_tooltip(self) -> None:
        self._reader._scroll_tip(round(self._reader.osd[1] * self.SCROLL_FRACTION))

    def enable_translation(self) -> None:
        self._reader._setup_secondary()
        self._reader.toggle_translation()

    def mine(self, *, bulk: bool) -> None:
        (self._reader.bulk_mine if bulk else self._reader.mine_current)()

    def capture(self, path: str) -> object:
        """Capture the window to `path`, synchronously.

        Stays synchronous by contract: the reply is the capture's result, and the file has to exist
        by the time this returns for the caller to have anything to look at.
        """
        return self._ipc.command("screenshot-to-file", path, "window")
