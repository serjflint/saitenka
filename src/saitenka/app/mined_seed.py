"""Bounded background loading of expressions already present in the mining deck."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app.miner import Miner
from saitenka.runtime.jobs import JobLanePolicy

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.runtime import EffectFinished, Owner


@dataclass(frozen=True, slots=True)
class MinedSeedRequest:
    anki: object
    mine_cfg: object


def load_mined_seed(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, MinedSeedRequest):
        raise TypeError("invalid mined-seed request")
    if cancelled.is_set():
        return None
    return Miner.mined_expressions(request.anki, request.mine_cfg)


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool: ...


def configure_runtime_job(ipc) -> JobSubmitter | None:
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(
        "mined-seed",
        JobLanePolicy(capacity=2, workers=2),
        load_mined_seed,
    ):
        return None
    return ipc.submit_runtime_job
