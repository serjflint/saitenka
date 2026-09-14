"""Bounded command and ingress evidence for gateway rendering properties."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.player_evidence import OPTIONS
from saitenka.app.render_evidence import EvidenceRegistry
from saitenka.app.report_schema import count

if TYPE_CHECKING:
    from collections.abc import Callable

PROPERTIES = frozenset(f"options/{name}" for name in OPTIONS) | {
    "osd-dimensions",
    "video-out-params",
    "sub-text/ass-full",
}
_VERBS = frozenset({"observe_property", "get_property"})
MAX_OWNERS = 2
INGRESS_HISTORY = 8
INGRESS_OUTCOMES = frozenset(
    {
        "queued",
        "buffered",
        "stale-epoch",
        "closed",
        "not-ready",
        "candidate-full",
        "mailbox-full",
        "exception",
    }
)
_ERRORS = {
    "property not found": "property-not-found",
    "property unavailable": "property-unavailable",
    "timeout": "timeout",
    "disconnected": "disconnected",
    "stale-epoch": "stale-epoch",
}
_OUTCOMES = frozenset(
    {
        "pending",
        "success",
        "success-null",
        "success-missing-data",
        "property-not-found",
        "property-unavailable",
        "timeout",
        "error",
        "malformed",
        "exception",
        "disconnected",
        "stale-epoch",
    }
)


def reply_outcome(reply: object, verb: str) -> str:
    if not isinstance(reply, dict):
        return "malformed"
    error = reply.get("error")
    if error != "success":
        return _ERRORS.get(error, "error") if isinstance(error, str) else "malformed"
    if verb == "get_property":
        if "data" not in reply:
            return "success-missing-data"
        if reply["data"] is None:
            return "success-null"
    return "success"


def _safe_command(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    name, verb = row.get("property"), row.get("verb")
    if not isinstance(name, str) or not isinstance(verb, str):
        return None
    if name not in PROPERTIES or verb not in _VERBS or not count(row.get("sequence")):
        return None
    outcome = row.get("outcome")
    return {
        "property": name,
        "verb": verb,
        "sequence": row["sequence"],
        "started_ns": count(row.get("started_ns")),
        "completed_ns": count(row.get("completed_ns")),
        "outcome": outcome if isinstance(outcome, str) and outcome in _OUTCOMES else "unknown",
    }


def _safe_owner(raw: object) -> dict:
    if not isinstance(raw, dict) or count(raw.get("owner")) is None:
        return {"status": "invalid"}
    rows = raw.get("commands")
    if not isinstance(rows, list) or len(rows) > len(PROPERTIES) * len(_VERBS):
        return {"status": "invalid"}
    if count(raw.get("connection_epoch")) is None:
        return {"status": "invalid"}
    sanitized: list[dict] = []
    for row in rows:
        command = _safe_command(row)
        if command is None:
            return {"status": "invalid"}
        sanitized.append(command)
    if len({(row["property"], row["verb"]) for row in sanitized}) != len(rows) or len(
        {row["sequence"] for row in sanitized}
    ) != len(rows):
        return {"status": "invalid"}
    return {
        "status": "collected",
        "owner": raw["owner"],
        "commands": sanitized,
        "connection_epoch": count(raw.get("connection_epoch")),
        "closed": raw.get("closed") if type(raw.get("closed")) is bool else None,
        "stale_completions": count(raw.get("stale_completions")),
        "ingress": _safe_ingress(raw.get("ingress")),
    }


def _safe_ingress_row(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    name, source, outcome = row.get("property"), row.get("source"), row.get("outcome")
    if (
        not isinstance(name, str)
        or name not in PROPERTIES
        or not isinstance(source, str)
        or source not in {"wire", "replay-read"}
        or not isinstance(outcome, str)
        or outcome not in INGRESS_OUTCOMES
    ):
        return None
    fields = {key: count(row.get(key)) for key in ("sequence", "connection_epoch", "recorded_ns")}
    mailbox_sequence = count(row.get("mailbox_sequence"))
    if any(value is None for value in fields.values()) or (
        outcome == "queued" and mailbox_sequence is None
    ):
        return None
    return {
        **fields,
        "property": name,
        "source": source,
        "outcome": outcome,
        "mailbox_sequence": mailbox_sequence if outcome == "queued" else None,
    }


def _safe_ingress(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    rows, counts = raw.get("recent"), raw.get("counts")
    if not isinstance(rows, list) or len(rows) > INGRESS_HISTORY or not isinstance(counts, dict):
        return {"status": "invalid"}
    totals = {key: count(counts.get(key)) for key in sorted(INGRESS_OUTCOMES)}
    if any(value is None for value in totals.values()):
        return {"status": "invalid"}
    sanitized = [_safe_ingress_row(row) for row in rows]
    if None in sanitized:
        return {"status": "invalid"}
    retained = [row for row in sanitized if row is not None]
    evicted = count(raw.get("evicted"))
    total = sum(value for value in totals.values() if value is not None)
    if (
        evicted != max(0, total - INGRESS_HISTORY)
        or [row["sequence"] for row in retained]
        != list(range(max(0, total - INGRESS_HISTORY) + 1, total + 1))
        or any(
            sum(row["outcome"] == key for row in retained) > value
            for key, value in totals.items()
            if value is not None
        )
    ):
        return {"status": "invalid"}
    return {"status": "collected", "counts": totals, "recent": sanitized, "evicted": evicted}


def safe_snapshot(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        return {"status": "unsupported-schema"}
    owners = raw.get("owners")
    if not isinstance(owners, list) or len(owners) > MAX_OWNERS:
        return {"status": "invalid"}
    if not owners:
        return {"status": "unknown"}
    sanitized = [_safe_owner(owner) for owner in owners]
    identifiers = [owner.get("owner") for owner in sanitized]
    if None in identifiers or len(set(identifiers)) != len(identifiers):
        return {"status": "invalid"}
    return {
        "status": "partial",
        "schema": 1,
        "clock": "unix-nanoseconds",
        "scope": "gateway rendering-property commands and ingress; not all IPC or application",
        "applied": "unknown",
        "missing_command": "not-recorded",
        "owners": sanitized,
        "owners_evicted": count(raw.get("owners_evicted")),
    }


registry = EvidenceRegistry(max_owners=MAX_OWNERS)


class QueryEvidence:
    def __init__(self) -> None:
        self._registry = registry
        self.owner = self._registry.allocate()
        self._lock = threading.Lock()
        self._sequence = 0
        self._rows: dict[tuple[str, str], dict] = {}
        self._epoch = 0
        self._closed = False
        self._stale = 0
        self._ingress: deque[dict] = deque(maxlen=INGRESS_HISTORY)
        self._ingress_counts = dict.fromkeys(sorted(INGRESS_OUTCOMES), 0)
        self._ingress_sequence = 0

    def _save(self) -> None:
        self._registry.update(
            self.owner,
            {
                "owner": self.owner,
                "commands": list(self._rows.values()),
                "connection_epoch": self._epoch,
                "closed": self._closed,
                "stale_completions": self._stale,
                "ingress": {
                    "recent": list(self._ingress),
                    "counts": self._ingress_counts,
                    "evicted": max(0, self._ingress_sequence - INGRESS_HISTORY),
                },
            },
        )

    def ingress(
        self,
        message: dict,
        epoch: int,
        outcome: str,
        *,
        source: str = "wire",
        mailbox_sequence: int | None = None,
    ) -> None:
        name = message.get("name")
        if (
            message.get("event") != "property-change"
            or not isinstance(name, str)
            or name not in PROPERTIES
        ):
            return
        with self._lock:
            self._ingress_sequence += 1
            sequence = self._ingress_sequence
            self._ingress_counts[outcome] += 1
            self._ingress.append(
                {
                    "sequence": sequence,
                    "property": name,
                    "connection_epoch": epoch,
                    "source": source,
                    "outcome": outcome,
                    "recorded_ns": time.time_ns(),
                    "mailbox_sequence": mailbox_sequence,
                }
            )
            self._save()
        with otel_metrics.traced("player_property_ingress") as span:
            span.set("query_owner", self.owner)
            span.set("ingress_sequence", sequence)
            span.set("connection_epoch", epoch)
            span.set("property", name)
            span.set("source", source)
            span.set("outcome", outcome)
            if mailbox_sequence is not None:
                span.set("mailbox_sequence", mailbox_sequence)

    def command(self, send: Callable[..., dict], verb: str, *args: object, epoch: int) -> dict:
        name = args[-1] if args else None
        if not isinstance(name, str) or name not in PROPERTIES or verb not in _VERBS:
            return send(verb, *args)
        with self._lock:
            if not self._closed and epoch > self._epoch:
                self._epoch = epoch
                self._rows.clear()
            self._sequence += 1
            sequence = self._sequence
            row = {
                "property": name,
                "verb": verb,
                "sequence": sequence,
                "started_ns": time.time_ns(),
                "completed_ns": None,
                "outcome": "pending",
            }
            if not self._closed and epoch == self._epoch:
                self._rows[name, verb] = row
                self._save()
        outcome = "exception"
        with otel_metrics.traced("player_property_command") as span:
            span.set("query_owner", self.owner)
            span.set("query_sequence", sequence)
            span.set("connection_epoch", epoch)
            span.set("property", name)
            span.set("verb", verb)
            try:
                reply = send(verb, *args)
                outcome = reply_outcome(reply, verb)
                return reply
            finally:
                span.set("outcome", outcome)
                with self._lock:
                    if (
                        self._closed
                        or epoch != self._epoch
                        or self._rows.get((name, verb)) is not row
                    ):
                        self._stale += 1
                    else:
                        row.update(outcome=outcome, completed_ns=time.time_ns())
                    self._save()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._rows or self._ingress:
                self._save()
