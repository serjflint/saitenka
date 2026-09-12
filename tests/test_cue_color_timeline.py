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

import contextlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from saitenka_subtitles import Cue, CueIndex
from test_native_subtitles import reader, settle_geometry, settle_jobs
from util import record_spans

from saitenka import otel_metrics
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


def _coloring():
    """A scorer, so tokens carry styles: without one every style is `None`, nothing is colorable,
    and a test about what a cue owes would pass for the wrong reason."""
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    return Coloring(
        Scorer(known=KnownWords.from_set(["猫"]), enable_freq=False, enable_jlpt=False), Palette()
    )


def _session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cues: tuple[Cue, ...], *, scorer=None
):
    spans = record_spans(monkeypatch)
    result, ipc, backend = reader(tmp_path, scorer=scorer)
    source = tmp_path / "timeline.ass"
    source.write_bytes(_source(cues))
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.track_commands.navigation.current.sub_index = CueIndex(list(cues))
    return result, ipc, backend, spans


@pytest.fixture
def timeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    result, ipc, backend, spans = _session(tmp_path, monkeypatch, TIMELINE)
    yield result, ipc, backend, spans
    result.close()


#: Two speakers authored as two events with one span — the shape 38 times in one real episode, and
#: the shape that refused geometry on six seeks in every field bundle.
CO_TIMED: tuple[Cue, ...] = (
    Cue(1.0, 3.0, "猫を見る"),
    Cue(3.0, 5.0, "犬も見る"),
    Cue(3.0, 5.0, "鳥を見た"),
    Cue(5.0, 7.0, "帰ろうか"),
)


@pytest.fixture
def overlapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    result, ipc, backend, spans = _session(tmp_path, monkeypatch, CO_TIMED)
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


def _refusals(spans: list[dict]) -> list[str]:
    return [
        span["attrs"].get("reason")
        for span in spans
        if span["name"] == "subtitle_geometry_decision" and span["attrs"].get("outcome") != "ready"
    ]


def test_navigating_into_an_overlap_paints_without_waiting_for_mpv(overlapped) -> None:
    """The seek a viewer feels as a delay.

    Navigation steps to an authored event; mpv draws the *set* active at that instant. Drawing only
    the event handed geometry a line the document does not have there, so it refused
    (`subtitle-hint-text-mismatch`) and color could not appear until mpv's own `sub-text` arrived
    tens of milliseconds later. Six such seeks in every field bundle, ~50 ms each.

    So: the frame is what gets drawn, and geometry paints it from the index with no round trip.
    """
    result, ipc, _backend, spans = overlapped
    _show(result, ipc, CO_TIMED[0])
    mark = len(spans)

    assert result.graph.subtitle_navigation.seek(SeekCue(1, result.graph.cue.revision))

    assert result.graph.playback.cue.text == "犬も見る\n鳥を見た"
    assert "subtitle-hint-text-mismatch" not in _refusals(spans[mark:])
    cue = result.graph.subtitle_presentation.cue.current
    assert cue.boxes, "the navigated frame is on screen with no hit boxes"
    assert all(0 <= box.index < len(cue.tokens) for box in cue.boxes)
    # A HIT, not merely a paint. The lookahead already speculates per visibility boundary and files
    # under the document's rows there; the point of drawing the frame is that navigation now asks
    # the same question, so the answer is already on the shelf and nothing renders.
    assert [
        span["attrs"]["outcome"]
        for span in spans[mark:]
        if span["name"] == "subtitle_geometry_cache"
    ] == ["hit"]


def test_navigating_onto_a_lone_cue_is_unchanged_by_the_frame(timeline) -> None:
    """The negative control, on a track with no overlap at all. A cue sharing its span with nobody
    draws exactly what it drew before — a frame-aware path that quietly widened every cue would
    satisfy the test above and fail here."""
    result, ipc, _backend, spans = timeline
    _show(result, ipc, TIMELINE[0])
    mark = len(spans)

    assert result.graph.subtitle_navigation.seek(SeekCue(1, result.graph.cue.revision))

    assert result.graph.playback.cue.text == TIMELINE[1].text
    assert "subtitle-hint-text-mismatch" not in _refusals(spans[mark:])


