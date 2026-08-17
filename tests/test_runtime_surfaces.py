from __future__ import annotations

from saitenka.runtime import EffectError, EffectOutcome
from saitenka.runtime.surfaces import (
    SurfaceAction,
    SurfaceRuntime,
    SurfaceStatus,
    SurfaceTransactionOutcome,
)


def test_late_present_cannot_restore_removed_surface():
    runtime = SurfaceRuntime()
    present = runtime.request("toast", SurfaceAction.PRESENT)
    remove = runtime.request("toast", SurfaceAction.REMOVE)

    assert not runtime.finish(SurfaceTransactionOutcome(present, EffectOutcome.SUCCEEDED))
    assert runtime.finish(SurfaceTransactionOutcome(remove, EffectOutcome.SUCCEEDED))
    assert runtime.snapshot("toast").status is SurfaceStatus.ABSENT


def test_late_remove_cannot_clear_newer_present_surface():
    runtime = SurfaceRuntime()
    remove = runtime.request("loading", SurfaceAction.REMOVE)
    present = runtime.request("loading", SurfaceAction.PRESENT)

    assert not runtime.finish(SurfaceTransactionOutcome(remove, EffectOutcome.SUCCEEDED))
    assert runtime.finish(SurfaceTransactionOutcome(present, EffectOutcome.SUCCEEDED))
    assert runtime.snapshot("loading").status is SurfaceStatus.PRESENT


def test_current_failure_is_acknowledged_without_claiming_pixels():
    runtime = SurfaceRuntime()
    present = runtime.request("toast", SurfaceAction.PRESENT)

    assert runtime.finish(
        SurfaceTransactionOutcome(present, EffectOutcome.FAILED, EffectError.INVALID_RESULT)
    )
    snapshot = runtime.snapshot("toast")
    assert snapshot.acknowledged_revision == present.revision
    assert snapshot.status is SurfaceStatus.FAILED


def test_duplicate_ack_is_rejected_after_one_terminal_transition():
    runtime = SurfaceRuntime()
    present = runtime.request("toast", SurfaceAction.PRESENT)
    outcome = SurfaceTransactionOutcome(present, EffectOutcome.SUCCEEDED)

    assert runtime.finish(outcome)
    assert not runtime.finish(outcome)
