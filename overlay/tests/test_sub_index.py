"""The subtitle cue index that powers instant Alt+←/→/↓ navigation (parse + locate + step).

Parsing is a port of SubMiner's subtitle-cue-parser; locate/target mirror its
findActiveSubtitleCueIndex. All pure — no mpv, no I/O below load_index."""

from __future__ import annotations

import textwrap

from overlay.app.sub_index import (
    SubIndex,
    load_index,
    parse_ass,
    parse_cues,
    parse_srt,
)

SRT = textwrap.dedent(
    """\
    1
    00:00:01,000 --> 00:00:03,000
    こんにちは

    2
    00:00:04,500 --> 00:00:06,000
    <i>お孫さん</i>ですね

    3
    00:00:10,000 --> 00:00:12,000
    また
    あした
    """
)

ASS = textwrap.dedent(
    """\
    [Script Info]
    Title: x

    [Events]
    Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an8}こんにちは
    Dialogue: 0,0:00:04.50,0:00:06.00,Default,,0,0,0,,セリフ、読点あり
    Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,また\\Nあした
    """
)


def test_parse_srt_times_text_and_strips_tags():
    cues = parse_srt(SRT)
    assert len(cues) == 3
    assert cues[0].start == 1.0 and cues[0].end == 3.0 and cues[0].text == "こんにちは"
    assert cues[1].text == "お孫さんですね"  # <i>…</i> stripped
    assert cues[2].text == "また\nあした"  # multi-line preserved as \n


def test_parse_ass_uses_format_order_strips_overrides_keeps_text_commas():
    cues = parse_ass(ASS)
    assert len(cues) == 3
    assert cues[0].start == 1.0 and cues[0].text == "こんにちは"  # {\an8} stripped
    assert cues[1].text == "セリフ、読点あり"  # a comma inside Text is preserved (re-joined)
    assert cues[2].text == "また\\Nあした"  # ASS line break kept literally (\N)


def test_parse_cues_dispatches_by_extension_and_sorts():
    assert [c.text for c in parse_cues(SRT, "ep01.srt")] == [
        "こんにちは",
        "お孫さんですね",
        "また\nあした",
    ]
    assert len(parse_cues(ASS, "ep01.ass")) == 3
    # vtt shares the srt parser
    assert len(parse_cues(SRT, "ep01.vtt")) == 3


def test_parse_cues_falls_back_when_extension_wrong():
    # ass content behind a .srt name → the srt parser finds nothing, fallback tries ass
    assert len(parse_cues(ASS, "mislabeled.srt")) == 3


def test_load_index_reads_file(tmp_path):
    p = tmp_path / "ep.srt"
    p.write_text(SRT, encoding="utf-8")
    idx = load_index(p)
    assert idx is not None and len(idx) == 3


def test_load_index_none_on_garbage(tmp_path):
    p = tmp_path / "empty.srt"
    p.write_text("not a subtitle file\n", encoding="utf-8")
    assert load_index(p) is None
    assert load_index(tmp_path / "missing.srt") is None  # unreadable → None, never raises


# --- locate: which cue is "current" ------------------------------------------------------------


def _idx() -> SubIndex:
    return SubIndex(parse_srt(SRT))  # cues at [1,3), [4.5,6), [10,12)


def test_locate_by_sub_start_timing():
    idx = _idx()
    assert idx.locate(sub_start=4.7) == 1  # inside cue 2
    assert idx.locate(sub_start=8.0) == -1  # in a gap → no exact timing match


def test_locate_by_text_prefers_nearest_to_hint():
    idx = SubIndex(
        parse_srt("1\n00:00:01,000 --> 00:00:02,000\n…\n\n2\n00:00:05,000 --> 00:00:06,000\n…\n")
    )
    # both cues share the text "…"; the preferred hint disambiguates
    assert idx.locate(text="…", preferred=1) == 1
    assert idx.locate(text="…", preferred=0) == 0


def test_locate_by_time_pos_active_or_upcoming():
    idx = _idx()
    assert idx.locate(time_pos=2.0) == 0  # inside cue 1
    assert idx.locate(time_pos=8.5) == 2  # in a gap → the upcoming cue 3
    assert idx.locate(time_pos=99.0) == -1  # past the end → nothing upcoming


def test_locate_text_beats_stale_time():
    # chaining case: we just rendered cue 3's text, but time-pos is still stale at cue-1 territory
    idx = _idx()
    assert idx.locate(text="また\nあした", time_pos=2.0, preferred=2) == 2


# --- target: stepping prev/replay/next ---------------------------------------------------------


def test_target_steps_within_bounds():
    idx = _idx()
    assert idx.target(1, 1) == 2  # next
    assert idx.target(1, -1) == 0  # prev
    assert idx.target(1, 0) == 1  # replay
    assert idx.target(2, 1) == -1  # next past the last → out of range
    assert idx.target(0, -1) == -1  # prev before the first → out of range


def test_target_from_no_current():
    idx = _idx()
    assert idx.target(-1, 1) == 0  # next with nothing current → first cue
    assert idx.target(-1, -1) == -1  # prev/replay with nothing current → nothing
    assert idx.target(-1, 0) == -1


def test_target_from_a_gap_lands_on_the_upcoming_cue():
    """In a gap, `current` is the UPCOMING cue: next opens it (not skip past — that's what mpv's
    sub-seek 1 does), prev goes to the cue before the gap, replay defers to mpv."""
    idx = _idx()
    assert idx.target(2, 1, inside=False) == 2  # next → the upcoming cue itself
    assert idx.target(2, -1, inside=False) == 1  # prev → the cue before the gap
    assert idx.target(2, 0, inside=False) == -1  # replay from a gap → let mpv decide
    assert idx.target(0, -1, inside=False) == -1  # gap before the first cue → nothing before it
