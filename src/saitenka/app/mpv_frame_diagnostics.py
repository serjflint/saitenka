"""Opt-in frame-probe launch provenance and session-bound report collection."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from saitenka import otel_metrics

_LIMIT = 64 * 1024 * 1024
log = logging.getLogger(__name__)


def launch_environment(binary: str, cache: Path, session: str) -> dict[str, str] | None:
    try:
        return _launch_environment(binary, cache, session)
    except OSError as error:
        log.warning("mpv frame diagnostics unavailable: %s", error)
        return None


def _launch_environment(binary: str, cache: Path, session: str) -> dict[str, str]:
    cache.mkdir(parents=True, exist_ok=True)
    trace = cache / f"mpv-frame-{session}.tsv"
    trace.unlink(missing_ok=True)
    with Path(binary).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    from saitenka.app.report_schema import startup_source_identity

    receipt = {
        "consumer_source": dict(startup_source_identity()),
        "schema": 1,
        "session": session,
        "binary": str(Path(binary).resolve()),
        "binary_sha256": digest,
        "trace": trace.name,
        "probe_status": "requested-not-confirmed",
        "endpoint": "gpu-submission-not-physical-presentation",
    }
    (cache / f"mpv-frame-{session}.json").write_text(json.dumps(receipt), encoding="utf-8")
    return dict(os.environ, SAITENKA_MPV_TRACE=str(trace))


def record_player(ipc, cache: Path, session: str) -> None:
    try:
        _record_player(ipc, cache, session)
    except (OSError, ValueError, TypeError) as error:
        log.warning("mpv frame provenance unavailable: %s", error)


def _record_player(ipc, cache: Path, session: str) -> None:
    path = cache / f"mpv-frame-{session}.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise TypeError("invalid frame receipt")
    peer = ipc.command("client_name").get("data")
    commands = ipc.query("command-list") or []
    receipt.update(
        ipc_connection=ipc.diagnostic_id,
        connection_epoch=0,
        ipc_peer=peer,
        mpv_version=ipc.query("mpv-version"),
        timed_osd=any(c.get("name") == "osd-overlay-timed" for c in commands),
        timed_osd_api=[c for c in commands if c.get("name") == "osd-overlay-timed"],
        subtitle_layout=ipc.probe("subtitle-layout").get("error"),
        subtitle_clock={
            name: ipc.query(name)
            for name in ("sub-delay", "sub-speed", "sub-fps", "play-direction")
        },
    )
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with otel_metrics.traced(
        "mpv_ipc_peer", ipc_connection=ipc.diagnostic_id, ipc_peer=str(peer), connection_epoch="0"
    ):
        pass


def collect(cache: Path, session: str | None) -> dict[str, str]:
    path = cache / f"mpv-frame-{session}.json"
    if not path.exists():
        return {}
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or not session or receipt.get("session") != session:
            return {}
        trace = cache / f"mpv-frame-{session}.tsv"
        raw = b""
        if trace.exists():
            with trace.open("rb") as stream:
                raw = stream.read(_LIMIT + 1)
        receipt["probe_status"] = trace_health(raw)
        if len(raw) > _LIMIT:
            raw = b""
        members = {"diagnostics/mpv-frame.json": json.dumps(receipt)}
        if raw:
            members["diagnostics/mpv-frame.tsv"] = raw.decode("utf-8", errors="replace")
        return members
    except (OSError, ValueError, TypeError):
        return {"diagnostics/mpv-frame.json": json.dumps({"probe_status": "unreadable"})}


def trace_health(raw: bytes) -> str:
    if len(raw) > _LIMIT:
        return "oversized"
    lines = raw.splitlines()
    if not lines or not lines[-1].startswith(b"# health "):
        return "missing-or-incomplete"
    try:
        counts = dict(item.split(b"=", 1) for item in lines[-1].split()[2:])
        if int(counts[b"overflow"]):
            return "overflow"
        if int(counts[b"recorded"]) == int(counts[b"attempted"]) == len(lines) - 1:
            return "complete"
    except (ValueError, KeyError):
        pass
    return "invalid-health"