def test_boxes_in_an_overlap_name_the_event_each_word_was_measured_in(overlapped) -> None:
    """Two speakers on screen are two authored events and one flat token list, so a token index
    alone cannot say whose word was clicked. Geometry never lost that — `TokenGeometry.event_id`
    carries it — and the cue layer used to drop it on the way to `WordBox`.

    Asserts the partition, not merely that the field is populated: one event answering for every
    box is exactly the state the drop produced, and it is not distinguishable from "populated".
    """
    result, ipc, _backend, spans = overlapped
    _show(result, ipc, CO_TIMED[0])

    assert result.graph.subtitle_navigation.seek(SeekCue(1, result.graph.cue.revision))

    boxes = result.graph.subtitle_presentation.cue.current.boxes
    assert boxes
    assert len({box.event_id for box in boxes}) == 2, (
        "every box named one event, but two are on screen"
    )
    assert all(box.event_id is not None for box in boxes)
    drawn = [span for span in spans if span["name"] == "subtitle_draw"]
    assert drawn[-1]["attrs"]["box_events"] == 2


#: A sign held across a scene while dialogue comes and goes underneath it — partial overlap, the
#: general case the format allows. Absent from this corpus (0 in 576 events) but not from the format.
HELD_SIGN: tuple[Cue, ...] = (
    Cue(0.5, 7.0, "看板を見る"),
    Cue(1.0, 3.0, "猫を見る"),
    Cue(3.0, 5.0, "犬も見る"),
)


def test_a_cue_under_a_held_sign_draws_both_and_paints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial overlap: the frame's membership changes while a cue is still on screen, so no cue's
    own span describes what is drawn. The lookahead already speculates per visibility boundary
    rather than per cue, so navigation agreeing with it is the whole claim — one of them working
    off cue spans is a key the other can never match.
    """
    result, ipc, _backend, spans = _session(tmp_path, monkeypatch, HELD_SIGN)
    try:
        _show(result, ipc, HELD_SIGN[0], at=0.6)  # the sign alone, before any dialogue
        assert result.graph.playback.cue.text == "看板を見る"

        assert result.graph.subtitle_navigation.seek(SeekCue(1, result.graph.cue.revision))

        assert result.graph.playback.cue.text == "看板を見る\n猫を見る"
        assert "subtitle-hint-text-mismatch" not in _refusals(spans)
        assert result.graph.subtitle_presentation.cue.current.boxes
    finally:
        result.close()


#: A run of music markers between two spoken lines. Three, because the lookahead window is two: a
#: run longer than the window is what makes it matter that a frame with nothing to paint still
#: spends a slot. `♬～` tokenizes, but every token is one the tokenizer skips, so the frame owes no
#: color — the commonest such cue in anime, held 14 s and 19 s in the episode this was traced
#: against.
SILENCE: tuple[Cue, ...] = (
    Cue(1.0, 3.0, "猫を見る"),
    Cue(3.0, 5.0, "♬～"),
    Cue(5.0, 7.0, "♬～"),
    Cue(7.0, 9.0, "♬～"),
    Cue(9.0, 11.0, "犬も見る"),
)


def _prefetched_timestamps(spans: list[dict]) -> list[int]:
    return [
        span["attrs"]["timestamp_ms"]
        for span in spans
        if span["name"] == "subtitle_geometry_prepare"
    ]


def test_the_line_behind_a_silent_stretch_is_ready_when_it_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewer-visible claim, and the one the field bundle failed.

    Whether a frame has anything to paint is only known after tokenizing it, on the worker — so the
    queuer counts one against its window and finds out later that it built nothing. Two markers in
    a row is the whole window, and the spoken line behind them was never speculated: `completed`
    frozen across the entire silent stretch, then the next line missing `first-seen` and paying a
    full render.
    """
    result, ipc, _backend, spans = _session(tmp_path, monkeypatch, SILENCE)
    try:
        for cue in SILENCE[:4]:
            _show(result, ipc, cue)
        mark = len(spans)

        _show(result, ipc, SILENCE[4])  # the spoken line, arriving after the silence

        assert [
            span["attrs"]["outcome"]
            for span in spans[mark:]
            if span["name"] == "subtitle_geometry_cache"
        ] == ["hit"], "the line behind a run of silent cues rendered cold"
    finally:
        result.close()


