"""The subtitle cue index that powers instant Alt+←/→/↓ navigation."""

from __future__ import annotations

import textwrap

import pytest
from saitenka_subtitles import Cue, CueIndex, parse_ass, parse_cues, parse_srt

from saitenka.app.sub_index import load_index

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
    assert cues[2].text == "また\nあした"


def test_parse_ass_strips_html_markup():
    content = ASS.replace("こんにちは", '<font color="japanese">こんにちは</font>')
    assert parse_ass(content)[0].text == "こんにちは"


def test_parse_ass_removes_drawing_runs_and_keeps_surrounding_dialogue() -> None:
    content = ASS.replace(
        "{\\an8}こんにちは",
        r"前{\p1}m 0 0 l 10 10{\p0}中{\pos(20,30)}後",
    )

    assert parse_ass(content)[0].text == "前中後"


def test_parse_ass_drops_a_drawing_only_event() -> None:
    content = ASS.replace("{\\an8}こんにちは", r"{\p1}m 0 0 l 10 10")

    assert [cue.text for cue in parse_ass(content)] == ["セリフ、読点あり", "また\nあした"]


def test_parse_ass_keeps_dialogue_from_an_unclosed_override_block() -> None:
    content = ASS.replace("{\\an8}こんにちは", r"猫{\b1犬")

    assert parse_ass(content)[0].text == r"猫{\b1犬"


@pytest.mark.parametrize("wrap_style", ["0", "2"])
def test_parse_ass_keeps_semantic_soft_breaks_across_wrap_styles(wrap_style: str) -> None:
    content = ASS.replace("Title: x", f"Title: x\nWrapStyle: {wrap_style}").replace(
        "{\\an8}こんにちは", r"猫\n犬"
    )

    assert parse_ass(content)[0].text == "猫\n犬"


def test_parse_ass_bounds_oversized_drawing_scales() -> None:
    content = ASS.replace("{\\an8}こんにちは", "{\\p" + "9" * 5000 + "}x")

    assert [cue.text for cue in parse_ass(content)] == ["セリフ、読点あり", "また\nあした"]


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


def _idx() -> CueIndex:
    return CueIndex(parse_srt(SRT))  # cues at [1,3), [4.5,6), [10,12)


def test_locate_by_sub_start_timing():
    idx = _idx()
    assert idx.locate(sub_start=4.7) == 1  # inside cue 2
    assert idx.locate(sub_start=8.0) == -1  # in a gap → no exact timing match


def test_locate_by_text_prefers_nearest_to_hint():
    idx = CueIndex(
        parse_srt("1\n00:00:01,000 --> 00:00:02,000\n…\n\n2\n00:00:05,000 --> 00:00:06,000\n…\n")
    )
    # both cues share the text "…"; the preferred hint disambiguates
    assert idx.locate(text="…", preferred=1) == 1
    assert idx.locate(text="…", preferred=0) == 0


def test_locate_by_text_disambiguates_duplicate_lines_by_timing_then_first():
    """A repeated line must resolve to the RIGHT occurrence. `_disambiguate_text_matches` has three
    tiers — the `preferred` (last-jump) hint, then the cue whose ``[start, end)`` window holds
    ``sub_start``, then the first match — but only the hint tier was asserted, so a wrong-occurrence
    bug in the timing/fallback tiers (jump to the other copy of a repeated subtitle) slid past. "同じ"
    repeats with a distinct cue between the copies, exercising all three tiers."""
    idx = CueIndex(
        [
            Cue(1.0, 2.0, "同じ"),  # 0
            Cue(3.0, 4.0, "ちがう"),  # 1 — distinct, never a match
            Cue(5.0, 6.0, "同じ"),  # 2
        ]
    )
    dup = "同じ"  # matches cues 0 and 2
    # tier 1 — the hint wins even when sub_start points at the other occurrence
    assert idx.locate(text=dup, preferred=0, sub_start=5.5) == 0
    assert idx.locate(text=dup, preferred=2, sub_start=1.5) == 2
    # tier 2 — no hint (-1): the occurrence whose window contains sub_start (lower bound inclusive)
    assert idx.locate(text=dup, preferred=-1, sub_start=5.5) == 2
    assert idx.locate(text=dup, preferred=-1, sub_start=5.0) == 2
    # tier 3 — no hint, sub_start in no window → first match (upper bound exclusive; None → first)
    assert idx.locate(text=dup, preferred=-1, sub_start=6.0) == 0
    assert idx.locate(text=dup, preferred=-1, sub_start=None) == 0


def test_locate_by_time_pos_active_or_upcoming():
    idx = _idx()
    assert idx.locate(time_pos=2.0) == 0  # inside cue 1
    assert idx.locate(time_pos=8.5) == 2  # in a gap → the upcoming cue 3
    assert idx.locate(time_pos=99.0) == -1  # past the end → nothing upcoming


def test_locate_text_beats_stale_time():
    # chaining case: we just rendered cue 3's text, but time-pos is still stale at cue-1 territory
    idx = _idx()
    assert idx.locate(text="また\nあした", time_pos=2.0, preferred=2) == 2


def test_visibility_boundaries_are_unique_sorted_and_strictly_future():
    idx = CueIndex([Cue(1.0, 5.0, "a"), Cue(3.0, 7.0, "b"), Cue(7.0, 8.0, "c")])

    assert tuple(idx.boundaries_after(3.0)) == (5.0, 7.0, 8.0)


def _overlapping() -> CueIndex:
    """A sign held over a whole scene, with two dialogue cues inside it — ordinary authored ASS."""
    return CueIndex([Cue(0.0, 10.0, "看板"), Cue(2.0, 4.0, "猫を見る"), Cue(3.0, 6.0, "犬も見る")])


@pytest.mark.parametrize(
    ("timestamp", "position", "overlapping"),
    [
        (1.0, 0, 0),  # only the sign is up
        (2.5, 1, 1),  # the sign plus the first line — the line is what the viewer just saw
        (3.5, 2, 2),  # both lines and the sign; the newest line wins
        (5.0, 2, 1),  # the first line has ended
        (7.0, 0, 0),  # back to the sign alone
    ],
)
def test_the_current_cue_in_an_overlap_is_the_most_recently_revealed_line(
    timestamp: float, position: int, overlapping: int
) -> None:
    """The cue list is sorted by start, so "the first active cue" means "on screen longest". A sign
    spanning a scene then answers for every moment inside it and navigation steps relative to the
    sign rather than the dialogue being read."""
    active = _overlapping().active_at(timestamp)

    assert (active.position, active.overlapping) == (position, overlapping)


def test_an_overlap_resolves_the_same_way_from_either_clock() -> None:
    """`sub_start` and `time_pos` are two clocks for one question, and they used to disagree: one
    scanned for an active cue, the other for the first cue still to end. Only the second is reached
    in a gap, which is where they legitimately differ."""
    index = _overlapping()

    for timestamp in (1.0, 2.5, 3.5, 5.0, 7.0):
        assert index.locate(sub_start=timestamp) == index.locate(time_pos=timestamp)


def test_navigating_out_of_an_overlap_steps_from_the_line_being_read() -> None:
    index = _overlapping()

    current = index.locate(sub_start=2.5)

    assert index.cues[current].text == "猫を見る"
    assert index.cues[index.target(current, 1)].text == "犬も見る"  # not back to the sign
    assert index.cues[index.target(current, -1)].text == "看板"


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
