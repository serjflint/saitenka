"""One correlated write to mpv, shared by the features that make them.

Not awaited: mpv has a single ordered outbound channel and a synchronous read is queued behind the
write, so a readback after it still observes it. What the correlation buys is a *terminal outcome* —
a rejected command is reported instead of vanishing into a discarded reply.

Shared rather than copied per feature because the interesting part is the failure handling, and a
second copy is where "was not admitted" quietly stops being logged in one of them.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.runtime import EffectOutcome, Owner

if TYPE_CHECKING:
    from saitenka.runtime import EffectFinished

log = logging.getLogger(__name__)


def send_correlated(ipc, identity: str, *command: object, owner: Owner = Owner.SESSION) -> None:
    """Submit ``command``, time it to its terminal outcome, and log any outcome that is not success.

    Submitted-to-settled is measured here because a correlated write is fire-and-forget at the call
    site: the caller returns the moment the effect is admitted, so nothing downstream can tell a
    command that reached mpv in 5 ms from one that sat for six seconds. Reading mpv's own log back
    against our spans is what raised the question, and it cannot answer it — mpv timestamps when it
    *ran* a command, never when we asked.

    Labeled by identity, which is a closed set of literals, so the cardinality is bounded by the
    call sites rather than by traffic.
    """
    submitted = time.perf_counter()

    def finished(completion: EffectFinished) -> None:
        if otel_metrics.mpv_effect_apply_ms is not None:
            otel_metrics.mpv_effect_apply_ms.record(
                (time.perf_counter() - submitted) * 1000.0, {"identity": identity}
            )
        if otel_metrics.mpv_effect_outcome is not None:
            otel_metrics.mpv_effect_outcome.add(
                1, {"identity": identity, "outcome": completion.outcome.value}
            )
        if completion.outcome is not EffectOutcome.SUCCEEDED:
            log.warning("mpv command %s did not apply: %s", identity, completion.outcome)

    if not ipc.submit_runtime_mpv(
        owner=owner,
        identity=identity,
        command=command,
        timeout_s=10.0,
        on_finished=finished,
    ):
        if otel_metrics.mpv_effect_outcome is not None:
            otel_metrics.mpv_effect_outcome.add(
                1, {"identity": identity, "outcome": "not-admitted"}
            )
        log.warning("mpv command %s was not admitted", identity)