def test_a_cue_owed_no_color_still_reads_ahead_for_the_ones_that_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half, and a separate gap: standing on a silent cue used to speculate nothing at
    all — the eligibility check returned before the lookahead ran. Whether *this* frame has
    anything to paint says nothing about its successors.

    Asserted while a marker holds the screen, which is where the old path did nothing.
    """
    result, ipc, _backend, spans = _session(tmp_path, monkeypatch, SILENCE)
    try:
        _show(result, ipc, SILENCE[0])
        mark = len(spans)

        _show(result, ipc, SILENCE[1])  # a marker takes the screen

        assert _prefetched_timestamps(spans[mark:]), (
            "nothing was read ahead while a silent cue was on screen"
        )
    finally:
        result.close()


def _show_without_timings(result: TestSession, ipc: FakeIPC, cue: Cue) -> None:
    """A cue arriving textually before it arrives temporally — mpv publishes `sub-text` and
    `sub-start`/`sub-end` as separate property changes, and the gap between them is real."""
    ipc.set_prop("sub-text/ass-full", _dialogue(cue))
    ipc.set_prop("sub-start", None)
    ipc.set_prop("sub-end", None)
    ipc.set_prop("time-pos", cue.start + 0.25)
    ipc.set_prop("sub-text", cue.text)
    _settle(result, ipc)


def test_a_cue_whose_timings_have_not_arrived_is_measured_from_the_index(timeline) -> None:
    """The last visible instance of text-before-color, measured at 58 ms and 111 ms in the field.

    Geometry needed `sub-start`/`sub-end` and refused without them, so the line drew plain and
    stayed plain until mpv's second property change landed. The timings it needs are the cue's own
    and the index has them — the same answer a navigation hint already takes, with the same guard:
    an index disagreeing with the text on screen does not get to choose the instant.
    """
    result, ipc, _backend, spans = timeline

    _show_without_timings(result, ipc, TIMELINE[0])

    assert "subtitle-observation-pending" not in _refusals(spans)
    cue = result.graph.subtitle_presentation.cue.current
    assert cue.boxes, "a cue with no timings yet drew plain and stayed plain"
    assert all(0 <= box.index < len(cue.tokens) for box in cue.boxes)


def test_an_unknown_cue_without_timings_still_waits(timeline) -> None:
    """The negative control. Substituting the index is only sound when the index agrees; a cue it
    cannot identify has to keep waiting for mpv rather than be measured at a guessed instant."""
    result, ipc, _backend, spans = timeline
    stranger = Cue(11.0, 13.0, "知らない行")  # not in the index this session loaded

    _show_without_timings(result, ipc, stranger)

    assert "subtitle-observation-pending" in _refusals(spans)
    assert not result.graph.subtitle_presentation.cue.current.boxes


def test_a_cue_matching_the_index_only_after_normalisation_still_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's own control, and not the same case as an unknown cue.

    `locate` matches on NORMALISED text — whitespace collapsed — so it answers for a cue whose
    spelling differs from the one on screen. Taking that cue's timings would measure one line's
    instant for another's text, which is the failure the navigation hint already guards against.
    An unknown cue cannot reach this: `locate` returns -1 and the first half of the check catches
    it, which is why that test alone left this half unexercised.
    """
    spaced = Cue(1.0, 3.0, "猫を　見る")  # ideographic space; the index holds this spelling
    result, ipc, _backend, spans = _session(tmp_path, monkeypatch, (spaced, *TIMELINE[1:]))
    try:
        _show_without_timings(result, ipc, Cue(1.0, 3.0, "猫を 見る"))  # mpv reports an ASCII one

        assert "subtitle-observation-pending" in _refusals(spans)
        assert not result.graph.subtitle_presentation.cue.current.boxes
    finally:
        result.close()


