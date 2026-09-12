"""Real mpv, real cue boundaries: a cue you navigate to must end up scannable.

The fixture's boundaries touch, so the successor arrives in the frame the predecessor ends — the
transition every cue-boundary defect lives in, and one a single-cue clip cannot reach.

Opt-in: ``SAITENKA_LIVE=1`` — `uv run poe smoke-live`.
"""

from __future__ import annotations

import contextlib
import os

import pytest
from live_harness import BOUNDARY_CUES, live_reader, poll_until

from saitenka import otel_metrics
from saitenka.mpvio.launch import NATIVE_GEOMETRY_MPV_MIN

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAITENKA_LIVE"),
    reason="live real-mpv test — set SAITENKA_LIVE=1; run `uv run poe smoke-live`",
)

SCANNABLE = "犬…　かな？"


def _cue_text(reader) -> str:
    return reader.graph.playback.cue.text


def _boxes(reader) -> list:
    return list(reader.graph.subtitle_presentation.cue.current.boxes)


@pytest.mark.live
@pytest.mark.timeout(30)
def test_a_cue_navigated_to_across_a_touching_boundary_becomes_scannable() -> None:
    """Deliberately not "carries them immediately" — the wait is a separate question with its own
    readout. This asks whether the words are ever clickable at all."""
    with live_reader(cues=BOUNDARY_CUES) as (_tmp, reader, _ipc):
        reader.graph.subtitle_navigation.navigate(1)  # ♬～ -> 犬…　かな？, boundaries touching

        poll_until(
            reader,
            lambda: _cue_text(reader) == SCANNABLE,
            f"navigation never landed on {SCANNABLE!r}; saw {_cue_text(reader)!r}",
        )
        poll_until(
            reader,
            lambda: bool(_boxes(reader)),
            f"{SCANNABLE!r} was on screen with no hit boxes — it is not scannable",
        )


@pytest.mark.live
@pytest.mark.timeout(30)
def test_stepping_back_onto_a_cue_leaves_it_scannable() -> None:
    """The move a viewer makes *because* a cue was not scannable. The lookahead only reads forward,
    so a backward step is the coldest cache this can reach."""
    with live_reader(cues=BOUNDARY_CUES) as (_tmp, reader, _ipc):
        reader.graph.subtitle_navigation.navigate(1)
        poll_until(reader, lambda: _cue_text(reader) == SCANNABLE, "forward step never landed")
        reader.graph.subtitle_navigation.navigate(1)
        poll_until(reader, lambda: _cue_text(reader) != SCANNABLE, "second step never left the cue")

        reader.graph.subtitle_navigation.navigate(-1)

        poll_until(
            reader,
            lambda: _cue_text(reader) == SCANNABLE,
            f"backward step never returned to {SCANNABLE!r}",
        )
        poll_until(
            reader,
            lambda: bool(_boxes(reader)),
            f"{SCANNABLE!r} was not scannable on the second visit",
        )


@pytest.mark.live
@pytest.mark.timeout(30)
def test_no_box_survives_onto_a_cue_that_has_no_such_token() -> None:
    """The negative control for the two above, which an implementation that never cleared a box
    would also satisfy.

    Asserts the pairing `TooltipController.hit` depends on: it indexes `tokens` by whatever box
    answers a click, so a box past the end is a crash or a hit region on another cue's word. Not
    "the neighbour owns no box" — `♬～` tokenizes to two tokens and measures into two boxes; being
    skippable for *lookup* is a different question.
    """
    with live_reader(cues=BOUNDARY_CUES) as (_tmp, reader, _ipc):
        reader.graph.subtitle_navigation.navigate(1)
        poll_until(reader, lambda: _cue_text(reader) == SCANNABLE, "forward step never landed")
        poll_until(reader, lambda: bool(_boxes(reader)), "the cue never became scannable")

        reader.graph.subtitle_navigation.navigate(1)  # onto the neighbour, boundaries touching

        poll_until(reader, lambda: _cue_text(reader) != SCANNABLE, "never left the scannable cue")
        poll_until(
            reader,
            lambda: _boxes_index_real_tokens(reader),
            "a box outlived its cue: it indexes a token the cue on screen does not have",
        )


def _boxes_index_real_tokens(reader) -> bool:
    cue = reader.graph.subtitle_presentation.cue.current
    return all(0 <= box.index < len(cue.tokens) for box in cue.boxes)


@contextlib.contextmanager
def _telemetry():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("live"))
    try:
        yield
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def _coloring():
    """Without a scorer every token's style is `None`, nothing is colorable, and a test about how
    long color took would pass on a cue that was never owed any."""
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    return Coloring(
        Scorer(known=KnownWords.from_set(["\u9580"]), enable_freq=False, enable_jlpt=False),
        Palette(),
    )


@pytest.mark.live
# The only test here that draws through native geometry, so the only one with that floor.
@pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)
@pytest.mark.timeout(30)
def test_the_color_latency_counter_measures_a_real_cue() -> None:
    """Liveness for the instrument, not a ceiling on what it reads.

    Everything else that times this runs against FakeIPC, where a write is acknowledged in the same
    turn it is sent — so the counter can be wired correctly there and record nothing once mpv is the
    one acknowledging, which is the only case the number exists for. No threshold is asserted: this
    produces the first real measurement of it, and a budget chosen before a baseline is a guess.
    """
    with _telemetry(), live_reader(native_visible=True, scorer=_coloring()) as (_tmp, reader, _ipc):
        poll_until(
            reader,
            lambda: "saitenka.subtitle.color_latency_ms" in otel_metrics.snapshot(),
            "a real cue colored and the color-latency timer recorded nothing",
        )
        latency = otel_metrics.snapshot()["saitenka.subtitle.color_latency_ms"]
    assert latency["count"] >= 1
    assert latency["max"] > 0.0  # a zero would mean both ends read the same instant
