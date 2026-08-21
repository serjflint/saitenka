"""Bounded background loading of expressions already present in the mining deck."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.miner import mined_expressions
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    import threading


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
