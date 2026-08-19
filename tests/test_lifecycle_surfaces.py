from __future__ import annotations

import threading
from pathlib import Path

import pytest
import util
from PIL import Image

from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
from saitenka.app.overlay_ids import OverlayId
from saitenka.mpvio.osd import Overlay, PreparedOverlay
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner, SurfaceStatus


class _DeferredIPC(util.FakeIPC):
    """Holds correlated terminals until the test settles them by hand.

    The one thing the shared fake deliberately cannot do — it completes inline — so this overrides
    just that, and inherits every other port. A double defining the whole surface itself is how a
    missing port silently sends production down a fallback branch.
    """

    def __init__(self, *, accepted: bool = True) -> None:
        super().__init__()
        self.pending: list[tuple[object, object]] = []
        self.accepted = accepted
        self.visible: set[int] = set()
        self.executed: set[int] = set()

    def submit_runtime_mpv(self, *, identity, command, on_finished, **_kwargs) -> bool:
        self.commands.append(command)
        if not self.accepted:
            return False
        self.pending.append((identity, on_finished))
        return True

    def command(self, *args):
        if args and args[0] == "overlay-remove":
            self.visible.discard(args[1])
        super().command(*args)
        return {"error": "success"}

    def execute(self, index: int) -> None:
        command = self.commands[index]
        if command[0] == "overlay-add":
            self.visible.add(command[1])
        self.executed.add(index)

    def finish(self, index: int) -> None:
        identity, callback = self.pending[index]
        if index not in self.executed:
            self.execute(index)
        callback(
            EffectFinished(
                EffectId(index),
                Owner.PRESENTATION,
                identity,
                EffectOutcome.SUCCEEDED,
            )
        )


def test_close_places_final_remove_after_pending_add_before_attach_disconnect():
    ipc = _DeferredIPC()
    surfaces = LifecycleSurfaces(Overlay(ipc, runtime_submit=ipc.submit_runtime_mpv))
    surfaces.present(Image.new("RGBA", (2, 2), "white"), 0, 0, oid=OverlayId.LOADING)
    ipc.execute(0)  # mpv applied the add, but its correlated completion is not drained yet
    assert ipc.visible == {OverlayId.LOADING}

    surfaces.close()

    assert ipc.commands[-1] == ("overlay-remove", OverlayId.LOADING)
    assert ipc.visible == set()
    assert surfaces.snapshot(OverlayId.LOADING).status is SurfaceStatus.ABSENT
    ipc.finish(0)
    assert ipc.visible == set()


def test_late_present_ack_cannot_restore_removed_lifecycle_surface():
    ipc = _DeferredIPC()
    overlay = Overlay(ipc, runtime_submit=ipc.submit_runtime_mpv)
    surfaces = LifecycleSurfaces(overlay)

    surfaces.present(Image.new("RGBA", (2, 2), "white"), 4, 5, oid=OverlayId.TOAST)
    surfaces.remove(OverlayId.TOAST)
    ipc.finish(1)
    ipc.finish(0)

    assert ipc.commands[0][0] == "overlay-add"
    assert ipc.commands[1] == ("overlay-remove", OverlayId.TOAST)
    assert surfaces.snapshot(OverlayId.TOAST).status is SurfaceStatus.ABSENT


def test_rejected_surface_submission_is_terminally_failed():
    ipc = _DeferredIPC(accepted=False)
    surfaces = LifecycleSurfaces(Overlay(ipc, runtime_submit=ipc.submit_runtime_mpv))

    surfaces.present(Image.new("RGBA", (2, 2), "white"), 0, 0, oid=OverlayId.LOADING)

    assert surfaces.snapshot(OverlayId.LOADING).status is SurfaceStatus.FAILED


class _BlockingOverlay:
    def __init__(self) -> None:
        self.first_entered = threading.Event()
        self.release_first = threading.Event()
        self.prepare_count = 0
        self.submitted: list[int] = []
        self.lifecycle_oids: set[int] = set()

    def prepare(self, _img, _x, _y, *, oid, revision):
        self.prepare_count += 1
        if self.prepare_count == 1:
            self.first_entered.set()
            self.release_first.wait()
        return PreparedOverlay(oid, Path(f"unused-{revision}"), (), ("overlay-add", oid))

    def submit_surface_transaction(self, *, identity, **_kwargs):
        self.submitted.append(identity.revision)

    def physical_oid(self, oid):
        return oid


@pytest.mark.timeout(5)
def test_concurrent_preparation_cannot_reverse_slot_revision_dispatch():
    overlay = _BlockingOverlay()
    surfaces = LifecycleSurfaces(overlay)
    image = Image.new("RGBA", (1, 1))
    first = threading.Thread(target=lambda: surfaces.present(image, 0, 0, oid=OverlayId.TOAST))
    second = threading.Thread(target=lambda: surfaces.present(image, 0, 0, oid=OverlayId.TOAST))

    first.start()
    assert overlay.first_entered.wait(1)
    second.start()
    overlay.release_first.set()
    first.join()
    second.join()

    assert overlay.submitted == [1, 2]


