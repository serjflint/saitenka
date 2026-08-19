"""WP5.2: the interactive slots are revision-fenced, so a stale paint settles nobody."""

from __future__ import annotations

import numpy as np

from saitenka.app.interaction_surfaces import InteractionSurfaces
from saitenka.runtime.surfaces import SurfaceStatus

OID = 7


class FakeOverlay:
    """Holds each paint's acknowledgement so a test decides when — and in what order — it lands.

    The real presenter is newest-wins per overlay id, which orders the *paints*. It says nothing
    about when their replies come back, and that is the whole subject here.
    """

    def __init__(self) -> None:
        self.pending: list = []
        self.hidden: list[int] = []

    def show_bgra_interactive(self, bgra, x, y, *, oid, on_presented=None):  # noqa: ARG002
        self.pending.append(on_presented)
        return {"error": "deferred"}

    def hide_interactive(self, oid: int):
        self.hidden.append(oid)
        return {"error": "success"}


def view() -> np.ndarray:
    return np.zeros((4, 4, 4), dtype=np.uint8)


def test_a_paint_acknowledged_after_a_newer_one_settles_nobody() -> None:
    overlay = FakeOverlay()
    surfaces = InteractionSurfaces(overlay)
    settled: list[str] = []

    def record(name: str):
        def settle(*, painted: bool) -> None:  # noqa: ARG001
            settled.append(name)

        return settle

    surfaces.present_bgra(view(), 0, 0, oid=OID, on_settled=record("first"))
    surfaces.present_bgra(view(), 0, 0, oid=OID, on_settled=record("second"))
    first, second = overlay.pending
    second({"error": "success"})
    first({"error": "success"})  # the older paint's reply, arriving late

    assert settled == ["second"]
    assert surfaces.snapshot(OID).status is SurfaceStatus.PRESENT


def test_a_paint_acknowledged_after_a_hide_does_not_report_pixels() -> None:
    """The failure this exists for: a hide wins the slot, then the paint it replaced answers. Left
    unfenced, the tooltip reports itself present after the user watched it disappear."""
    overlay = FakeOverlay()
    surfaces = InteractionSurfaces(overlay)
    settled: list[bool] = []

    surfaces.present_bgra(
        view(), 0, 0, oid=OID, on_settled=lambda *, painted: settled.append(painted)
    )
    surfaces.remove(OID)
    overlay.pending[0]({"error": "success"})

    assert settled == []
    assert surfaces.snapshot(OID).status is SurfaceStatus.ABSENT


def test_a_refused_paint_settles_its_caller_as_unpainted() -> None:
    overlay = FakeOverlay()
    surfaces = InteractionSurfaces(overlay)
    settled: list[bool] = []

    surfaces.present_bgra(
        view(), 0, 0, oid=OID, on_settled=lambda *, painted: settled.append(painted)
    )
    overlay.pending[0]({"error": "unsupported format"})

    assert settled == [False]
    assert surfaces.snapshot(OID).status is SurfaceStatus.FAILED


def test_a_deferred_reply_is_not_a_failure() -> None:
    """`show_bgra_interactive` answers `deferred` when it hands the paint to the presenter thread —
    that is the normal path on a real session, not a refusal."""
    overlay = FakeOverlay()
    surfaces = InteractionSurfaces(overlay)
    settled: list[bool] = []

    surfaces.present_bgra(
        view(), 0, 0, oid=OID, on_settled=lambda *, painted: settled.append(painted)
    )
    overlay.pending[0]({"error": "deferred"})

    assert settled == [True]
