"""Demand-derived qualification of diagnostic mpv compositions, never display timestamps."""

from __future__ import annotations

import collections
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.report_reader import read_member, trace_evidence

if TYPE_CHECKING:
    from pathlib import Path


def qualify_report(source: Path, manifest: dict) -> dict:
    try:
        receipt = json.loads(read_member(source, "diagnostics/mpv-frame.json") or "{}")
        health = json.loads(read_member(source, "telemetry/health.json") or "{}")
        evidence = trace_evidence(source)
        if evidence["status"] != "readable":
            return {"qualified": False, "status": "unknown", "reason": "incomplete consumer trace"}
        check_provenance(receipt, health, manifest["provenance"])
        sessions = [
            e["args"].get("session")
            for e in evidence["events"]
            if e.get("ph") == "M" and e.get("name") == "session"
        ]
        if sessions != [receipt["session"]]:
            return {
                "qualified": False,
                "status": "unknown",
                "reason": "consumer trace session mismatch",
            }
        stages = publication_records(evidence["events"])
        return qualify(read_member(source, "diagnostics/mpv-frame.tsv") or "", manifest, stages)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"qualified": False, "status": "unknown", "reason": str(error)}


def check_provenance(receipt: dict, health: dict, expected: dict) -> None:
    if not receipt.get("session") or receipt["session"] != health.get("session"):
        raise ValueError("producer/consumer session mismatch")
    actual = {
        "session": receipt["session"],
        "binary_sha256": receipt.get("binary_sha256"),
        "source_sha256": receipt.get("consumer_source", {}).get("sha256"),
    }
    if actual != expected or not all(actual.values()):
        raise ValueError("producer/consumer provenance mismatch")
    if health.get("end") != "clean" or any(
        health.get(key) != 0
        for key in (
            "lost_events",
            "queue_dropped",
            "write_failures",
            "history_losses",
            "sample_failures",
            "pending_admissions_omitted",
        )
    ):
        raise ValueError("incomplete consumer trace health")


def read_events(raw: str) -> list[dict]:
    lines = raw.splitlines()
    if not lines or not lines[-1].startswith("# health "):
        raise ValueError("missing frame trace health")
    health = dict(field.split("=", 1) for field in lines[-1].split()[2:])
    if not (
        int(health["overflow"]) == 0
        and int(health["recorded"]) == int(health["attempted"]) == len(lines) - 1
    ):
        raise ValueError("incomplete frame trace")
    events = []
    for line in lines[:-1]:
        mono, wall, comp, name, *fields = line.split()
        events.append(
            {
                "mono": int(mono),
                "wall": int(wall),
                "comp": int(comp),
                "name": name,
                "args": dict(field.split("=", 1) for field in fields),
            }
        )
    return sorted(events, key=lambda event: event["mono"])


@dataclass(frozen=True)
class Frame:
    composition: int
    wall: int
    video_ms: int
    native: bool
    color: tuple[tuple[str, str, str, int | None, int | None], ...]
    interval_errors: int


def _frame(comp: int, batch: list[dict]) -> Frame | None:
    draw = next((e for e in batch if e["name"] == "draw_begin"), None)
    if draw is None:
        raise ValueError("composition lacks draw boundary")
    if not _successful(batch):
        return None
    ms = math.floor(float(draw["args"]["pts"]) * 1000 + 1e-6)
    attached = {
        e["args"]["index"]
        for e in batch
        if e["name"] == "overlay_attached" and int(e["args"]["parts"]) > 0
    }
    candidates = [e["args"] for e in batch if e["name"] == "external_candidate"]
    timed = [e["args"] for e in batch if e["name"] == "timed_candidate"]
    colors = tuple(
        _color_identity(a, timed)
        for a in candidates
        if a["images"] == "1"
        and a["render_index"] in attached
        and (int(a["id"]) >= 2001 or a["id"] == "1001")
    )
    return Frame(
        comp, draw["wall"], ms, "0" in attached, colors, _interval_errors(ms, timed, candidates)
    )


def _successful(batch: list[dict]) -> bool:
    if not all(any(e["name"] == phase for e in batch) for phase in ("gpu_render", "gpu_submit")):
        raise ValueError("composition lacks render/submission outcome")
    return all(
        any(e["name"] == phase and e["args"].get("ok") == "1" for e in batch)
        for phase in ("gpu_render", "gpu_submit")
    )


def _color_identity(
    candidate: dict[str, str], timed: list[dict]
) -> tuple[str, str, str, int | None, int | None]:
    interval = next(
        (t for t in timed if (t["id"], t["hash"]) == (candidate["id"], candidate["hash"])), None
    )
    return (
        candidate.get("peer", candidate["owner"]),
        candidate["id"],
        candidate["hash"],
        int(interval["start"]) if interval else None,
        int(interval["end"]) if interval else None,
    )


