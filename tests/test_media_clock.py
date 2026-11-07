"""Subtitle-to-media clock bounds preserve the playable intersection."""

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from saitenka.app.media import media_span


@given(
    start=st.integers(-10000, 10000), length=st.integers(1, 1000), delay=st.integers(-10000, 10000)
)
@example(start=10, length=2, delay=-11)
@example(start=10, length=2, delay=-12)
@example(start=12, length=2, delay=0)
def test_media_span_is_the_delayed_playable_intersection(start, length, delay):
    end = start + length
    if end + delay <= 0:
        with pytest.raises(ValueError, match="outside the media timeline"):
            media_span(start, end, delay)
    else:
        span = media_span(start, end, delay)
        assert (span.start, span.end) == (max(0.0, start + delay), end + delay)


@pytest.mark.parametrize(
    ("start", "end", "delay"),
    [
        (10, 10, 0),
        (12, 10, 0),
        (float("nan"), 12, 0),
        (10, float("inf"), 0),
        (10, 12, float("nan")),
        (1e308, 1.1e308, 1e308),
    ],
)
def test_media_span_rejects_invalid_or_overflowing_bounds(start, end, delay):
    with pytest.raises(ValueError):
        media_span(start, end, delay)
