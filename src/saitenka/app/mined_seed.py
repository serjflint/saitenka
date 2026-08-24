"""Bounded background loading of expressions already present in the mining deck."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.miner import mined_expressions
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    import threading


@dataclass(slots=True)
class MinedSeedLane:
    """The seed attempt's own state: is one out, did one land, how many have failed, is a retry armed.

    Five loose `SessionController` fields before this, written from four sites — the shape the one-writer
    invariant names: nobody competed for them, but `apply_deps` reset four of them by hand and the
    close bumped a fifth, so "start over" and "invalidate what is in flight" had no name. The
    generation is the fence a completion is checked against, so bumping it IS the invalidation.
    """

    generation: int = 0
    inflight: bool = False
    done: bool = False
    failures: int = 0
    retry_pending: bool = False

    @property
    def idle(self) -> bool:
        """Nothing out, nothing landed, nothing armed — the only state a new attempt may start from."""
        return not (self.inflight or self.done or self.retry_pending)

    def invalidate(self) -> None:
        """Everything in flight is stale from here. The close does this before anything tears down."""
        self.generation += 1

    def restart(self) -> None:
        """New collaborators arrived: forget the old deck entirely and let a fresh attempt run.

        Deliberately not `__init__`'s defaults reused — the generation must *advance*, never reset,
        or a completion from the previous deck would pass the fence.
        """
        self.invalidate()
        self.inflight = self.done = self.retry_pending = False
        self.failures = 0

    def backoff_delay(self) -> float:
        """Exponential, capped. Called after `failures` has counted this one."""
        return min(8.0, 0.25 * (2 ** (self.failures - 1)))


@dataclass(frozen=True, slots=True)
class MinedSeedRequest:
    anki: object
    mine_cfg: object


def load_mined_seed(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, MinedSeedRequest):
        raise TypeError("invalid mined-seed request")
    if cancelled.is_set():
        return None
    return mined_expressions(request.anki, request.mine_cfg)


def configure_runtime_job(ipc) -> JobSubmitter | None:
    return configure_lane(
        ipc,
        "mined-seed",
        JobLanePolicy(capacity=2, workers=2),
        load_mined_seed,
    )