def _draws(spans: list[dict]) -> list[dict]:
    return [span["attrs"] for span in spans if span["name"] == "subtitle_draw"]


def test_a_draw_says_how_much_color_its_cue_was_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Makes a draw self-describing, which it was not.

    `measured_boxes=0` reads identically for a line whose geometry has not landed and for a music
    marker that owes nothing, and telling them apart meant joining to a decision span that is often
    absent — "no geometry decision recorded" was this readout's most common verdict on exactly the
    appearances nobody could explain.
    """
    result, ipc, _backend, spans = _session(tmp_path, monkeypatch, SILENCE, scorer=_coloring())
    try:
        _show(result, ipc, SILENCE[0])  # 猫を見る — content words, owes color
        spoken = _draws(spans)[-1]
        mark = len(spans)
        _show(result, ipc, SILENCE[1])  # ♬～ — every token skippable, owes nothing

        silent = _draws(spans[mark:])[-1]
        assert spoken["owed_color"] > 0
        assert silent["owed_color"] == 0
        assert silent["measured_boxes"] == 0  # …and the two are distinguishable despite this
    finally:
        result.close()


def test_a_cue_that_loses_the_color_it_had_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait-to-color reading takes the FIRST colored draw and stops, so color going away later
    is invisible to it. Three such drops were found by hand in one bundle and none by the readout."""
    result, ipc, _backend, spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        assert _draws(spans)[-1]["measured_boxes"] > 0
        mark = len(spans)

        presentation = result.graph.subtitle_presentation
        presentation.cue.replace_geometry(boxes=[])  # what a degrade leaves behind
        presentation.pipeline.draw_current(presentation.target())

        dropped = _draws(spans[mark:])[-1]
        assert dropped["measured_boxes"] == 0
        assert dropped["lost_color"] is True
    finally:
        result.close()


def test_the_write_that_carries_the_color_is_timed_to_mpvs_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subtitle's own overlay write does not go through `LifecycleSurfaces`, so the round-trip
    span added there covered the toast and the loading spinner and not the payload that actually
    carries the color — the one write on the draw path with nothing measuring mpv's side of it."""
    result, ipc, _backend, spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])

        written = [
            span["attrs"]
            for span in spans
            if span["name"] == "surface_write"
            and span["attrs"].get("slot") == "subtitle-native-focus"
        ]
        assert written, "the color payload's round trip is unmeasured"
        assert written[-1]["command"] == "osd-overlay"
        assert written[-1]["events"] > 0  # the ASS events mpv has to parse
        assert written[-1]["round_trip_ms"] >= 0.0
    finally:
        result.close()


def _color_writes(ipc: FakeIPC) -> list[tuple]:
    """Every `osd-overlay` carrying a color payload — the write mpv pays 2-33 ms to composite.

    Narrowed to the focus slot's own id: the layout calibration writes the SAME payload to its own
    hidden id, so a filter on `ass-events` alone counts it as a duplicate of the thing it is
    measuring.
    """
    from saitenka.app.subtitle_render import NATIVE_FOCUS_ID

    return [
        command
        for command in ipc.commands
        if command
        and command[0] == "osd-overlay"
        and command[1] == NATIVE_FOCUS_ID
        and command[2] == "ass-events"
    ]


