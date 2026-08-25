"""A frame handed to ``overlay-add`` must stay readable, and unchanged, while mpv may read it.

mpv reads the named file *inside* `cmd_overlay_add`, and a pagein against a file mid-rewrite is a
SIGBUS inside mpv — not an error we can see. Two shipped crashes faulted in that frame.

Publishing to a fresh path each time buys immutability outright, so what these oracles test is *when*
a path stops existing: retirement is the only way the design can now break.

`Path.open()` below is the subject, not a way to read a file — the assertion is about what a
descriptor opened *before* the later writes still yields, which `read_bytes` (FURB101) cannot express.
"""

import pathlib
import threading
import time

import numpy as np
import pytest
import util

from saitenka.app.overlay_ids import OverlayId
from saitenka.mpvio.osd import RETAINED_FRAMES, Overlay


def _frame(value: int, size: int = 16) -> np.ndarray:
    return np.full((size, size, 4), value, dtype=np.uint8)


def _published_path(overlay: Overlay, oid: int) -> pathlib.Path:
    return pathlib.Path(overlay._live[overlay.physical_oid(oid)][2])


def test_a_published_frame_survives_the_frames_that_follow_it():
    """The immutability property, at the range where mpv can still be reading: within the retention
    window a published path keeps its own bytes, whatever is published after it."""
    overlay = Overlay(util.FakeIPC())
    overlay.show_bgra(_frame(1), oid=OverlayId.TIP)
    published = _published_path(overlay, OverlayId.TIP)
    expected = _frame(1).tobytes()

    with published.open("rb") as held:
        overlay.show_bgra(_frame(2), oid=OverlayId.TIP)
        assert held.read() == expected
        assert published.read_bytes() == expected  # not merely a live fd — the NAME still resolves

    overlay.close()


def test_the_path_named_in_the_last_overlay_add_is_still_readable_after_the_next_publish():
    """`repaint` re-issues `_live`'s tail from another thread, and `_live` is only updated *after*
    `overlay-add` returns — so between a publish and that assignment, the tail still names the
    previous frame. Retiring it in that window hands mpv a deleted path."""
    overlay = Overlay(util.FakeIPC())
    overlay.show_bgra(_frame(1), oid=OverlayId.TIP)
    previous = _published_path(overlay, OverlayId.TIP)

    overlay.show_bgra(_frame(2), oid=OverlayId.TIP)

    assert previous.exists(), "the tail repaint would re-issue names a path that is already gone"
    overlay.close()


def _record_published_paths(overlay: Overlay) -> list[pathlib.Path]:
    """Every path production hands out for a frame, in order.

    Hooks `_new_frame_path`, so it sees the `show_bgra` route only — `prepare_rgba` allocates its
    own tempfile and would be invisible here rather than counted.

    Not `_frame_history`: `_sweep` truncates that list whether or not it deleted anything, so it
    cannot distinguish "retired" from "leaked". Not a temp-directory glob either — a parallel worker
    publishing to the same overlay id writes the same name shape into the same directory.
    """
    produced: list[pathlib.Path] = []
    issue = overlay._new_frame_path

    def recording(oid: int) -> pathlib.Path:
        path = issue(oid)
        produced.append(path)
        return path

    overlay._new_frame_path = recording
    return produced


def test_retirement_is_bounded_even_while_the_overlay_is_hidden():
    """A fresh path per frame is a leak unless something retires them, and `_add` short-circuits when
    `visible` is False — so a scheme that retires "after the next add lands" stops draining exactly
    when nothing is drawing. Bound it on publication instead.

    Counted over what production actually published, not over `_frame_history` — see
    :func:`_record_published_paths` for why that list cannot answer this.
    """
    overlay = Overlay(util.FakeIPC())
    published = _record_published_paths(overlay)
    overlay.set_visible(visible=False)

    for value in range(30):
        overlay.show_bgra(_frame(value % 250), oid=OverlayId.TIP)

    assert len(published) == 30, "the recorder missed publications"
    assert len([path for path in published if path.exists()]) <= RETAINED_FRAMES

    overlay.close()


@pytest.mark.timeout(30)
def test_a_committed_frame_is_not_retired_under_an_in_flight_repaint():
    """`commit_prepared` retires the frame it replaces, which is the one a concurrent `repaint` is
    most likely to be carrying.

    Drives the lifecycle route (`prepare_rgba`/`commit_prepared`); the rest of this file drives
    `show_bgra`, and the two publish differently.
    """
    released = threading.Event()
    observed: list[bool] = []

    class BlockingIPC(util.FakeIPC):
        def command(self, *args):
            if args[0] == "overlay-add" and threading.current_thread().name == "repaint":
                # Unbounded: a bounded wait is a barrier, and if the main thread overran it the
                # repaint would release, `commit_prepared` would legitimately retire the frame, and
                # the test would fail on correct code. The timeout marker is the backstop.
                released.wait()
                observed.append(pathlib.Path(args[4]).exists())
            return super().command(*args)

    overlay = Overlay(BlockingIPC())
    first = overlay.prepare_rgba(_frame(1), 0, 0, oid=OverlayId.SUB, revision=1)
    overlay.commit_prepared(first)

    reader = threading.Thread(target=overlay.repaint, name="repaint")
    reader.start()
    try:
        # Deadlined, not open-ended: if the hold regresses the path never arrives, and an unbounded
        # poll would wedge the run instead of reporting the regression it exists to catch.
        deadline = time.monotonic() + 10
        while first.path not in overlay._in_flight:
            if time.monotonic() > deadline:
                pytest.fail("repaint never registered a hold on the committed frame")
            time.sleep(0.01)
        second = overlay.prepare_rgba(_frame(2), 0, 0, oid=OverlayId.SUB, revision=2)
        overlay.commit_prepared(second)

        assert first.path.exists(), "retired a frame an in-flight overlay-add still names"
    finally:
        released.set()
        reader.join()

    assert observed == [True]
    overlay.close()


