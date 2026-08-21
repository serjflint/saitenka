"""The composition seam the noninteractive entry modes drive (WP5.5).

Demo and screenshot used to reach straight into `Reader` and the IPC transport: a cue read was
`reader._get("sub-text")`, a capture was `ipc.command("screenshot-to-file", ...)`. That coupling is
why they could not be switched to the blocking runner with `run`/`attach` — they did not share a
driver with it, they shared a *host object*, and every private they touched was one more thing WP6
would have to keep alive.

So the entry modes name operations here instead. What each one costs today (a Reader call, a
correlated command) is this module's business; when WP6 repoints them at reducers and ports, the
entry modes do not change. The seam is the point.

It held a `Reader` for exactly one reason — it *drives* one — and that read as composition until the
members were counted: three facts and a dozen acts, which is a feature value. `SessionFacts` and
`SessionActs` are that value, split the way every feature here splits.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from saitenka.app.mpv_egress import send_correlated
from saitenka.runtime.effects import Owner
from saitenka.runtime.runner import SessionRunner

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: One hop's worth of waiting. Bounded per attempt so a cue search keeps seeking rather than parking
#: on the first wake; the overall bound is the caller's deadline, not a retry count.
CUE_HOP_SECONDS = 0.12


@dataclass(frozen=True, slots=True)
class SessionFacts:
    """What driving a noninteractive session *observes*.

    `prop` and `get` are both here and are not the same question: `prop` answers with the observed
    property while observing, which is what the render-space wait needs, and `get` is the plain
    transport read.
    """

    refresh_osd: Callable[[], object]
    prop: Callable[[str], object]
    get: Callable[[str], object]
    tokens: Callable[[], Sequence[Any]]
    is_content_token: Callable[[Any], bool]
    #: Height of the space actually being drawn into — the scroll step is a fraction of it.
    osd_height: Callable[[], int]
    #: Every staged surface acknowledged. A capture taken before this photographs the previous frame.
    painted: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class SessionActs:
    """What driving a noninteractive session *performs*.

    Every one of these is blocking or immediate by contract: a demo composes a frame and then looks
    at it, so an act that merely queued would be photographed half-done.
    """

    drive_annotation_once: Callable[[float | None], object]
    prepare_subtitle: Callable[[str], None]
    prepare_hover: Callable[[int], None]
    mark_ready: Callable[[], object]
    scroll_tip: Callable[[int], None]
    setup_secondary: Callable[[], object]
    toggle_translation: Callable[[], None]
    mine_current: Callable[[], None]
    bulk_mine: Callable[[], None]


@dataclass(frozen=True, slots=True)
class SessionEntry:
    """Which of the two run-mode branches is taken, as one value: drive a demo, or enter the loop.

    `runtime` is built eagerly rather than behind a factory because `SessionRuntime.__init__`
    resolves nothing — the interactive branch that never touches it pays a bound attribute, and the
    alternative is a callable whose only job is to hide a constructor.
    """

    runtime: SessionRuntime
    #: Enters the blocking reader loop and returns when the session ends.
    run: Callable[[], None]


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

    def __init__(
        self,
        facts: SessionFacts,
        acts: SessionActs,
        ipc,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._facts = facts
        self._acts = acts
        self._ipc = ipc
        self._clock = clock
        # Resolved per step, not bound here: a mode that never waits must not require the host to
        # carry a driver, and eager binding turns "never looked" into an AttributeError at compose.
        self._runner = SessionRunner(self._drive, clock=clock)

    def _drive(self, timeout: float | None) -> None:
        self._acts.drive_annotation_once(timeout)

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
        return self._facts.painted()

    # --- cue ---------------------------------------------------------------------------------

    def refresh_render_space(self) -> None:
        self._facts.refresh_osd()

    def await_render_space(self, *, timeout: float) -> bool:
        """Pump until mpv publishes its window geometry. `False` if the deadline passed first.

        A demo used to sleep here instead. What the nap was standing in for is exactly this fact,
        and the two are not the same: `Reader.osd` falls back to a 720p default that is never
        obviously wrong and never right either, so a demo that composed its tooltip before the real
        geometry landed produced a screenshot of a panel sized for a window that does not exist —
        with nothing failing anywhere. A fixed nap is also the wrong instrument twice over: too
        short on a cold machine, dead time on every warm one.
        """
        return self.run_until(self._render_space_known, timeout=timeout)

    def _render_space_known(self) -> bool:
        self._facts.refresh_osd()  # fold in whatever this turn observed
        observed = self._facts.prop("osd-dimensions")
        dimensions = observed if isinstance(observed, dict) else {}
        return bool(dimensions.get("w")) and bool(dimensions.get("h"))

    def cue_text(self) -> str:
        text = self._facts.get("sub-text")
        return text if isinstance(text, str) else ""

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
            self._acts.drive_annotation_once(
                CUE_HOP_SECONDS if remaining is None else min(remaining, CUE_HOP_SECONDS)
            )

        SessionRunner(hop, clock=self._clock).run_until(
            lambda: bool(self.cue_text()), deadline=deadline
        )
        return self.cue_text()

    def prepare_cue(self, text: str) -> None:
        self._acts.prepare_subtitle(text)

    def tokens(self) -> Sequence[Any]:
        return self._facts.tokens()

    def is_content_token(self, token: Any) -> bool:
        return self._facts.is_content_token(token)

    def prepare_hover(self, index: int) -> None:
        self._acts.prepare_hover(index)

    def mark_ready(self) -> None:
        self._acts.mark_ready()

    # --- interaction -------------------------------------------------------------------------

    #: A scroll step as a fraction of the panel height — the demo scrolls by a proportion of what is
    #: on screen, so the same spec produces the same visual result at any resolution.
    SCROLL_FRACTION = 0.12

    def scroll_tooltip(self) -> None:
        self._acts.scroll_tip(round(self._facts.osd_height() * self.SCROLL_FRACTION))

    def enable_translation(self) -> None:
        self._acts.setup_secondary()
        self._acts.toggle_translation()

    def mine(self, *, bulk: bool) -> None:
        (self._acts.bulk_mine if bulk else self._acts.mine_current)()

    def capture(self, path: str) -> object:
        """Capture the window to `path`, synchronously.

        Stays synchronous by contract: the reply is the capture's result, and the file has to exist
        by the time this returns for the caller to have anything to look at.
        """
        return self._ipc.command("screenshot-to-file", path, "window")
