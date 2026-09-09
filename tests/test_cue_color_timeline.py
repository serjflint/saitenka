"""A cue *timeline*, driven through observation and judged by the field readout.

Every defect behind the unscannable-cue investigation was found by a human watching an episode, and
the reason the suite could not find them is the shape of the suite: each test drives one cue, and
the failures lived in the handoff *between* cues — a cache key that moved when a cue was re-entered,
a box that outlived its cue, a fence that stepped on every blank gap.

The oracle is `tools/report_color_latency.py` — the same readout a field bundle goes through — so it
is gated here rather than only trusted. What it derives from a bundle is a latency; a recorded span
carries no clock, so that number is not asserted. The deterministic *shadow* of it is: a cue
re-entered has to hit its own filed result instead of re-rendering, and a cue owed color has to get
it before the next one arrives.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from saitenka_subtitles import Cue, CueIndex
from test_native_subtitles import reader, settle_geometry, settle_jobs
from util import record_spans

from saitenka.app.subtitle_intents import SeekCue

if TYPE_CHECKING:
    from session_builder import TestSession
    from test_native_subtitles import FakeIPC

_READOUT = Path(__file__).resolve().parent.parent / "tools" / "report_color_latency.py"


def _load_readout():
    spec = importlib.util.spec_from_file_location("_color_latency", _READOUT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `Appearance` is a dataclass, and `dataclasses` resolves a field
    # annotation through `sys.modules[cls.__module__]`, which an unregistered module has no entry in.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


readout = _load_readout()


#: Touching boundaries, so each successor arrives in the frame its predecessor ends. `猫を見る`
#: appears twice, several cues apart, which is the re-entry the result cache exists for and the one
#: a repeated-line digest cannot distinguish from a single long showing.
TIMELINE: tuple[Cue, ...] = (
    Cue(1.0, 3.0, "猫を見る"),
    Cue(3.0, 5.0, "犬も見る"),
    Cue(5.0, 7.0, "鳥を見た"),
    Cue(7.0, 9.0, "猫を見る"),
)


def _dialogue(cue: Cue) -> str:
    def stamp(seconds: float) -> str:
        return f"{int(seconds) // 3600}:{int(seconds) // 60 % 60:02d}:{seconds % 60:05.2f}"

    return f"Dialogue: 0,{stamp(cue.start)},{stamp(cue.end)},Default,,0000,0000,0000,,{cue.text}"


def _source(cues: tuple[Cue, ...]) -> bytes:
    from test_native_subtitles import ASS

    header = ASS.decode().split("Dialogue:")[0]
    return (header + "\n".join(_dialogue(cue) for cue in cues) + "\n").encode()


def _show(result: TestSession, ipc: FakeIPC, cue: Cue, *, at: float | None = None) -> None:
    """Put a cue on screen the way mpv does — the properties move and the session observes them.

    Not `cue.set_subtitle`: that is the writer *downstream* of the observation, so a test using it
    never crosses the seam where the cue's identity is decided, and `timestamp_ms` — the field whose
    two derivations were the root cause — is read off the observed playhead. `at` is where inside
    the cue the playhead sits, which is what a seek and a steady advance disagree about.
    """
    ipc.set_prop("sub-text/ass-full", _dialogue(cue))
    ipc.set_prop("sub-start", cue.start)
    ipc.set_prop("sub-end", cue.end)
    ipc.set_prop("time-pos", cue.start + 0.25 if at is None else at)
    ipc.set_prop("sub-text", cue.text)
    _settle(result, ipc)


def _settle(result: TestSession, ipc: FakeIPC) -> None:
    result.pump()
    settle_geometry(result, ipc)
    settle_jobs(result, ipc)
    result.pump()


@pytest.fixture
def timeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spans = record_spans(monkeypatch)
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "timeline.ass"
    source.write_bytes(_source(TIMELINE))
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.track_commands.navigation.current.sub_index = CueIndex(list(TIMELINE))
    yield result, ipc, backend, spans
    result.close()


def _appearances(spans: list[dict]):
    return readout.appearances(
        readout.from_records(spans, "subtitle_draw"),
        readout.from_records(spans, "subtitle_geometry_decision"),
    )


def _lanes(spans: list[dict]) -> list[str]:
    return [span["attrs"]["outcome"] for span in spans if span["name"] == "subtitle_geometry_lane"]


def test_every_cue_in_a_timeline_gets_the_color_it_is_owed(timeline) -> None:
    """The whole-session claim the per-cue tests cannot make.

    Read through the field readout so a failure here reads as the bundle would: an appearance owed
    color that never got one is exactly the line a viewer reports as "the cue didn't work".
    """
    result, ipc, _backend, spans = timeline

    for cue in TIMELINE:
        _show(result, ipc, cue)

    shown = _appearances(spans)
    assert [item.cue for item in shown], "no native draws — the timeline never reached the screen"
    uncolored = [item for item in shown if item.owed_color and item.wait is None]
    assert not uncolored, f"{len(uncolored)} of {len(shown)} appearances never colored"


def test_no_draw_in_a_timeline_carries_a_box_its_cue_cannot_use(timeline) -> None:
    """The pairing behind `tokens=0, measured_boxes=3` in the field: geometry filed against one cue
    reaching a draw of another. It is never a paint, so a wait-to-color reading counts it as a
    success — which is how it survived a whole day in the first bundle of the investigation."""
    result, ipc, _backend, spans = timeline

    for cue in TIMELINE:
        _show(result, ipc, cue)

    orphans = sum(item.orphan_boxes for item in _appearances(spans))
    assert orphans == 0, f"{orphans} draw(s) carried boxes with no tokens to put them on"


def test_a_cue_re_entered_at_a_different_instant_serves_itself_from_cache(timeline) -> None:
    """Two claims in one act, because they fail independently and look identical from a seat.

    The key must not move: `timestamp_ms` reaches it and used to come from the playhead, so a cue
    entered by a seek and by a steady advance produced two keys. And the entry must still be there:
    a cue's own forward lookahead must not evict the cue it was speculating for.

    The wait either one costs is wall-clock, which this harness has no clock for. The miss is not,
    and `rendering` where `cached` is owed is the same defect one layer up.
    """
    result, ipc, _backend, spans = timeline
    _show(result, ipc, TIMELINE[0])
    _show(result, ipc, TIMELINE[1])
    first = len(_lanes(spans))

    # Back onto the first cue, and deliberately NOT at the instant it was first rendered at: the
    # playhead lands wherever the seek put it, which is the divergence the key must not carry.
    _show(result, ipc, TIMELINE[0], at=TIMELINE[0].start + 1.75)

    assert "cached" in _lanes(spans)[first:], (
        "stepping back re-rendered a cue rendered moments ago: either the result-cache key still "
        "moves within one cue, or the lookahead evicted the cue it was speculating for"
    )


def test_a_navigated_cue_lands_scannable_with_boxes_its_tokens_own(timeline) -> None:
    """Navigation, not observation, is how a viewer re-reads a line they missed — and it is the
    entry point that skips the lookahead's filing order entirely."""
    result, ipc, _backend, spans = timeline
    _show(result, ipc, TIMELINE[0])

    assert result.graph.subtitle_navigation.seek(SeekCue(1, result.graph.cue.revision))
    _show(result, ipc, TIMELINE[1])

    cue = result.graph.subtitle_presentation.cue.current
    assert cue.boxes, "the navigated cue is on screen with no hit boxes"
    assert all(0 <= box.index < len(cue.tokens) for box in cue.boxes)
    assert sum(item.orphan_boxes for item in _appearances(spans)) == 0
