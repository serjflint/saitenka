"""One correlated write to mpv, shared by the features that make them.

Not awaited: mpv has a single ordered outbound channel and a synchronous read is queued behind the
write, so a readback after it still observes it. What the correlation buys is a *terminal outcome* —
a rejected command is reported instead of vanishing into a discarded reply.

Shared rather than copied per feature because the interesting part is the failure handling, and a
second copy is where "was not admitted" quietly stops being logged in one of them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from saitenka.runtime import EffectOutcome, Owner

if TYPE_CHECKING:
    from saitenka.runtime import EffectFinished

log = logging.getLogger(__name__)


def send_correlated(ipc, identity: str, *command: object, owner: Owner = Owner.SESSION) -> None:
    """Submit ``command`` and log any outcome that is not success."""

    def finished(completion: EffectFinished) -> None:
        if completion.outcome is not EffectOutcome.SUCCEEDED:
            log.warning("mpv command %s did not apply: %s", identity, completion.outcome)

    if not ipc.submit_runtime_mpv(
        owner=owner,
        identity=identity,
        command=command,
        timeout_s=10.0,
        on_finished=finished,
    ):
        log.warning("mpv command %s was not admitted", identity)
