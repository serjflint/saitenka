"""SubRip colors preserve the original track's text, timing and markup."""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st
from saitenka_subtitles import TokenAnnotation, parse_cues
from saitenka_subtitles.colored_srt import color_document
from saitenka_subtitles.colored_track import Verbatim
from test_colored_track_document import _per_character


def srt(body, *, ending="\n"):
    return f"1\n00:00:01,123 --> 00:00:03,987\n{body}\n".replace("\n", ending)


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("body", ["猫を見る", "猫を<i>見る</i>\n<b>犬</b>も見る", "<u>猫</u>"])
def test_srt_copy_keeps_text_timing_markup_and_line_endings(body, ending):
    source = srt(body, ending=ending)

    result = color_document(source, _per_character)

    assert result.document.colored == 1
    assert parse_cues(result.document.text, "x.srt") == parse_cues(source, "x.srt")
    assert re.sub(r'<font color="#[0-9A-F]{6}">|</font>', "", result.document.text) == source
    assert len(result.painted_rows) == 1


def test_tokens_can_cross_basic_style_tags_without_changing_them():
    source = srt("<i>見</i>る")

    result = color_document(source, lambda _: ((TokenAnnotation(0, 0, 2),), {0: 0}))

    assert '<i><font color="#000000">見</i>る</font>' in result.document.text


@pytest.mark.parametrize(
    "body", ['<font color="red">猫</font>', "{\\an8}猫", "a < b", " 猫", "猫 ", "猫\t犬"]
)
def test_unsupported_srt_markup_stays_verbatim(body):
    source = srt(body)

    result = color_document(source, _per_character)

    assert result.document.text == source
    assert result.document.verbatim == {Verbatim.REFUSED: 1}
    assert result.painted_rows == ()


def test_cotimed_cues_are_colored_individually():
    source = srt("猫") + "\n" + srt("犬").replace("1\n", "2\n", 1)

    result = color_document(source, _per_character)

    assert result.document.colored == 2
    assert len(result.painted_rows) == 2
    assert parse_cues(result.document.text, "x.srt") == parse_cues(source, "x.srt")


def test_cues_without_scored_tokens_keep_the_fallback():
    result = color_document(srt("♬"), lambda _: None)

    assert result.document.verbatim == {Verbatim.NO_TOKENS: 1}
    assert result.painted_rows == ()


@given(st.text(alphabet="猫犬を見る abc。", min_size=1).filter(lambda text: text == text.strip()))
def test_plain_srt_colors_are_insertions_only(text):
    source = srt(text)

    result = color_document(source, _per_character)

    assert result.document.colored == 1
    assert re.sub(r'<font color="#[0-9A-F]{6}">|</font>', "", result.document.text) == source