def _interval_errors(ms: int, timed: list[dict], candidates: list[dict]) -> int:
    errors = sum((int(a["start"]) <= ms < int(a["end"])) != (a["active"] == "1") for a in timed)
    for a in timed:
        external = [c for c in candidates if (c["id"], c["hash"]) == (a["id"], a["hash"])]
        errors += len(external) != 1 or external[0]["images"] != a["active"]
    return errors


def frames(events: list[dict]) -> list[Frame]:
    groups: dict[int, list[dict]] = collections.defaultdict(list)
    for event in events:
        if event["comp"]:
            groups[event["comp"]].append(event)
    return [frame for comp, batch in groups.items() if (frame := _frame(comp, batch)) is not None]


def _appearance(demand: dict, observed: list[Frame], stages: list[dict]) -> dict:
    row = {"occurrence": demand["occurrence"], "status": "unknown"}
    window = [f for f in observed if demand["wall_start_ns"] <= f.wall < demand["wall_end_ns"]]
    interval = [
        f for f in window if demand["video_start_ms"] <= f.video_ms < demand["video_end_ms"]
    ]
    native = [f for f in interval if f.native]
    if demand["requested"] == 0:
        return {**row, "status": "no-color"}
    if demand.get("unsupported"):
        return {**row, "status": "unsupported"}
    if not native:
        return {**row, "reason": "no-native-composition"}
    matches = _matching_stages(demand, stages)
    if not matches:
        return {**row, "status": "missed", "reason": "unstaged"}
    matches = [s for s in matches if not _retired_before(s, native[0].wall, stages)]
    if not matches:
        return {**row, "status": "missed", "reason": "retired-preparation"}
    stage = min(matches, key=lambda s: s["captured_ns"])
    if not stage.get("payload_hash") or not demand.get("owner"):
        return {**row, "reason": "unmatched-payload-or-owner"}
    return {**row, **_alignment(demand, native, window, stage)}


def _matching_stages(demand: dict, stages: list[dict]) -> list[dict]:
    matches = [
        s
        for s in stages
        if all(
            s.get(key) == demand.get(key)
            for key in ("text_hash", "start_ms", "end_ms", "epoch", "connection_epoch")
        )
        and s.get("event") == "ack"
        and s.get("captured_ns", 0) < demand["wall_end_ns"]
    ]
    return [
        s
        for s in matches
        if s.get("video_start_ms") == demand["video_start_ms"]
        and s.get("video_end_ms") == demand["video_end_ms"]
    ]


def _alignment(demand: dict, native: list[Frame], window: list[Frame], stage: dict) -> dict:
    expected = (
        demand["owner"],
        str(stage["slot"]),
        stage["payload_hash"],
        demand["video_start_ms"],
        demand["video_end_ms"],
    )
    wrong = [f for f in native if f.color != (expected,)]
    stale = [
        f
        for f in window
        if expected in f.color
        and not demand["video_start_ms"] <= f.video_ms < demand["video_end_ms"]
    ]
    ready = stage["captured_ns"] < native[0].wall
    boundary = _complete_interval(demand, window)
    return {
        "status": "stale"
        if stale
        else "late"
        if wrong or not ready
        else "complete"
        if boundary
        else "unknown",
        "ready": ready,
        "first_native_composition": native[0].composition,
        "first_color_composition": next(
            (f.composition for f in native if f.color == (expected,)), None
        ),
        "compositions": len(native),
        "missed_compositions": len(wrong),
        "stale_compositions": len(stale),
        "complete_interval": boundary,
    }


def _complete_interval(demand: dict, window: list[Frame]) -> bool:
    onset = any(f.video_ms < demand["video_start_ms"] for f in window)
    offset = any(f.video_ms >= demand["video_end_ms"] for f in window)
    return (onset or demand.get("entry_composition") == window[0].composition) and (
        offset or demand.get("exit_composition") is not None
    )


def _validate(manifest: dict) -> None:
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != 1
        or not manifest.get("appearances")
    ):
        raise ValueError("missing independent appearance census")
    identities = set()
    for demand in manifest["appearances"]:
        identity = demand["occurrence"]
        if identity in identities:
            raise ValueError("duplicate occurrence")
        identities.add(identity)
        for start, end in (
            ("wall_start_ns", "wall_end_ns"),
            ("video_start_ms", "video_end_ms"),
            ("start_ms", "end_ms"),
        ):
            if (
                not all(type(demand[k]) is int for k in (start, end))
                or demand[start] >= demand[end]
            ):
                raise ValueError("invalid appearance interval")
        if type(demand["requested"]) is not int or demand["requested"] < 0:
            raise ValueError("invalid color demand")


