"""Report projection of the opt-in trace stream, owned by its writer thread."""

from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy

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
        "reduced",
        "projection-closed",
        "projection-stale-epoch",
        "projection-exception",
    }
)


class DiagnosticSummary:
    def __init__(self) -> None:
        self._owners: dict[str, OrderedDict[int, dict]] = {}
        self._evicted: Counter[str] = Counter()
        self._outcomes: Counter[tuple[str, str]] = Counter()

    def consume(self, event: dict) -> None:
        import json

        attrs = event.get("args", {})
        self._color_event(event, attrs)
        if attrs.get("timing") == "operation-lifetime":
            key = (event["name"], str(attrs.get("outcome", "unknown")))
            if key not in self._outcomes and len(self._outcomes) >= 256:
                key = ("other", "other")
            self._outcomes[key] += 1
        if event.get("name") == "diagnostic_record":
            self._configuration(
                attrs["kind"], attrs["owner"], attrs["action"], json.loads(attrs["record"])
            )

    def _color_event(self, event: dict, attrs: dict) -> None:
        owner = attrs.get("configuration_owner")
        if owner and (
            event["name"] == "subtitle_timed_osd" or event["name"].startswith("subtitle_color_")
        ):
            from saitenka.app import timed_osd_evidence, whole_cue_evidence

            timed = event["name"] == "subtitle_timed_osd"
            record = dict(attrs, captured_ns=round(event["ts"] * 1000))
            if not timed:
                record.update(event=event["name"], requested_tokens=attrs.get("requested"))
            record = (
                timed_osd_evidence.safe_record(record)
                if timed
                else whole_cue_evidence.safe_record(record)
            )
            self._geometry(owner, "timed" if timed else "whole", record)

    def _configuration(self, kind: str, owner: int, action: str, record: dict) -> None:
        owners = self._owners.setdefault(kind, OrderedDict())
        if kind == "runtime_configuration":
            self._geometry(owner, action, record)
        elif kind == "player_queries":
            _query_record(owners, owner, action, record)
        elif kind == "player_configuration":
            state = owners.setdefault(
                owner,
                {
                    "owner": owner,
                    "closed": False,
                    "configurations": [],
                    "configurations_evicted": 0,
                },
            )
            if action == "close":
                state["closed"] = True
            else:
                state["configurations"].append(record)
                if len(state["configurations"]) > 4:
                    state["configurations"].pop(0)
                    state["configurations_evicted"] += 1
        else:
            owners[owner] = record
        owners.move_to_end(owner)
        limit = 2 if kind == "player_queries" else 4
        if len(owners) > limit:
            owners.popitem(last=False)
            self._evicted[kind] += 1

    def _geometry(self, owner: int, action: str, record: dict) -> None:
        owners = self._owners.setdefault("runtime_configuration", OrderedDict())
        _geometry_record(owners.setdefault(owner, _geometry(owner)), action, record)
        owners.move_to_end(owner)
        if len(owners) > 4:
            owners.popitem(last=False)
            self._evicted["runtime_configuration"] += 1

    def snapshot(self, kind: str) -> dict:
        return {
            "schema": 1,
            "owners": deepcopy(
                [
                    {k: v for k, v in row.items() if not k.startswith("_")}
                    for row in self._owners.get(kind, {}).values()
                ]
            ),
            "owners_evicted": self._evicted[kind],
        }

    def report(self, pending: dict[str, str]) -> dict:
        options = self.snapshot("session_configuration")
        options["profiles"] = self.snapshot("profiles")
        return {
            "pending": dict(Counter(pending.values())),
            "outcomes": [
                {"operation": name, "outcome": outcome, "count": count}
                for (name, outcome), count in sorted(self._outcomes.items())
            ],
            "scope": "recorded operation boundaries; not all product operations",
            "runtime_configuration": self.snapshot("runtime_configuration"),
            "player_configuration": self.snapshot("player_configuration"),
            "player_queries": self.snapshot("player_queries"),
            "session_configuration": options,
        }


