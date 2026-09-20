"""Bounded report projection of the enabled trace stream."""

import json

import pytest

from saitenka.app.diagnostic_summary import DiagnosticSummary


@pytest.mark.usefixtures("enabled_telemetry")
def test_geometry_publication_never_transports_retained_history(monkeypatch):
    from util import record_spans

    from saitenka.app.render_evidence import GeometryEvidence
    from saitenka.app.telemetry import save_operation_summary

    spans = record_spans(monkeypatch)
    evidence = GeometryEvidence()
    for _ in range(100):
        evidence.geometry_source({"selected_source": "shadow", "reason": "configured-shadow"})
        save_operation_summary()

    records = [
        json.loads(row["attrs"]["record"]) for row in spans if row["name"] == "diagnostic_record"
    ]

    assert len(records) == 100
    for record in records:
        record.pop("captured_ns")
    assert all(record == records[0] for record in records)
    assert records[0]["selected_source"] == "shadow"
    assert "history" not in records[0]


def test_operation_census_overflow_preserves_denominator():
    summary = DiagnosticSummary()
    for index in range(1000):
        summary.consume(
            {
                "name": f"operation-{index}",
                "args": {"timing": "operation-lifetime", "outcome": f"outcome-{index}"},
            }
        )

    result = summary.report({})

    assert result["pending"] == {}
    assert len(result["outcomes"]) <= 257
    assert sum(row["count"] for row in result["outcomes"]) == 1000


def test_report_snapshot_is_detached_and_owner_retention_is_bounded():
    summary = DiagnosticSummary()
    for owner in range(10):
        summary.consume(
            {
                "name": "diagnostic_record",
                "args": {
                    "kind": "profiles",
                    "owner": owner,
                    "action": "replace",
                    "record": json.dumps({"owner": owner, "revision": owner}),
                },
            }
        )

    snapshot = summary.snapshot("profiles")
    snapshot["owners"][0]["revision"] = -1
    result = summary.snapshot("profiles")

    assert result["owners_evicted"] == 6
    assert [row["revision"] for row in result["owners"]] == [6, 7, 8, 9]