def qualify(raw: str, manifest: dict, stages: list[dict]) -> dict:
    """Require an independent census; losing any required evidence fails closed."""
    result: dict = {"qualified": False, "endpoint": "gpu-submission-not-physical-display"}
    try:
        _validate(manifest)
        observed = frames(read_events(raw))
        if not observed or manifest.get("evidence_complete") is not True:
            return {
                **result,
                "status": "unknown",
                "reason": "incomplete consumer or producer evidence",
                "counters": None,
            }
        _check_lifecycle(observed, stages)
        _check_seek_boundaries(manifest, observed, stages)
        return {**result, **_verdict(manifest, observed, stages)}
    except (ValueError, KeyError, TypeError, OverflowError, IndexError) as error:
        result.update(status="unknown", reason=str(error), counters=None)
    return result


def _verdict(manifest: dict, observed: list[Frame], stages: list[dict]) -> dict:
    rows = [_appearance(demand, observed, stages) for demand in manifest["appearances"]]
    required = [
        row
        for row, demand in zip(rows, manifest["appearances"], strict=True)
        if demand.get("required_warm") is True
    ]
    stale = _stale_cutovers(manifest, observed)
    interval_errors = sum(f.interval_errors for f in observed)
    duplicate = sum(len(f.color) > 1 for f in observed)
    orphan = sum(bool(f.color) and not f.native for f in observed)
    complete = all(row["status"] in {"complete", "no-color", "unsupported"} for row in rows)
    ready = bool(required) and all(
        row["status"] == "complete" and row.get("ready") for row in required
    )
    return {
        "qualified": bool(
            complete and ready and not (stale or interval_errors or duplicate or orphan)
        ),
        "status": "collected",
        "appearances": rows,
        "outcomes": dict(collections.Counter(row["status"] for row in rows)),
        "required_warm": len(required),
        "counters": {
            "stale_epochs": stale,
            "interval_errors": interval_errors,
            "duplicate_compositions": duplicate,
            "orphan_compositions": orphan,
            **_appearance_counters(rows),
        },
    }


def _stale_cutovers(manifest: dict, observed: list[Frame]) -> int:
    cutovers = manifest.get("cutovers", [])
    return sum(
        any(
            f.composition >= change["composition"]
            and any(
                color[:3] == (change["owner"], str(change["slot"]), change["payload_hash"])
                for color in f.color
            )
            for f in observed
        )
        for change in cutovers
    )


def _check_lifecycle(observed: list[Frame], stages: list[dict]) -> None:
    first, last = min(f.wall for f in observed), max(f.wall for f in observed)
    acknowledged = [row for row in stages if row.get("event") == "ack"]
    for row in stages:
        if (
            row.get("event") not in {"invalidate", "connection-replaced"}
            or not first <= row["captured_ns"] <= last
        ):
            continue
        if any(ack["captured_ns"] < row["captured_ns"] for ack in acknowledged):
            raise ValueError("native epoch cutover is unproven")


def _retired_before(stage: dict, wall: int, records: list[dict]) -> bool:
    for row in records:
        if not stage["captured_ns"] < row.get("captured_ns", 0) < wall:
            continue
        if row.get("event") in {"invalidate", "connection-replaced"}:
            return True
        if (
            row.get("event") in {"remove", "removed", "remove-failed", "remove-rejected"}
            and row.get("slot") == stage["slot"]
        ):
            return True
    return False


def _appearance_counters(rows: list[dict]) -> dict:
    return {
        "readiness_misses": sum(
            row.get("ready") is False or row["status"] == "missed" for row in rows
        ),
        "first_composition_misses": sum(
            row.get("first_native_composition") is not None
            and row.get("first_color_composition") != row["first_native_composition"]
            for row in rows
        ),
    }


def publication_records(events: list[dict]) -> list[dict]:
    records = []
    for event in events:
        if event.get("name") == "subtitle_timed_osd":
            records.append({**event["args"], "captured_ns": round(event["ts"] * 1000)})
        elif event.get("name") == "sub_seek" and "target_start" in event.get("args", {}):
            records.append(
                {**event["args"], "event": "navigation", "captured_ns": round(event["ts"] * 1000)}
            )
    return records


def _check_seek_boundaries(manifest: dict, observed: list[Frame], records: list[dict]) -> None:
    by_composition = {frame.composition: frame for frame in observed}
    navigation = [row for row in records if row.get("event") == "navigation"]
    for demand in manifest["appearances"]:
        delay = demand["video_start_ms"] - demand["start_ms"]
        for field, wall_key in (
            ("entry_composition", "wall_start_ns"),
            ("exit_composition", "wall_end_ns"),
        ):
            composition = demand.get(field)
            if composition is None:
                continue
            _check_seek_boundary(by_composition[composition], navigation, demand[wall_key], delay)


def _check_seek_boundary(frame: Frame, navigation: list[dict], wall: int, delay: int) -> None:
    candidates = [row for row in navigation if row["captured_ns"] <= frame.wall]
    if not candidates or frame.wall != wall:
        raise ValueError("unproven seek boundary")
    intent = max(candidates, key=lambda row: row["captured_ns"])
    target = round(intent["target_start"] * 1000) + delay
    if not target <= frame.video_ms <= target + 100:
        raise ValueError("seek boundary differs from intended destination")