def _geometry(owner: int) -> dict:
    return {
        "owner": owner,
        "closed": False,
        "generation": 0,
        "configurations": [],
        "configurations_evicted": 0,
        "requested": None,
        "published": None,
        "last_published": None,
        "renderer_selection": {"history": [], "evicted": 0},
        "geometry_sources": {"history": [], "evicted": 0},
        "whole_cue": {"history": [], "evicted": 0, "counts": {}},
        "timed_osd": {"history": [], "evicted": 0, "counts": {}},
    }


def _query_record(owners: OrderedDict[int, dict], owner: int, action: str, row: dict) -> None:

    state = owners.setdefault(
        owner,
        {
            "owner": owner,
            "commands": [],
            "connection_epoch": 0,
            "closed": False,
            "stale_completions": 0,
            "ingress": {
                "schema": 2,
                "recent": [],
                "counts": dict.fromkeys(sorted(INGRESS_OUTCOMES), 0),
                "evicted": 0,
            },
        },
    )
    if row["connection_epoch"] > state["connection_epoch"]:
        state["commands"] = []
    for key in ("connection_epoch", "closed", "stale_completions"):
        state[key] = row.pop(key)
    if action == "command":
        state["commands"] = [
            r
            for r in state["commands"]
            if (r["property"], r["verb"]) != (row["property"], row["verb"])
        ]
        state["commands"].append(row)
    elif action == "ingress":
        row["connection_epoch"] = row.pop("event_epoch")
        ingress = state["ingress"]
        ingress["counts"][row["outcome"]] += 1
        ingress["recent"].append(row)
        if len(ingress["recent"]) > INGRESS_HISTORY:
            ingress["recent"].pop(0)
            ingress["evicted"] += 1


def _append(state: dict, row: dict, limit: int) -> None:
    state["history"].append(row)
    if len(state["history"]) > limit:
        state["history"].pop(0)
        state["evicted"] += 1


def _geometry_record(state: dict, action: str, row: dict) -> None:
    if action == "describe":
        state["configurations"].append(row)
        if len(state["configurations"]) > 4:
            state["configurations"].pop(0)
            state["configurations_evicted"] += 1
    elif action == "requested":
        state.update(generation=row["generation"], requested=row)
    elif action == "published":
        state.update(published=row, last_published=row)
    elif action == "clear":
        state["published"] = None
    elif action == "invalidate":
        state.update(**row, requested=None, published=None)
    elif action == "selection":
        section = state["renderer_selection"]
        row["revision"] = section["evicted"] + len(section["history"]) + 1
        _append(section, row, 4)
    elif action == "source":
        _append(state["geometry_sources"], row, 32)
    elif action in {"whole", "timed"}:
        _history_record(state, action, row)


def _history_record(state: dict, action: str, row: dict) -> None:
    from saitenka.app import timed_osd_evidence, whole_cue_evidence

    section = state["whole_cue" if action == "whole" else "timed_osd"]
    limit = whole_cue_evidence.LIMIT if action == "whole" else timed_osd_evidence.LIMIT
    history = section["history"]
    comparable = {k: v for k, v in row.items() if k != "captured_ns"}
    if history and {k: v for k, v in history[-1].items() if k != "captured_ns"} == comparable:
        return
    if action == "timed":
        key = row.get("event", "declined")
        section["counts"][key] = section["counts"].get(key, 0) + 1
    elif row.get("event") == "decision":
        reason = row.get("reason", "unknown")
        identity = (row.get("color_session"), row.get("occurrence"))
        if identity[1] is None:
            identity = (row.get("text_hash"), row.get("generation"))
        seen = state.setdefault("_whole_occurrences", OrderedDict())
        reasons = seen.setdefault(identity, set())
        if reason not in reasons:
            section["counts"][reason] = section["counts"].get(reason, 0) + 1
            reasons.add(reason)
        if len(seen) > limit:
            seen.popitem(last=False)
    elif row.get("event") == "subtitle_color_outcome":
        counts = section.setdefault("outcomes", {})
        status = row.get("color_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    _append(section, row, limit)