def test_one_cue_writes_its_color_payload_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cue_redraw` and `subtitle_geometry_apply` both draw in the same millisecond and build the
    same payload, so every cue paid mpv twice to composite one picture.

    Measured, not assumed: `surface_write.round_trip_ms` puts each of those writes at 2-33 ms, back
    to back, byte-identical — 987.513 and 987.514 in one bundle, 33.4 ms and 33.0 ms, 11 events
    each.
    """
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        assert _color_writes(ipc), "the cue never wrote a color payload"
        before = len(_color_writes(ipc))

        # The second draw, with nothing changed — which is what the runtime does: `cue_redraw` and
        # `subtitle_geometry_apply` both reach `draw` for one arrival.
        presentation = result.graph.subtitle_presentation
        presentation.pipeline.draw_current(presentation.target())

        assert len(_color_writes(ipc)) == before, "the same payload was sent to mpv twice"
    finally:
        result.close()


def _focus_writes(ipc: FakeIPC) -> list[tuple]:
    """Every write to the focus slot, color payloads and removals alike, in order."""
    from saitenka.app.subtitle_render import NATIVE_FOCUS_ID

    return [
        command
        for command in ipc.commands
        if command and command[0] == "osd-overlay" and command[1] == NATIVE_FOCUS_ID
    ]


def _blank(result: TestSession, ipc: FakeIPC, *, at: float) -> None:
    """The gap between cues, the way mpv reports it."""
    ipc.set_prop("sub-text/ass-full", "")
    ipc.set_prop("sub-start", None)
    ipc.set_prop("sub-end", None)
    ipc.set_prop("time-pos", at)
    ipc.set_prop("sub-text", "")
    _settle(result, ipc)


def test_a_cue_change_replaces_the_color_in_one_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured against mpv's frame draw: the color write lands in the cue's own frame only inside a
    5–20 ms window, and a removal sent ahead of it was a second command mpv parsed first — and a
    frame composited between the two showed the cue white. The next cue's payload replaces the
    previous one in a single write."""
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        assert _color_writes(ipc), "the first cue never wrote its color"
        before = len(_focus_writes(ipc))

        _show(result, ipc, TIMELINE[1])

        since = _focus_writes(ipc)[before:]
        assert [command[2] for command in since] == ["ass-events"], since
    finally:
        result.close()


def test_a_blank_after_a_colored_cue_removes_the_color_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant the single write must not cost: when the line goes away, so does its color —
    in one removal, not the two the teardown and the empty draw used to send."""
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        before = len(_focus_writes(ipc))

        _blank(result, ipc, at=TIMELINE[0].end + 0.1)

        since = _focus_writes(ipc)[before:]
        assert [command[2] for command in since] == ["none"], since
    finally:
        result.close()


def test_a_second_blank_sends_no_second_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slot is empty after the first blank; a session clearing the line again on top of it
    (a track switch installs an empty cue) must not cost mpv another command."""
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        before = len(_focus_writes(ipc))

        _blank(result, ipc, at=TIMELINE[0].end + 0.1)
        result.graph.cue.set_subtitle("")  # `clear_cue`, the way a track switch spells it

        assert [command[2] for command in _focus_writes(ipc)[before:]] == ["none"]
    finally:
        result.close()


def test_a_seek_keeps_its_pre_armed_color_while_mpv_catches_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `sub-seek` pre-arms the target's color before mpv moves. mpv then, in order: withdraws the
    rows and timings and blanks the text while the seek is in flight; reports the landed text
    alone; reports its rows and timings a turn later. Traced on ten seeks: the pre-armed color was
    up 7 ms after the press, taken down 12 ms later by a geometry refresh that could not key the
    blank, and put back 30 ms after that — which is the line arriving white and turning blue."""
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        assert result.graph.subtitle_navigation.seek(SeekCue(1, result.graph.cue.revision))
        _settle(result, ipc)
        assert _color_writes(ipc)[-1:], "the seek never pre-armed the target's color"
        before = len(_focus_writes(ipc))
        landed = TIMELINE[1]

        _blank(result, ipc, at=landed.start + 0.01)  # mid-seek
        ipc.set_prop("sub-text", landed.text)  # the text half
        _settle(result, ipc)
        ipc.set_prop("sub-text/ass-full", _dialogue(landed))  # the rows and timings, a turn later
        ipc.set_prop("sub-start", landed.start)
        ipc.set_prop("sub-end", landed.end)
        _settle(result, ipc)

        since = _focus_writes(ipc)[before:]
        assert [command[2] for command in since] == [], since
        assert result.graph.subtitle_presentation.cue.current.boxes
    finally:
        result.close()


def test_a_cue_owing_no_color_takes_the_previous_color_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line with nothing to color must still clear the one before it: deferring the removal to
    the draw is only safe because a draw with nothing to show pays it."""
    silent = (Cue(1.0, 3.0, "猫を見る"), Cue(3.0, 5.0, "……"))
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, silent, scorer=_coloring())
    try:
        _show(result, ipc, silent[0])
        assert _color_writes(ipc)
        before = len(_focus_writes(ipc))

        _show(result, ipc, silent[1])

        since = _focus_writes(ipc)[before:]
        assert [command[2] for command in since] == ["none"], since
    finally:
        result.close()


def test_a_cue_change_with_no_preview_up_sends_no_keybind_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every cue change dismisses the card preview; releasing its keys is a `keybind` command mpv
    queues ahead of the color write, and on the cues that never showed a preview it released
    nothing."""
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        before = len(ipc.commands)

        _show(result, ipc, TIMELINE[1])

        assert not any(c[0] == "keybind" for c in ipc.commands[before:])
    finally:
        result.close()


def test_a_split_observation_burst_writes_the_color_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mpv reports a cue as three property changes — the text, then its timing — and the reader
    can deliver them across two turns, so one cue reconciles twice. Both draws build the same
    bytes; the second was still sent, because the dedupe forgot its payload on every cue change.
    Field trace: two identical 16-event writes 6 ms apart on every such cue."""
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        before = len(_focus_writes(ipc))
        cue = TIMELINE[1]

        ipc.set_prop("sub-text/ass-full", _dialogue(cue))
        ipc.set_prop("sub-text", cue.text)
        _settle(result, ipc)  # the text arrives alone
        ipc.set_prop("sub-start", cue.start)
        ipc.set_prop("sub-end", cue.end)
        ipc.set_prop("time-pos", cue.start + 0.25)
        _settle(result, ipc)  # its timing follows, and the cue reconciles again

        assert [command[2] for command in _focus_writes(ipc)[before:]] == ["ass-events"]
    finally:
        result.close()


def test_a_changed_payload_is_always_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control that matters: skipping a needless write costs milliseconds, skipping a necessary
    one costs the color. A hover changes the payload, so it must reach mpv."""
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        _show(result, ipc, TIMELINE[0])
        before = len(_color_writes(ipc))

        result.graph.tooltip.select(0)  # adds the focus highlight to the same slot
        presentation = result.graph.subtitle_presentation
        presentation.pipeline.draw_current(presentation.target())

        written = _color_writes(ipc)
        assert len(written) > before, "a changed payload was skipped as a duplicate"
        assert written[-1][3] != written[before - 1][3]
    finally:
        result.close()


def test_the_next_cue_writes_even_when_its_payload_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeated line renders to the same payload, and skipping it would leave the previous cue's
    color up. The cache is dropped on every cue change, so identity never spans one."""
    repeated = (TIMELINE[0], TIMELINE[1], TIMELINE[0])
    result, ipc, _backend, _spans = _session(tmp_path, monkeypatch, repeated, scorer=_coloring())
    try:
        for cue in repeated:
            _show(result, ipc, cue)

        first = _color_writes(ipc)[0][3]
        assert any(command[3] == first for command in _color_writes(ipc)[1:]), (
            "the repeated cue reused the earlier cue's write instead of making its own"
        )
    finally:
        result.close()


@contextlib.contextmanager
def _telemetry():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        yield
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def test_a_timeline_times_the_color_of_every_cue_that_colors(tmp_path, monkeypatch) -> None:
    """The runtime counter reaches the field, not just its own unit test.

    `saitenka.subtitle.color_latency_ms` is joined from two components — the coordinator opens the
    wait, the renderer closes it on mpv's acknowledgement — so either end can be unwired while both
    halves still pass in isolation. Driving the timeline is what proves the join holds.
    """
    with _telemetry():
        result, ipc, _backend, spans = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
        try:
            for cue in TIMELINE:
                _show(result, ipc, cue)
            colored = [item for item in _appearances(spans) if item.owed_color]
            snap = otel_metrics.snapshot()
        finally:
            result.close()
    assert colored, "no appearance owed color — the timeline proves nothing about the timer"
    assert snap["saitenka.subtitle.color_latency_ms"]["count"] == len(colored)
