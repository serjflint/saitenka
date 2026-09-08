"""The application sink must accept every metric the subtitle core records.

This is a contract with an enforcing end and an enumerable end, and nothing bound them. The sink
RAISES on a name it does not know — deliberately, so a typo cannot vanish — and the backend's caller
catches `Exception` and reports `provider-error`. So a metric added on the library side and not
taught to the sink does not degrade telemetry: it fails the geometry render, every render, and a
live session has no hit boxes, no color and no scanning at all.

That shipped. Four timing calls were added inside `extract_token_geometry` and the whole feature went
dark, while `poe all` stayed green — because `NullTelemetry` and every test collector accept any
name, so neither end of the suite ever exercised the pair together.
"""

from __future__ import annotations

import pytest
from saitenka_subtitles.telemetry import GEOMETRY_METRICS

from saitenka import otel_metrics


@pytest.mark.parametrize("metric", sorted(GEOMETRY_METRICS))
def test_the_production_sink_accepts_every_metric_the_core_records(metric: str) -> None:
    """Unregistered instruments are `None` here, which is the shipped state until telemetry is
    configured — and the name check runs before the null check, so this covers the real path."""
    otel_metrics.geometry_telemetry.record(metric, 1.0)


def test_an_unknown_metric_is_still_refused() -> None:
    """The negative control. The map is not permissive — it rejects, which is what makes the test
    above load-bearing rather than decorative."""
    with pytest.raises(ValueError, match="unknown geometry metric"):
        otel_metrics.geometry_telemetry.record("extract_nonexistent_ms", 1.0)


def test_the_sink_records_into_a_registered_instrument(monkeypatch) -> None:
    """And the accepted name reaches a histogram rather than being swallowed by the null check."""
    recorded: list[float] = []
    monkeypatch.setattr(
        otel_metrics,
        "subtitle_geometry_extract_collect_ms",
        type("H", (), {"record": lambda _s, v: recorded.append(v)})(),
    )

    otel_metrics.geometry_telemetry.record("extract_collect_ms", 4.25)

    assert recorded == [4.25]
