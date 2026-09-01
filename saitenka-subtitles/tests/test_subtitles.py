"""The package on its own terms: installed alone, with no application around it.

The repo suite covers the behaviour in depth. What it cannot cover is this — that the distribution
is self-contained — because there the application is always importable and an accidental edge back
into it would resolve silently.
"""

from __future__ import annotations

import pytest
from saitenka_subtitles import Cue, CueIndex, parse_cues
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from saitenka_subtitles.null_backend import NullGeometryBackend
from saitenka_subtitles.telemetry import NullTelemetry

SRT = """1
00:00:01,000 --> 00:00:02,500
hello

2
00:00:03,000 --> 00:00:04,000
world
"""


def test_cues_parse_and_the_index_answers_what_is_on_screen() -> None:
    cues = parse_cues(SRT, "episode.srt")

    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (1.0, 2.5, "hello"),
        (3.0, 4.0, "world"),
    ]
    index = CueIndex(cues)
    assert index.active_at(2.0).position == 0
    assert not index.active_at(2.7).located


def test_a_backend_measures_nothing_until_a_host_asks_it_to() -> None:
    """The telemetry port's default. A library that had to be handed a sink to run would be one
    the application still owns, which is the edge this package was extracted to remove."""
    assert isinstance(LibassGeometryBackend()._telemetry, NullTelemetry)

    with NullTelemetry().span("anything") as span:
        span.set("key", "value")  # accepted and discarded — a no-op sink still answers the calls


def test_the_null_backend_answers_the_same_port() -> None:
    """A host without the optional libass binding still has a provider, so composition never has to
    branch on whether geometry is available."""
    assert hasattr(NullGeometryBackend(), "render")


@pytest.mark.parametrize("bad", ["", "not a subtitle file at all", "1\n\n"])
def test_parsing_malformed_input_yields_no_cues_rather_than_raising(bad: str) -> None:
    """A subtitle file is user-supplied and arbitrary, so the parser is the one place that must
    never take the session down — every format is tried and an empty list is the honest answer."""
    assert parse_cues(bad, "episode.srt") == []


def test_cue_is_orderable_by_start() -> None:
    assert min([Cue(3.0, 4.0, "b"), Cue(1.0, 2.0, "a")], key=lambda cue: cue.start).text == "a"
