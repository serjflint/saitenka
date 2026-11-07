"""The subtitle cue index that powers instant Alt+←/→/↓ navigation."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from saitenka_subtitles import Cue, CueIndex, parse_ass, parse_cues, parse_srt

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


#: Two speakers over each other, authored as two events with identical timing, plus a sign held
#: across both — the partial overlap the format allows and this corpus happens not to contain.
_OVERLAP = [
    Cue(1.0, 3.0, "（児童）雨　すごいね。"),
    Cue(1.0, 3.0, "（児童）ねえ。"),
    Cue(0.5, 5.0, "（看板）雨天決行"),
    Cue(6.0, 8.0, "ひとり。"),
]


def test_a_frame_is_every_cue_on_screen_at_the_instant_its_cue_appears():
    """The renderer asks the document at ``round(start * 1000) + 1`` ms and draws whatever is
    active there. A frame computed at any other instant is a set the document disagrees with, which
    is the disagreement the whole split exists to remove."""
    idx = CueIndex(list(_OVERLAP))
    positions = {cue.text: index for index, cue in enumerate(idx.cues)}
    sign = positions["（看板）雨天決行"]
    first, second = positions["（児童）雨　すごいね。"], positions["（児童）ねえ。"]

    assert idx.frame_at_position(first) == idx.frame_at_position(second) == (sign, first, second)
    assert idx.frame_at_position(sign) == (sign,)  # at 0.501 the dialogue has not started
    assert idx.frame_at_position(positions["ひとり。"]) == (positions["ひとり。"],)


def test_a_frames_text_joins_in_document_order():
    """The renderer joins the document's own event order, so this has to as well — the two texts
    are compared for equality, and a different order is a mismatch nobody moved a line to cause."""
    idx = CueIndex(list(_OVERLAP))

    assert idx.frame_text(idx.cues.index(Cue(1.0, 3.0, "（児童）ねえ。"))) == (
        "（看板）雨天決行\n（児童）雨　すごいね。\n（児童）ねえ。"
    )


def test_a_cue_outside_any_overlap_is_its_own_frame():
    """The negative control: nothing about a lone cue changes, so a frame-aware draw is identical
    to the single-cue draw it replaces."""
    idx = CueIndex(list(_OVERLAP))
    lone = idx.cues.index(Cue(6.0, 8.0, "ひとり。"))

    assert idx.frame_text(lone) == "ひとり。"


def test_a_drawn_frame_locates_back_to_the_cue_it_was_drawn_for():
    """Chaining. After a navigation the line on screen is the frame, so `locate` is asked about
    text no single cue has; without the frame in the text map, next/next/next stops dead the moment
    it steps into an overlap — and the timings it would fall back on are stale mid-seek."""
    idx = CueIndex(list(_OVERLAP))
    second = idx.cues.index(Cue(1.0, 3.0, "（児童）ねえ。"))
    # Spelled out, not `frame_text(second)`: sourcing both sides from the same call asserts that a
    # function agrees with itself, which it does even when it returns the single cue this is about.
    drawn = "（看板）雨天決行\n（児童）雨　すごいね。\n（児童）ねえ。"

    assert idx.locate(text=drawn, preferred=second) == second


CORPUS = json.loads((Path(__file__).parent / "fixtures/ass_collapse/cases.json").read_text())[
    "cases"
]


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["name"])
def test_locked_ass_study_cue_count(case):
    assert len(parse_cues(case["ass"], "episode.ass")) == case["expected_count"]


def test_duplicate_overlap_keeps_authored_frame_text_and_event_splits():
    content = CORPUS[0]["ass"] + "\nDialogue: 1,0:00:02.00,0:00:03.00,Default,sign,0,0,0,,看板"
    index = CueIndex.from_content(content, "episode.ass")

    assert len(index) == 2
    assert index.frame_text(1) == "重なった行\n重なった行\n看板"
    assert index.frame_events("重なった行\n重なった行\n看板") == (
        "重なった行",
        "重なった行",
        "看板",
    )
    assert index.locate(text="重なった行\n重なった行\n看板", preferred=1) == 1


@pytest.mark.parametrize(
    ("field", "value"), [(3, "Other"), (4, "Bob"), (0, "1"), (5, "12"), (8, "effect")]
)
def test_exact_equality_retains_distinct_authored_metadata(field, value):
    content = CORPUS[0]["ass"]
    lines = content.splitlines()
    row = lines[-1].removeprefix("Dialogue: ").split(",", 9)
    row[field] = value
    lines[-1] = "Dialogue: " + ",".join(row)
    assert len(parse_ass("\n".join(lines))) == 2


@given(st.lists(st.integers(min_value=1, max_value=20), min_size=1, max_size=12))
def test_ordinary_repeated_dialogue_retains_every_interval(durations):
    rows = []
    start = 0
    for duration in durations:
        end = start + duration
        rows.append(
            f"Dialogue: 0,0:00:{start // 100:02}.{start % 100:02},0:00:{end // 100:02}.{end % 100:02},Default,,0,0,0,,えっ"
        )
        start = end
    content = "\n".join(CORPUS[0]["ass"].splitlines()[:2] + rows)
    assert parse_ass(content) == parse_ass(content, collapse=False)


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["name"])
def test_every_authored_frame_remains_available_after_study_collapse(case):
    index = CueIndex.from_content(case["ass"], "episode.ass")
    authored = CueIndex(sorted(parse_ass(case["ass"], collapse=False), key=lambda cue: cue.start))
    for position, cue in enumerate(authored.cues):
        frame = authored.frame_text(position)
        assert index.frame_events(frame) == authored.frame_events(frame)
        logical = index.locate(text=frame, sub_start=cue.start)
        assert index.cues[logical].start <= cue.start < index.cues[logical].end
    assert tuple(index.boundaries_after(-1)) == tuple(authored.boundaries_after(-1))


def test_retiming_retains_a_boundary_inside_a_collapsed_burst(tmp_path):
    from saitenka.app.resync import _alass_ready_source, _parse_cues, _retimed_document

    source = tmp_path / "episode.srt"
    source.write_text(CORPUS[1]["ass"], encoding="utf-8")
    authored = _parse_cues(source)
    ready = _alass_ready_source(source, tmp_path)
    assert len(_parse_cues(ready)) == 3
    shifted = _retimed_document(source, authored, delta=1.0, boundary=1.05, workdir=tmp_path)
    assert [(cue.start, cue.end) for cue in _parse_cues(shifted)] == [
        (1.0, 1.05),
        (2.05, 2.09),
        (2.09, 4.55),
    ]


def test_timed_osd_uses_later_authored_frame_span():
    from saitenka.app.timed_osd import timed_cue

    index = CueIndex.from_content(CORPUS[1]["ass"], "episode.ass")
    cue = timed_cue(index, 1060, 0)
    assert cue is not None
    assert (cue.start_ms, cue.end_ms) == (1050, 1090)


def test_study_preprocessing_preserves_raw_offsets_in_duplicate_overlap():
    from saitenka_tokenize.registry import UnidicTokenizer

    from saitenka.app.features.annotation.jobs import AnnotationInputs, annotate
    from saitenka.app.native_subtitles import token_annotations
    from saitenka.app.study_text import StudyRules
    from saitenka.app.token_cache import event_lines

    content = (
        CORPUS[0]["ass"].replace("重なった行", "太郎：本を読む。")
        + "\nDialogue: 1,0:00:02.00,0:00:03.00,Default,sign,0,0,0,,［看板］猫"
    )
    index = CueIndex.from_content(content, "episode.ass")
    raw = index.frame_text(1)
    tokenizer = UnidicTokenizer()
    cue = annotate(
        AnnotationInputs(
            raw,
            tokenizer,
            None,
            None,
            study_rules=StudyRules(speaker_labels=True, annotations=True),
        )
    )
    selection = token_annotations(raw, cue.lines, cue.tokens, tokenizer.is_skippable)
    assert event_lines(raw, index) == (1, 1, 1)
    assert [raw[item.text_start : item.text_end] for item in selection.annotations] == [
        token.surface for token in cue.tokens if not tokenizer.is_skippable(token)
    ]
    assert all("太郎" not in token.surface and "看板" not in token.surface for token in cue.tokens)


@pytest.mark.parametrize("duration", [100, 110])
def test_animation_collapse_requires_short_preceding_frames(duration):
    from saitenka_subtitles import Cue

    def timestamp(ms):
        return f"0:00:{ms // 1000:02}.{ms % 1000 // 10:02}"

    content = "\n".join(
        CORPUS[0]["ass"].splitlines()[:2]
        + [
            f"Dialogue: 0,{timestamp(1000 + i * duration)},{timestamp(1000 + (i + 1) * duration)},Default,,0,0,0,,{{\\clip(m {i} {i})}}歌詞"
            for i in range(3)
        ]
    )
    expected = (
        [Cue(1, (1000 + 3 * duration) / 1000, "歌詞")]
        if duration == 100
        else parse_ass(content, collapse=False)
    )
    assert parse_ass(content) == expected


def test_two_animation_frames_do_not_prove_a_burst():
    content = "\n".join(CORPUS[1]["ass"].splitlines()[:4])
    assert len(parse_ass(content)) == 2


@pytest.mark.parametrize(
    ("field", "value"), [(3, "Other"), (4, "Bob"), (0, "1"), (5, "12"), (8, "effect")]
)
def test_animation_collapse_retains_distinct_authored_metadata(field, value):
    lines = CORPUS[1]["ass"].splitlines()
    row = lines[-1].removeprefix("Dialogue: ").split(",", 9)
    row[field] = value
    lines[-1] = "Dialogue: " + ",".join(row)
    assert len(parse_ass("\n".join(lines))) == 3


def test_mislabeled_ass_preserves_the_authored_view():
    index = CueIndex.from_content(CORPUS[1]["ass"], "episode.srt")
    assert len(index) == 1
    assert len(index.authored) == 3


def test_literal_animation_command_outside_an_override_is_ordinary_dialogue():
    content = CORPUS[6]["ass"].replace(",Karaoke,歌詞", ",,歌詞\\t(")
    assert len(parse_ass(content)) == 3


@example(order=(0, 1, 2, 3, 2))
@given(st.permutations((0, 1, 2, 3, 2)))
def test_burst_overlap_and_duplicate_keep_every_authored_frame(order):
    rows = CORPUS[1]["ass"].splitlines()
    events = [*rows[2:], rows[4].replace("0:00:03.55", "0:00:01.10").replace("m 3 3", "m 4 4")]
    content = "\n".join(rows[:2] + [events[position] for position in order])
    index = CueIndex.from_content(content, "episode.ass")
    assert len(index.authored) == 5
    assert len(index) == 2
    assert tuple(index.boundaries_after(-1)) == (1.0, 1.05, 1.09, 1.1, 3.55)
    assert (
        index.frame_events("過ぎ去ってしまう瞬間を\n過ぎ去ってしまう瞬間を\n過ぎ去ってしまう瞬間を")
        == ("過ぎ去ってしまう瞬間を",) * 3
    )


@example(duration=5, authored=("注意", "scrolling-credit"))
@given(
    duration=st.integers(min_value=1, max_value=10),
    authored=st.sampled_from(
        [
            (r"{\clip(0,0,100,100)}注意", ""),
            (r"{\pos(100,100)\fad(20,20)}注意", ""),
            ("注意", "scrolling-credit"),
        ]
    ),
)
def test_rapid_static_signs_and_custom_effects_are_not_animation(duration, authored):
    raw, effect = authored

    def timestamp(cs):
        return f"0:00:{cs // 100:02}.{cs % 100:02}"

    content = "\n".join(
        CORPUS[0]["ass"].splitlines()[:2]
        + [
            f"Dialogue: 0,{timestamp(100 + i * duration)},{timestamp(100 + (i + 1) * duration)},Default,,0,0,0,{effect},{raw}"
            for i in range(3)
        ]
    )
    assert parse_ass(content) == parse_ass(content, collapse=False)