def test_the_oracle_catches_retirement_one_step_too_early():
    """Negative control: retiring the previous frame the moment a new one is published is the failure
    the two tests above exist to catch, so they must be able to see it.

    Holds no descriptor on the frame it expects to disappear — on Windows a delete is refused while
    anyone holds the file open, so the test's own handle would keep it alive.
    """
    overlay = Overlay(util.FakeIPC())
    overlay.show_bgra(_frame(1), oid=OverlayId.TIP)
    previous = _published_path(overlay, OverlayId.TIP)
    overlay.show_bgra(_frame(2), oid=OverlayId.TIP)
    assert previous.exists()  # the retention window still covers it

    # The production sweep, asked to keep one — what a one-deep scheme would have done.
    overlay._sweep(overlay.physical_oid(OverlayId.TIP), retain=1)

    assert not previous.exists()
    overlay.close()


def test_closing_a_shifted_overlay_removes_the_ids_it_actually_drew():
    """`_files` is keyed by PHYSICAL id, so a `close` that sends each back through `hide` shifts it
    twice: at `overlay_id_base = 10` it removes 19 and 20 while the overlays sit at 10 and 11, leaving
    them drawn after detach and their frames on disk. Invisible at the default base."""
    removed: list[int] = []

    class RecordingIPC(util.FakeIPC):
        def command(self, *args):
            if args[0] == "overlay-remove":
                removed.append(args[1])
            return super().command(*args)

    overlay = Overlay(RecordingIPC(), id_base=10)
    prepared = overlay.prepare_rgba(_frame(1), 0, 0, oid=OverlayId.SUB, revision=1)
    overlay.commit_prepared(prepared)
    overlay.show_bgra(_frame(2), oid=OverlayId.TIP)
    published = list(overlay._files.values())

    overlay.close()

    assert sorted(removed) == [
        overlay.physical_oid(OverlayId.SUB),
        overlay.physical_oid(OverlayId.TIP),
    ]
    assert [path for path in published if path.exists()] == []


def test_closing_retires_every_frame_it_published():
    overlay = Overlay(util.FakeIPC())
    published = _record_published_paths(overlay)
    for value in range(5):
        overlay.show_bgra(_frame(value), oid=OverlayId.TIP)

    overlay.close()

    assert published
    assert [path for path in published if path.exists()] == []


def _publish_concurrently(overlay: Overlay, frame: np.ndarray, *, publishers: int) -> None:
    """Drive `publishers` upload threads against one oid while a reader thread repaints."""
    stop = threading.Event()

    def repaint_loop() -> None:
        while not stop.is_set():
            overlay.repaint()

    def publish_loop() -> None:
        for _ in range(150):
            overlay.show_bgra(frame, oid=OverlayId.TIP)

    reader = threading.Thread(target=repaint_loop, name="repaint")
    uploads = [threading.Thread(target=publish_loop, name=f"upload{i}") for i in range(publishers)]
    reader.start()
    for thread in uploads:
        thread.start()
    try:
        for thread in uploads:
            thread.join()
    finally:
        stop.set()
        reader.join()


class _ReadingIPC(util.FakeIPC):
    """Reads the named frame from *inside* `overlay-add`, the way mpv does, recording every add that
    named a file it could not read or whose bytes were wrong.

    The sleep is the subject, not padding: mpv's read sits inside an IPC round-trip, and against an
    instant fake these assertions score zero failures on an implementation that retires a frame under
    an in-flight add.
    """

    delay = 0.0005

    def __init__(self, expected: int) -> None:
        super().__init__()
        self.expected = expected
        self.wrong: list[object] = []

    def command(self, *args):
        if args[0] == "overlay-add":
            time.sleep(self.delay)
            try:
                size = len(pathlib.Path(args[4]).read_bytes())  # tail = (x, y, path, …)
            except OSError as error:
                self.wrong.append(error)
            else:
                if size != self.expected:
                    self.wrong.append(size)
        return super().command(*args)


@pytest.mark.timeout(30)
def test_no_overlay_add_ever_names_a_frame_that_was_retired():
    """Two upload threads on one oid plus a repainting reader — the shape that makes retirement racy,
    and the one a single publisher cannot reach."""
    frame = _frame(4, size=32)
    ipc = _ReadingIPC(frame.nbytes)
    overlay = Overlay(ipc)
    overlay.show_bgra(frame, oid=OverlayId.TIP)

    _publish_concurrently(overlay, frame, publishers=2)

    assert ipc.wrong == []
    overlay.close()


@pytest.mark.timeout(30)
def test_the_concurrency_oracle_catches_a_publication_that_holds_nothing():
    """Negative control for the test above: neutralise the hold taken across an in-flight command and
    the same run must report retired frames. Without this, that test passes on the strength of the
    retention count and the guard could be deleted unnoticed."""
    frame = _frame(4, size=32)
    ipc = _ReadingIPC(frame.nbytes)
    overlay = Overlay(ipc)
    overlay._hold = lambda paths: []  # noqa: ARG005  # register nothing against retirement
    overlay.show_bgra(frame, oid=OverlayId.TIP)

    _publish_concurrently(overlay, frame, publishers=2)

    assert ipc.wrong, "the oracle cannot see a frame retired under an in-flight overlay-add"
    overlay.close()
