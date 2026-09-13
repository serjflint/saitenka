"""Tracing-disabled diagnostic outcomes remain bounded and exactly once."""

from saitenka import operation_summary, otel_metrics
from saitenka.operation_summary import OperationSummary


def test_unavailable_tracing_keeps_terminal_outcome_without_importing_sdk(monkeypatch):
    summary = OperationSummary()
    monkeypatch.setattr(operation_summary, "operations", summary)
    monkeypatch.setattr(otel_metrics, "_trace_available", False)
    operation = otel_metrics.DeferredSpan("runtime_job", owner="subtitle")
    assert summary.snapshot()["pending"] == {"runtime_job": 1}

    operation.finish(outcome="not-admitted")
    operation.finish(outcome="succeeded")

    result = summary.snapshot()
    assert result["pending"] == {}
    assert result["outcomes"] == [
        {"operation": "runtime_job", "outcome": "not-admitted", "count": 1}
    ]


def test_operation_census_overflow_preserves_denominator():
    summary = OperationSummary()
    for index in range(1000):
        key = summary.start(f"operation-{index}")
        summary.finish(key, f"outcome-{index}")

    result = summary.snapshot()
    assert result["pending"] == {}
    assert len(result["outcomes"]) <= 257
    assert sum(row["count"] for row in result["outcomes"]) == 1000
