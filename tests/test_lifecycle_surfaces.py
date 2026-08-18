from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PIL import Image

from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
from saitenka.app.overlay_ids import OverlayId
from saitenka.mpvio.osd import Overlay, PreparedOverlay
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner, SurfaceStatus


class _DeferredIPC:
    def __init__(self, *, accepted: bool = True) -> None:
        self.commands: list[tuple[object, ...]] = []
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

    def command(self, *_args):
        self.commands.append(_args)
        if _args[0] == "overlay-remove":
            self.visible.discard(_args[1])
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
