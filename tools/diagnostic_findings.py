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
    text = read_member(source, "diagnostics/envelope.json", limit=128 * 1024)
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
        "scope": "recorded player query and ingress faults; not a whole-product diagnosis",
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
