"""A frame handed to ``overlay-add`` must be immutable for as long as mpv can still read it.

mpv reads the named file *inside* `cmd_overlay_add`, so anything that mutates a published path races
that read, and a pagein against a file mid-rewrite is a SIGBUS inside mpv — not an error we can see.
Two shipped crashes faulted in exactly that frame. The oracle here is the property, not the timing:
hold an open descriptor the way mpv does, publish more frames, and require the held bytes to survive.

`Path.open()` here is the subject, not a way to read a file: the assertion is about what a descriptor
opened *before* the later writes still yields, which `read_bytes` (FURB101) cannot express.
"""

import pathlib
import threading

import numpy as np
import pytest
import util

from saitenka.app.overlay_ids import OverlayId
from saitenka.mpvio.osd import Overlay


def _frame(value: int, size: int = 16) -> np.ndarray:
    return np.full((size, size, 4), value, dtype=np.uint8)


def _published_path(overlay: Overlay, oid: int) -> pathlib.Path:
    return pathlib.Path(overlay._live[overlay.physical_oid(oid)][2])


def test_a_published_frame_survives_the_frames_that_follow_it():
    overlay = Overlay(util.FakeIPC())
    overlay.show_bgra(_frame(1), oid=OverlayId.TIP)
    expected = _frame(1).tobytes()

    with _published_path(overlay, OverlayId.TIP).open("rb") as held:
        overlay.show_bgra(_frame(2), oid=OverlayId.TIP)
        overlay.show_bgra(_frame(3), oid=OverlayId.TIP)
        assert held.read() == expected

    overlay.close()


def test_the_oracle_catches_a_frame_rewritten_in_place():
    """The negative control: the shipped-crash write form, against the same assertion."""
    overlay = Overlay(util.FakeIPC())
    overlay.show_bgra(_frame(1), oid=OverlayId.TIP)
    expected = _frame(1).tobytes()
    published = _published_path(overlay, OverlayId.TIP)

    with published.open("rb") as held:
        published.write_bytes(_frame(2).tobytes())  # what `_write_frame` used to do
        assert held.read() != expected

    overlay.close()


@pytest.mark.timeout(5)
def test_repaint_and_publication_run_concurrently_without_stalling():
    """The two-writer shape — `repaint` re-issuing `_live`'s tail from the reader thread while the
    upload thread publishes. This guards the *lock* (a publish path both threads enter must not
    deadlock) and nothing more: it passes against the crashing implementation too, because the race
    it models is a sub-millisecond window inside a write. Immutability above is what kills the crash.
    """
    frame = _frame(4, size=64)
    expected = frame.nbytes
    short: list[int] = []

    class WatchingIPC(util.FakeIPC):
        def command(self, *args):
            if args[0] == "overlay-add":
                size = len(pathlib.Path(args[4]).read_bytes())  # tail = (x, y, path, …)
                if size != expected:
                    short.append(size)
            return super().command(*args)

    overlay = Overlay(WatchingIPC())
    overlay.show_bgra(frame, oid=OverlayId.TIP)
    stop = threading.Event()

    def repaint_loop() -> None:
        while not stop.is_set():
            overlay.repaint()

    reader = threading.Thread(target=repaint_loop, name="repaint")
    reader.start()
    try:
        for _ in range(200):
            overlay.show_bgra(frame, oid=OverlayId.TIP)
    finally:
        stop.set()
        reader.join()

    assert short == []
    overlay.close()
