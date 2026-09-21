"""Conservative diagnoses from sanitized producer evidence, including older report formats."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from saitenka.app.query_evidence import safe_snapshot
from saitenka.app.render_evidence import safe_runtime_configuration
from saitenka.app.report_reader import read_member, trace_evidence
from saitenka.app.report_schema import count

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


def diagnose_report(source: Path, *, trace: dict | None = None) -> dict:
    result = diagnose(read_envelope(source) if source.suffix != ".json" else {})
    capture = trace if trace is not None else trace_evidence(source)
    collection = _metadata_member(source, "telemetry/collection.json")
    candidates = collection.get("candidates", [])
    rejected = (
        sum(
            isinstance(row, dict)
            and isinstance(row.get("status"), str)
            and row["status"] in {"too-large", "invalid", "unreadable-or-concurrent-write"}
            for row in candidates
        )
        if isinstance(candidates, list)
        else 0
    )
    health = _metadata_member(source, "telemetry/health.json")
    losses = {
        key: count(health.get(key))
        for key in (
            "lost_events",
            "queue_dropped",
            "write_failures",
            "history_losses",
            "sample_failures",
        )
    }
    if capture.get("status") in {"partial", "invalid"} or rejected or any(losses.values()):
        result["findings"].append(
            {
                "category": "trace-incomplete",
                "severity": "warning",
                "evidence": {
                    "status": capture["status"],
                    "rejected_candidates": rejected,
                    **losses,
                    "scope": "retained capture accounting; not proof of current-session product failure",
                },
                "cause": "capture is incomplete or has rejected candidates; product failure not established",
                "next_evidence": "compatible complete trace or same-session operation summary",
            }
        )
    if source.suffix != ".json":
        try:
            fault = read_member(source, "crashes/faulthandler.log", limit=1024 * 1024)
        except (OSError, ValueError):
            fault = None
        if fault is not None and "Fatal Python error:" in fault:
            result["findings"].append(
                {
                    "category": "historical-native-fault",
                    "severity": "warning",
                    "evidence": {"scope": "historical", "session": "unknown"},
                    "cause": "native fault text retained; attribution to the current session unknown",
                    "next_evidence": "emitting process/session identity and symbolized native stack",
                }
            )
    result["status"] = "fault-observed" if result["findings"] else "unidentified"
    return result


def _metadata_member(source: Path, name: str) -> dict:
    if source.suffix == ".json":
        return {}
    try:
        raw = read_member(source, name, limit=128 * 1024)
        document = json.loads(raw) if raw is not None else None
    except (OSError, ValueError, RecursionError):
        return {}
    return document if isinstance(document, dict) else {}


def diagnose(envelope: dict) -> dict:
    queries = safe_snapshot(envelope.get("player_query_health"))
    geometry = safe_runtime_configuration(envelope.get("effective_runtime_configuration"))
    findings = geometry_findings(envelope)
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
                "subtitle_device_upload",
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
        "geometry_owner_count": sum(
            type(owner.get("owner")) is int and bool(owner.get("configurations"))
            for owner in geometry.get("owners", [])
        ),
        "findings": findings,
        "causal_completeness": projection_census(queries),
        "oracle_execution": {
            "status": "unknown",
            "next_evidence": "executed mask oracle and controls",
        },
        "fidelity": {"status": "unknown", "next_evidence": "independent pixel comparison"},
        "cost": {"status": "unknown", "next_evidence": "matched-work benchmark"},
    }


def evaluate_fault(diagnosis: dict, case: dict) -> dict:
    findings = diagnosis["findings"]
    evidence = {
        "geometry-history": diagnosis.get("geometry_owner_count", 0) > 0,
        "player-query": diagnosis.get("query_evidence_status") == "partial",
    }.get(case.get("required_evidence"), False)
    checks = {
        "required_evidence": evidence,
        "categories": {row["category"] for row in findings} == set(case["expected_categories"]),
        "severity": all(row["severity"] == case["severity"] for row in findings),
        "next_evidence": all(row["next_evidence"] == case["next_evidence"] for row in findings),
        "uncertainty": case["uncertainty"] == "displayed-fidelity-unknown"
        and diagnosis["fidelity"]["status"] == "unknown",
    }
    return {
        "case": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "scope": "caller-declared injected fault; matching a report does not prove injection",
    }


def geometry_findings(envelope: dict) -> list[dict]:
    configuration = safe_runtime_configuration(envelope.get("effective_runtime_configuration"))
    findings = []
    for owner in configuration.get("owners", []):
        publication = owner.get("published", {})
        if publication.get("status") != "retained":
            continue
        validation = publication.get("validation", {})
        counts = dict(validation.get("verdicts", {}))
        counts["coverage-evicted"] = validation.get("evicted_mask_tokens")
        for reason in (
            "probe-error",
            "probe-budget-exceeded",
            "exact-mask-mismatch",
            "coverage-evicted",
        ):
            count = counts.get(reason)
            if type(count) is int and count > 0:
                findings.append(
                    {
                        "category": "geometry-" + reason,
                        "severity": "warning",
                        "evidence": {
                            "owner": owner["owner"],
                            "revision": publication["revision"],
                            "generation": publication["generation"],
                            "tokens": count,
                        },
                        "cause": "recorded same-renderer eligibility or retention outcome; displayed fidelity unknown",
                        "next_evidence": "final device/upload outcome and independent pixel comparison",
                    }
                )
    return findings


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
