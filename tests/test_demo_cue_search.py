"""WP5.5: the demo/screenshot cue search is bounded by a deadline, not a retry count."""

from __future__ import annotations

from types import SimpleNamespace

import util

from saitenka.app.launch.run import DEMO_LINE, _wait_for_subtitle_text


class FakeIPC(util.FakeIPC):
    """Counts cue hops. Inherits the shared fake so the runtime egress port is present — the hop is
    a correlated write now, and a double without the port would take a branch production never does.
    """

    def __init__(self) -> None:
        super().__init__()
        self.seeks = 0

    def command(self, *args):
        if args and args[0] == "sub-seek":
            self.seeks += 1
        return super().command(*args)


def reader_for(texts: list[str], ipc: FakeIPC, clock: list[float]):
    """A reader whose `sub-text` follows `texts`, one value per hop.

    Each hop advances the injected clock by the time it was granted, so a search that never finds a
    cue exhausts its deadline without the test spending it — the determinism rule, and the
    difference between a 20-second suite and an instant one.
    """

    def get(name: str):
        return texts[min(ipc.seeks, len(texts) - 1)] if name == "sub-text" else None

    def drive(timeout: float | None) -> None:
        clock[0] += timeout or 0.0

    return SimpleNamespace(refresh_osd=lambda: None, _get=get, _drive_annotation_once=drive)


def test_a_cue_already_showing_needs_no_search() -> None:
    ipc = FakeIPC()
    clock = [0.0]
    reader = reader_for(["猫を見る"], ipc, clock)

    assert _wait_for_subtitle_text(reader, ipc, "/a.mkv", clock=lambda: clock[0]) == "猫を見る"
    assert ipc.seeks == 0


def test_it_hops_until_a_cue_lands() -> None:
    ipc = FakeIPC()
    clock = [0.0]
    reader = reader_for(["", "", "犬も見る"], ipc, clock)

    assert _wait_for_subtitle_text(reader, ipc, "/a.mkv", clock=lambda: clock[0]) == "犬も見る"
    assert ipc.seeks == 2  # stopped at the cue rather than running a fixed count out


def test_each_hop_is_bounded_so_the_search_keeps_seeking() -> None:
    """A step handed the whole remaining budget would park on the first wake and seek once. The
    per-hop cap is what makes this a search rather than a single long wait."""
    ipc = FakeIPC()
    waits: list[float | None] = []
    clock = [0.0]
    reader = reader_for([""], ipc, clock)
    original = reader._drive_annotation_once

    def record(timeout: float | None) -> None:
        waits.append(timeout)
        original(timeout)  # still advances the clock, or the search never reaches its deadline

    reader._drive_annotation_once = record

    _wait_for_subtitle_text(reader, ipc, "/a.mkv", clock=lambda: clock[0])

    assert waits
    assert max(w for w in waits if w is not None) <= 0.12


def test_a_search_that_never_finds_a_cue_falls_back() -> None:
    """The bound is a deadline: on a slow machine a retry count means nothing, and the demo has to
    end up with *something* to render either way."""
    ipc = FakeIPC()
    clock = [0.0]
    reader = reader_for([""], ipc, clock)

    assert _wait_for_subtitle_text(reader, ipc, "/a.mkv", clock=lambda: clock[0]) == DEMO_LINE


def test_no_video_never_seeks() -> None:
    """Nothing to seek through, so waiting could only ever time out."""
    ipc = FakeIPC()
    clock = [0.0]
    reader = reader_for([""], ipc, clock)

    assert _wait_for_subtitle_text(reader, ipc, None, clock=lambda: clock[0]) == DEMO_LINE
    assert ipc.seeks == 0
