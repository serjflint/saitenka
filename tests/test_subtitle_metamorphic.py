"""Input-equivalence metamorphic oracles for the subtitle parser."""

from __future__ import annotations

from saitenka.subtitles import parse_srt

_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,500\n"
    "Hello <i>world</i>\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "Second line\n"
    "テスト"
)


def test_parse_srt_is_invariant_to_line_endings():
    # CRLF vs LF must not change the cues — splitlines() normalizes both.
    assert parse_srt(_SRT.replace("\n", "\r\n")) == parse_srt(_SRT)


def test_parse_srt_is_invariant_to_trailing_blank_lines():
    # trailing blank lines are not a cue block — appending them is a no-op.
    assert parse_srt(_SRT + "\n\n\n") == parse_srt(_SRT)


def test_the_input_equivalence_oracle_has_teeth():
    # negative control: a transform that DOES change meaning (a shifted start time) must diverge, proving
    # the equality above isn't a tautology that stays green on a broken parser.
    assert parse_srt(_SRT.replace("00:00:01", "00:00:09")) != parse_srt(_SRT)
