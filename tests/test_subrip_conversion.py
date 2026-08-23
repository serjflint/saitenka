"""Predicting the ASS event mpv builds for a SubRip cue.

The oracle is a live mpv, recorded into `tests/fixtures/subrip_rows.json` by
`tools/subrip_oracle.py`. Nothing here is hand-written: mpv links libavcodec's `srtdec` and then
serialises the row itself, so it is the only thing that can say what the row is.

Two claims, and the second is the one that keeps this honest. Where the converter answers, it must
agree exactly. Where `srtdec` does something no one would design — a stray `<` parsed as a tag, a
second `{\\an}` dropped, an unknown tag deleted, a named colour resolved from a table — it must
DECLINE rather than guess, because a declined cue costs a cache miss and a guessed one costs a
render.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from saitenka.subtitles import subrip
from saitenka.subtitles.model import Cue

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "subrip_rows.json").read_text(encoding="utf-8")
)
CASES = {case["name"]: case for case in FIXTURE["cases"]}

#: Cases the converter must decline. Named here rather than derived from a mismatch, so that a
#: converter which quietly stopped predicting everything would fail this file rather than pass it.
DECLINED = frozenset(
    {"stray-angle", "unknown-tag", "font-named-colour", "font-size", "trailing-an"}
)


def row_of(case: dict) -> tuple[str, Cue]:
    """The recorded row, and a cue carrying its own timings — so what is compared is the text."""
    fields = case["row"].split(",", 9)
    return case["row"], Cue(_seconds(fields[1]), _seconds(fields[2]), "")


def _seconds(stamp: str) -> float:
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


@pytest.mark.parametrize("name", sorted(set(CASES) - DECLINED))
def test_the_predicted_row_is_the_row_mpv_reports(name: str) -> None:
    """Exactly, after canonicalisation — which is the comparison the geometry cache key makes, and
    therefore the only one that decides whether a prefetched cue is used."""
    from saitenka.subtitles.ass_geometry import canonical_active_ass_rows

    case = CASES[name]
    recorded, cue = row_of(case)

    predicted = subrip.dialogue_row(cue, case["markup"])

    assert predicted is not None, "a case outside DECLINED was declined"
    assert canonical_active_ass_rows(predicted) == canonical_active_ass_rows(recorded)


@pytest.mark.parametrize("name", sorted(DECLINED))
def test_a_cue_srtdec_would_mangle_is_declined_rather_than_guessed(name: str) -> None:
    """Each of these is `srtdec` doing something surprising. Predicting one wrong is not a wrong
    box — the cache key would not match and the cue would be rebuilt — but it is a wasted render,
    and the point of predicting at all is to save one."""
    case = CASES[name]
    _recorded, cue = row_of(case)

    assert subrip.dialogue_row(cue, case["markup"]) is None


def test_the_declined_set_is_not_the_whole_corpus() -> None:
    """A converter that declined everything would satisfy both tests above one at a time."""
    assert len(DECLINED) < len(CASES) / 2


def test_centiseconds_are_truncated_the_way_mpv_truncates_them() -> None:
    """ffmpeg's muxer rounds and mpv truncates, and the row this has to match is mpv's — a rounded
    stamp differs from mpv's on more than a third of arbitrary timings."""
    row = subrip.dialogue_row(Cue(1.345, 3.456, ""), "x")

    assert row is not None
    assert "0:00:01.34,0:00:03.45" in row


@given(milliseconds=st.integers(min_value=0, max_value=6 * 3_600_000))
@example(milliseconds=2_010)  # 2.010 is 2.00999… in binary; truncating reads it back as 2009ms
@example(milliseconds=8_110)
def test_a_whole_millisecond_survives_the_trip_through_float_seconds(milliseconds: int) -> None:
    """The stamp mpv prints comes from an integer millisecond count; ours arrives as float seconds.

    Truncating that product loses a centisecond wherever the binary float lands just under the
    integer — 148 of 20 000 timings at 10ms steps — and each one is a row that matches nothing, so
    the cue it was prefetched for is silently rebuilt from cold.
    """
    row = subrip.dialogue_row(Cue(milliseconds / 1_000, 9.0, ""), "x")

    assert row is not None
    stamp = row.split(",")[1]
    hours, minutes, rest = stamp.split(":")
    printed = (int(hours) * 3_600_000) + (int(minutes) * 60_000) + round(float(rest) * 1_000)
    assert printed == milliseconds - milliseconds % 10


def test_markup_survives_the_walk_from_file_to_cue() -> None:
    """The cue index carries plain text — it has already lost what the prediction reproduces — so
    the markup has to come back off the file, matched to the index by timing rather than by order.
    """
    content = (
        "1\n00:00:01,000 --> 00:00:03,000\nHello <i>world</i>\n\n"
        "2\n00:00:04,000 --> 00:00:05,500\n<b>Bold</b>\nsecond\n"
    )

    found = subrip.markup_by_cue(content)

    assert found == {
        (1_000, 3_000): "Hello <i>world</i>",
        (4_000, 5_500): "<b>Bold</b>\nsecond",
    }


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not a subtitle file at all",
        "1\n00:00:01,000 --> 00:00:03,000\n\n",  # an empty cue is not a cue
        "1\n00:00:01.000 --> 00:00:03.000\ndotted\n",  # some writers use dots
    ],
)
def test_a_file_with_nothing_to_predict_yields_no_prediction(content: str) -> None:
    found = subrip.markup_by_cue(content)

    assert found == ({(1_000, 3_000): "dotted"} if "dotted" in content else {})