def test_queued_surface_revisions_keep_distinct_immutable_files():
    ipc = _DeferredIPC()
    surfaces = LifecycleSurfaces(Overlay(ipc, runtime_submit=ipc.submit_runtime_mpv))

    surfaces.present(Image.new("RGBA", (1, 1), "white"), 0, 0, oid=OverlayId.LOADING)
    surfaces.present(Image.new("RGBA", (3, 2), "black"), 0, 0, oid=OverlayId.LOADING)

    first, second = ipc.commands
    first_path, second_path = Path(first[4]), Path(second[4])
    assert first_path != second_path
    assert (first[7:10], len(first_path.read_bytes())) == ((1, 1, 4), 4)
    assert (second[7:10], len(second_path.read_bytes())) == ((3, 2, 12), 24)
    ipc.finish(1)
    ipc.finish(0)


class _PrepareFailureOverlay(_BlockingOverlay):
    def prepare(self, _img, _x, _y, *, oid, revision: int):
        _ = oid, revision
        raise OSError("disk unavailable")


def test_preparation_exception_terminally_fails_revision():
    surfaces = LifecycleSurfaces(_PrepareFailureOverlay())

    surfaces.present(Image.new("RGBA", (1, 1)), 0, 0, oid=OverlayId.TOAST)

    assert surfaces.snapshot(OverlayId.TOAST).status is SurfaceStatus.FAILED


class _SubmissionFailureIPC(_DeferredIPC):
    def submit_runtime_mpv(self, **kwargs):
        self.commands.append(kwargs["command"])
        raise OSError("adapter unavailable")


def test_submission_exception_fails_revision_and_discards_staged_file():
    ipc = _SubmissionFailureIPC()
    surfaces = LifecycleSurfaces(Overlay(ipc, runtime_submit=ipc.submit_runtime_mpv))
    surfaces.present(Image.new("RGBA", (1, 1)), 0, 0, oid=OverlayId.TOAST)

    assert surfaces.snapshot(OverlayId.TOAST).status is SurfaceStatus.FAILED
    assert not Path(ipc.commands[0][4]).exists()


class _RemoveFailureOverlay(_BlockingOverlay):
    def submit_surface_transaction(self, **_kwargs):
        raise OSError("adapter unavailable")


def test_remove_submission_exception_terminally_fails_revision():
    surfaces = LifecycleSurfaces(_RemoveFailureOverlay())

    surfaces.remove(OverlayId.LOADING)

    assert surfaces.snapshot(OverlayId.LOADING).status is SurfaceStatus.FAILED


def test_hiding_every_surface_and_showing_it_again_restores_what_was_there():
    """`Alt+o`. Hiding retains each slot's desired state rather than forgetting it, so showing puts
    the same surfaces back — a hide that dropped them would return the user to a blank overlay and
    make them re-trigger every surface by hand.
    """
    ipc = _DeferredIPC()
    overlay = Overlay(ipc)
    surfaces = LifecycleSurfaces(overlay)
    overlay.show(Image.new("RGBA", (4, 4)), oid=OverlayId.SUB)
    live_before = dict(overlay._live)

    surfaces.set_visible(visible=False)
    assert overlay.visible is False
    assert ("overlay-remove", OverlayId.SUB) in ipc.commands

    ipc.commands.clear()
    surfaces.set_visible(visible=True)

    assert overlay.visible is True
    assert overlay._live == live_before
    assert any(c[0] == "overlay-add" and c[1] == OverlayId.SUB for c in ipc.commands)


def test_a_repeated_hide_is_not_re_issued():
    """The toggle is driven by a reducer that may be asked twice; re-sending a hide would churn the
    overlay slots for no change."""
    ipc = _DeferredIPC()
    overlay = Overlay(ipc)
    surfaces = LifecycleSurfaces(overlay)
    overlay.show(Image.new("RGBA", (4, 4)), oid=OverlayId.SUB)

    surfaces.set_visible(visible=False)
    ipc.commands.clear()
    surfaces.set_visible(visible=False)

    assert ipc.commands == []


def test_a_repaint_re_adds_the_live_surfaces_without_counting_as_a_change():
    """The paused-nudge poke (mpv #8172). It must not bump the op counter the nudge arms off, or
    each repaint would arm the next one and the session would nudge forever."""
    ipc = _DeferredIPC()
    overlay = Overlay(ipc)
    surfaces = LifecycleSurfaces(overlay)
    overlay.show(Image.new("RGBA", (4, 4)), oid=OverlayId.SUB)
    ops_before = overlay.ops
    ipc.commands.clear()

    surfaces.repaint()

    assert any(c[0] == "overlay-add" and c[1] == OverlayId.SUB for c in ipc.commands)
    assert overlay.ops == ops_before


def test_a_repaint_while_hidden_draws_nothing():
    """Re-adding a hidden surface would un-hide it — the toggle undone by a nudge."""
    ipc = _DeferredIPC()
    overlay = Overlay(ipc)
    surfaces = LifecycleSurfaces(overlay)
    overlay.show(Image.new("RGBA", (4, 4)), oid=OverlayId.SUB)
    surfaces.set_visible(visible=False)
    ipc.commands.clear()

    surfaces.repaint()

    assert ipc.commands == []
