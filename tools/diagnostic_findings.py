"""Conservative diagnoses from sanitized producer evidence, including tracing-off reports."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from saitenka.app.query_evidence import safe_snapshot
from saitenka.app.report_reader import read_member

if TYPE_CHECKING:
    from pathlib import Path

QUERY_FAULTS = frozenset(
    {
        "timeout",
        "disconnected",
        "stale-epoch",
        "property-not-found",
        "property-unavailable",
        "malformed",
        "exception",
        "error",
        "success-missing-data",
    }
)
INGRESS_FAULTS = frozenset(
    {
        "mailbox-full",
        "candidate-full",
        "stale-epoch",
        "projection-stale-epoch",
        "projection-exception",
        "closed",
        "projection-closed",
    }
)


def read_envelope(source: Path) -> dict:
    try:
        text = read_member(source, "diagnostics/envelope.json", limit=128 * 1024)
    except (OSError, ValueError):
        return {"status": "invalid", "next_evidence": "readable producer metadata envelope"}
    if text is None:
        return {"status": "missing", "next_evidence": "producer metadata envelope"}
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        return {"status": "invalid", "next_evidence": "readable producer metadata envelope"}
    if not isinstance(raw, dict) or type(raw.get("schema")) is not int or raw["schema"] != 1:
        return {"status": "unsupported-schema", "next_evidence": "compatible report reader"}
    return raw


def diagnose(envelope: dict) -> dict:
    queries = safe_snapshot(envelope.get("player_query_health"))
    findings = []
    health = envelope.get("operation_health")
    if isinstance(health, dict):
        by_operation = health.get("by_operation")
        if isinstance(by_operation, dict):
            for operation in (
                "anki_request",
                "mpv_effect",
                "runtime_job",
                "runtime_mpv",
                "surface_write",
                "tooltip_quality_submission",
                "subtitle_calibration",
            ):
                counts = by_operation.get(operation)
                if not isinstance(counts, dict):
                    continue
                for outcome in (
                    "failed",
                    "unavailable",
                    "timeout",
                    "disconnected",
                    "shutdown-aborted",
                    "pending",
                    "other",
                ):
                    count = counts.get(outcome)
                    if type(count) is int and 0 < count <= 2**63 - 1:
                        findings.append(
                            {
                                "category": "operation-" + outcome,
                                "severity": "warning",
                                "evidence": {"operation": operation, "count": count},
                                "cause": "recorded boundary outcome; root cause unknown",
                                "next_evidence": "correlated terminal or dependency health; pending is not proof of a crash",
                            }
                        )
    for owner in queries.get("owners", []):
        identity = {"owner": owner["owner"], "connection_epoch": owner["connection_epoch"]}
        for command in owner["commands"]:
            outcome = command["outcome"]
            if outcome in QUERY_FAULTS:
                findings.append(
                    {
                        "category": "player-query-" + outcome,
                        "severity": "warning",
                        "evidence": {
                            **identity,
                            "sequence": command["sequence"],
                            "property": command["property"],
                            "verb": command["verb"],
                        },
                        "cause": "unknown beyond the recorded reply outcome",
                        "next_evidence": "subsequent query and projection in the same connection epoch",
                    }
                )
        ingress = owner["ingress"]
        for outcome in sorted(INGRESS_FAULTS):
            count = ingress.get("counts", {}).get(outcome)
            if isinstance(count, int) and count > 0:
                findings.append(
                    {
                        "category": "player-ingress-" + outcome,
                        "severity": "warning",
                        "evidence": {"owner": owner["owner"], "count": count},
                        "cause": "recorded stage outcome; not a diagnosis of displayed pixels",
                        "next_evidence": "retained event identity and downstream terminal",
                    }
                )
    return {
        "scope": "recorded operation, player query and ingress outcomes; not a whole-product diagnosis",
        "status": "fault-observed" if findings else "unidentified",
        "query_evidence_status": queries["status"],
        "findings": findings,
        "causal_completeness": projection_census(queries),
        "oracle_execution": {
            "status": "unknown",
            "next_evidence": "executed mask oracle and controls",
        },
        "fidelity": {"status": "unknown", "next_evidence": "independent pixel comparison"},
        "cost": {"status": "unknown", "next_evidence": "matched-work benchmark"},
    }


def projection_census(queries: dict) -> dict:
    admitted = completed = evicted = 0
    for owner in queries.get("owners", []):
        ingress = owner["ingress"]
        rows = ingress.get("recent", [])
        evicted += ingress.get("evicted", 0) or 0
        terminals = {
            (row["connection_epoch"], row["mailbox_sequence"])
            for row in rows
            if row["source"] == "projection-mailbox"
        }
        for row in rows:
            if row["outcome"] == "queued":
                admitted += 1
                completed += (row["connection_epoch"], row["mailbox_sequence"]) in terminals
    return {
        "scope": "retained mailbox admissions to reducer outcomes; not applied pixels",
        "status": "partial" if queries.get("owners") else "unknown",
        "admitted_retained": admitted,
        "joined_reducer_outcomes": completed,
        "without_retained_terminal": admitted - completed,
        "retention_loss": evicted,
        "owners_evicted": queries.get("owners_evicted"),
        "missing_terminal_meaning": "in-flight or lost evidence; never implicit success or crash",
    }
