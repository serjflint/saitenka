"""Local-only report bundles: allowlisted metadata by default, raw detail only by explicit opt-in."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from saitenka.version import overlay_version as _overlay_version

log = logging.getLogger(__name__)

# Scrub ``key = "..."`` / ``token: ...`` / ``Authorization: Bearer <tok>`` style secrets from any text.
# The ``(?:bearer|token)\s+`` skip is why ``Authorization: Bearer <tok>`` redacts the TOKEN, not the
# word "Bearer" (a property test caught that).
# The quote before the separator is what reaches JSON's ``"key": "…"``, and the bundle's largest
# member (`telemetry/trace.json`) is JSON — the name had to touch ``=``/``:`` directly to match, so
# every quoted form went through untouched.
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|key|token|secret|password|authorization|bearer)\b(['\"]?\s*[=:]\s*|\s+)"
    r"(?:(?:bearer|token)\s+)?"
    r'["\']?([^\s"\']{6,})'
)


def _redact_secrets(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)


def _scrub_home(text: str) -> str:
    """Replace the home dir path and OS username with placeholders so a shared report/crash log
    doesn't leak the username embedded in every path (`C:\\Users\\Jane\\…` → `<HOME>\\…`)."""
    import getpass

    home = str(Path.home())
    encoded_homes = {
        home,
        json.dumps(home)[1:-1],
        json.dumps(home, ensure_ascii=False)[1:-1],
    }
    out = text
    for encoded_home in encoded_homes:
        out = out.replace(encoded_home, "<HOME>")
    try:
        user = getpass.getuser()
    except OSError:  # pragma: no cover — getuser can raise if no login name is resolvable
        user = ""
    if user:
        out = re.sub(rf"(?<!\w){re.escape(user)}(?!\w)", "<USER>", out)
    return out


def redact(text: str) -> str:
    """Full redaction for anything that may be shared: secrets + home/username."""
    return _scrub_home(_redact_secrets(text))


def _redact_config(text: str) -> str:
    """Blank the value of any secret-ish TOML key (``key``/``token``/``secret``/``password``), keeping
    the line so the report still shows the key *was* set — just not its value."""
    out = []
    for line in text.splitlines():
        if re.match(r"\s*#?\s*(api[_-]?key|key|token|secret|password)\s*=", line, re.IGNORECASE):
            out.append(re.sub(r"=\s*.*$", '= "<redacted>"', line))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _first_line(*cmd: str) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=10, check=False
        )
        lines = ((out.stdout or "") + (out.stderr or "")).strip().splitlines()
        return lines[0] if lines else "no output"
    except (OSError, subprocess.SubprocessError):
        return "not found"


def _gil_state() -> str:
    fn = getattr(sys, "_is_gil_enabled", None)
    return "free-threaded (GIL off)" if fn and not fn() else "standard (GIL on)"


def _collect_versions() -> dict[str, str]:
    from saitenka.app.config import load_config
    from saitenka.mpvio.discover import find_mpv

    mpv = find_mpv(load_config().get("mpv_path"))
    return {
        "versions.txt": (
            "\n".join(
                [
                    f"saitenka: {_overlay_version()}",
                    f"python: {sys.version.split()[0]} — {_gil_state()}",
                    f"platform: {platform.platform()}",
                    f"mpv: {_first_line(mpv, '--version') if mpv else 'NOT FOUND'}",
                    f"ffmpeg: {_first_line('ffmpeg', '-version')}",
                ]
            )
            + "\n"
        )
    }


def _collect_doctor() -> dict[str, str]:
    """Structured doctor report (the machine-readable version of the ✓/!/✗ health check)."""
    from saitenka.app.doctor import run_checks

    def collect_json() -> str:
        try:
            payload = run_checks().to_json()
        except Exception as error:  # never let a doctor hiccup abort the bundle
            log.warning("doctor check failed while building report bundle", exc_info=True)
            payload = {"error": str(error)}
        return json.dumps(payload, ensure_ascii=False, indent=2)

    doctor_json, output = _capture_process_output(collect_json)
    members = {"doctor.json": redact(doctor_json)}
    if output.strip():
        members["doctor-output.txt"] = redact(output)
    return members


def _capture_process_output[T](action: Callable[[], T]) -> tuple[T, str]:
    """Run one local diagnostic with Python and native stdout/stderr captured together."""
    with tempfile.TemporaryFile() as capture:
        saved = [os.dup(fd) for fd in (1, 2)]
        try:
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
            os.dup2(capture.fileno(), 1)
            os.dup2(capture.fileno(), 2)
            result = action()
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
        finally:
            for fd, original in zip((1, 2), saved, strict=True):
                os.dup2(original, fd)
                os.close(original)
        capture.seek(0)
        output = capture.read().decode("utf-8", errors="replace")
    return result, output


def _collect_config() -> dict[str, str]:
    """Our config, secrets + home/username removed."""
    from saitenka.app.config import config_path

    cp = config_path()
    if not cp.exists():
        return {}
    return {
        "overlay.toml": _scrub_home(
            _redact_config(cp.read_text(encoding="utf-8", errors="replace"))
        )
    }


def _collect_mpv_conf() -> dict[str, str]:
    """mpv / mpv.net config (input-ipc-server, sub-auto, … — the exact things that break), plus the
    binding table: a pause we did not send is only excludable against the user's own binds."""
    from saitenka.app.paths import mpv_conf_paths, mpv_input_conf_paths

    members: dict[str, str] = {}
    for p in (*mpv_conf_paths(), *mpv_input_conf_paths()):
        if p.exists():
            members[f"mpv/{p.parent.name}.{p.name}"] = _scrub_home(
                p.read_text(encoding="utf-8", errors="replace")
            )
    return members


def _collect_scripts() -> dict[str, str]:
    """OUR plugin lua in full; OTHER scripts by NAME only (they cause overlay conflicts, but their
    bodies aren't ours to bundle)."""
    from saitenka.app.paths import mpv_scripts_dirs
    from saitenka.app.plugin import LUA_NAME

    members: dict[str, str] = {}
    for d in mpv_scripts_dirs():
        lua = d / LUA_NAME
        if lua.exists():
            members[f"scripts/{d.parent.name}.{LUA_NAME}"] = lua.read_text(
                encoding="utf-8", errors="replace"
            )
        if d.exists():
            others = sorted(x.name for x in d.iterdir() if x.name != LUA_NAME)
            members[f"scripts/{d.parent.name}.listing.txt"] = "\n".join(others) + "\n"
    return members


def _collect_dict_inventory() -> dict[str, str]:
    """What's imported into the consolidated DB, plus any pre-consolidation leftovers — makes
    "configured but not imported" and stale caches visible without the user's machine."""
    from saitenka.app.dictdb import DictionaryDb, db_path
    from saitenka.app.paths import legacy_dict_artifacts

    db_file = db_path()
    if not db_file.exists():
        inv = [f"[database] {db_file}", "  (none — run `saitenka import`)"]
    else:
        try:
            st = DictionaryDb.open().stats()
            inv = [f"[database] {db_file} (schema {st.schema}, {st.size_bytes / 1e6:.0f} MB)"]
            for d in st.dicts:
                # per-table counts make a missing tags table (sidecar-era import) visible without
                # shipping any dictionary content — the whole reason report/doctor can carry this.
                nums = " ".join(
                    f"{k}={d.counts[k]}"
                    for k in ("entries", "keys", "kanji", "term_meta", "tags")
                    if d.counts[k]
                )
                inv.append(f"  [{d.row.kind}] {d.row.title}  {nums}".rstrip())
            inv += [] if st.dicts else ["  (empty)"]
        except Exception:  # pragma: no cover — diagnostics must never raise
            log.debug("dictionary inventory read failed", exc_info=True)
            inv = [f"[database] {db_file}", "  (unreadable)"]
    arts = legacy_dict_artifacts()
    if arts:
        inv += ["[legacy — unused, safe to delete]"]
        inv += [f"  {d} ({n} files, {b / 1e6:.0f} MB)" for d, n, b in arts]
    return {"dicts.listing.txt": _scrub_home("\n".join(inv) + "\n")}


def _collect_crashes() -> dict[str, str]:
    """Recent crash reports (already redacted at write time; the whole point of capturing them), plus
    any shutdown thread dump — a file that exists at all means an exit hung (see `arm_exit_watchdog`)."""
    from saitenka.app.paths import crash_dir

    members: dict[str, str] = {}
    cd = crash_dir()
    if cd.exists():
        for pattern in ("crash-*.log", "shutdown-hang-*.log", "faulthandler.log"):
            for c in sorted(cd.glob(pattern))[-5:]:
                with c.open("rb") as stream:
                    size = c.stat().st_size
                    stream.seek(max(0, size - 1024 * 1024))
                    text = stream.read(1024 * 1024).decode("utf-8", errors="replace")
                members[f"crashes/{c.name}"] = redact(text)
                members[f"crashes/{c.name}.metadata.json"] = json.dumps(
                    {
                        "session": "unknown",
                        "scope": "historical",
                        "source_bytes": size,
                        "truncated": size > 1024 * 1024,
                    }
                )
    return members


#: Where macOS files a crashed process's report. Linux (core dumps) and Windows (WER) have no
#: comparable always-on text artifact, so this is deliberately macOS-only rather than a stub.
_MACOS_CRASH_REPORTS = "Library/Logs/DiagnosticReports"

#: Only reports from around the sessions this bundle covers; older ones are a different bug.
_PLAYER_CRASH_MAX_AGE_S = 24 * 3600


def _collect_player_crashes() -> dict[str, str]:
    """mpv's own native crash reports.

    A player killed by a signal leaves nothing useful on our side of the socket: the overlay log gets
    an EOF and a signal number, which says *that* it died, never *where*. Both SIGBUS crashes behind
    `47de5fa3` were only mechanised by the faulting frame in one of these files, and getting it meant
    leaving the bundle — so the one artifact that could settle the question was the one a report
    could not carry. The frames name mpv's own functions; the redactor handles the paths around them.
    """
    # `platform.system()`, not `sys.platform`: the type checkers NARROW `sys.platform`, so comparing
    # it makes every line below unreachable when checking for Linux — green on macOS, `types` failure
    # on CI. This reads the same fact without the narrowing.
    directory = Path.home() / _MACOS_CRASH_REPORTS
    if platform.system() != "Darwin" or not directory.is_dir():
        return {}
    cutoff = time.time() - _PLAYER_CRASH_MAX_AGE_S
    recent = sorted(
        (p for p in directory.glob("mpv-*.ips") if p.stat().st_mtime >= cutoff),
        key=lambda p: p.stat().st_mtime,
    )[-2:]
    return {
        f"crashes/player/{p.name}": redact(p.read_text(encoding="utf-8", errors="replace"))
        for p in recent
    }


def _trace_session(doc: dict) -> str | None:
    value = doc.get("otherData", {}).get("session")
    return value if isinstance(value, str) and value else None


def _collect_telemetry(session: str | None = None) -> dict[str, str]:
    """The CTF trace file the LIVE overlay session wrote to disk, if telemetry was enabled for it —
    `report` runs in its own short-lived process, so it can only see what made it to disk, not the
    in-memory metrics snapshot of a session that has already exited (metrics stay pull-based /
    process-local by design, see otel_metrics.snapshot)."""
    from saitenka.app.config import load_config, resolve_telemetry
    from saitenka.app.telemetry import export_dir

    directory = export_dir(resolve_telemetry(load_config()))
    candidates = sorted(
        p for p in directory.glob("trace-*.json") if not p.name.endswith(".health.json")
    )
    accounting: dict[str, object] = {"session": session, "status": "unavailable", "candidates": []}
    attempts: list[dict[str, object]] = []
    members: dict[str, str] = {}
    health_session = session
    selected_source: Path | None = None
    for path in reversed(candidates[-10:]):
        entry: dict[str, object] = {"source": path.name}
        attempts.append(entry)
        try:
            with path.open("rb") as stream:
                raw = stream.read(64 * 1024 * 1024 + 1)
            if len(raw) > 64 * 1024 * 1024:
                entry["status"] = "too-large"
                continue
            doc = json.loads(raw)
            recorded = _trace_session(doc)
            entry["session"] = recorded
            if session is not None and recorded != session:
                entry["status"] = "session-mismatch"
                continue
            if not isinstance(doc.get("traceEvents"), list):
                entry["status"] = "invalid"
                continue
            entry["status"] = "collected"
            members["telemetry/trace.json"] = redact(raw.decode("utf-8"))
            health_session = recorded
            selected_source = path
            accounting["status"] = "collected" if recorded else "unknown-provenance"
            break
        except (OSError, ValueError, AttributeError, UnicodeError):
            entry["status"] = "unreadable-or-concurrent-write"
    accounting["candidates"] = attempts
    members.update(_collect_trace_health(directory, health_session, source=selected_source))
    members["telemetry/collection.json"] = json.dumps(accounting)
    return members


DIAGNOSTIC_JSON_LIMIT = 2 * 1024 * 1024


def _diagnostic_json(path: Path) -> tuple[str, dict]:
    with path.open("rb") as stream:
        raw = stream.read(DIAGNOSTIC_JSON_LIMIT + 1)
    if len(raw) > DIAGNOSTIC_JSON_LIMIT:
        raise ValueError("diagnostic exceeds size limit")
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise TypeError("diagnostic must be an object")
    return raw.decode("utf-8"), doc


def _collect_trace_health(
    directory: Path, session: str | None, *, source: Path | None = None
) -> dict[str, str]:
    attempts = []
    members: dict[str, str] = {}
    candidates = (
        [source.with_suffix(".health.json")]
        if source
        else sorted(directory.glob("trace-*.health.json"), reverse=True)[:10]
    )
    for path in candidates:
        try:
            raw, health = _diagnostic_json(path)
            if session is not None and health.get("session") != session:
                attempts.append({"source": path.name, "status": "session-mismatch"})
                continue
            members["telemetry/health.json"] = redact(raw)
            attempts.append({"source": path.name, "status": "collected"})
            break
        except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
            attempts.append({"source": path.name, "status": "unreadable"})
    members["telemetry/health-collection.json"] = json.dumps(
        {
            "sources": attempts,
            "session": session,
            "trace_source": source.name if source else None,
            "scope": "selected-trace" if source else "standalone-health",
        }
    )
    return members


def _latest_session(log_text: str) -> str | None:
    """The session id of the most recent run in the (JSON-lines) overlay log — the run a fresh report
    is almost always about. None if the log predates session stamping."""
    for line in reversed(log_text.splitlines()):
        try:
            sid = json.loads(line).get("session")
        except (ValueError, AttributeError, RecursionError):
            continue
        if sid:
            return str(sid)
    return None


def _collect_operation_summary(session: str | None) -> dict[str, str]:
    from saitenka.app.paths import cache_dir

    if session is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session):
        return {}
    summary = cache_dir() / "diagnostics" / f"session-{session}.json"
    if not summary.is_file():
        return {}
    try:
        raw, doc = _diagnostic_json(summary)
        if doc.get("session") != session:
            return {"diagnostics/collection.json": json.dumps({"status": "session-mismatch"})}
        return {"diagnostics/session.json": redact(raw)}
    except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
        return {"diagnostics/collection.json": json.dumps({"status": "summary-unavailable"})}


def _read_log_snapshot(path: Path) -> tuple[str, dict]:
    info: dict[str, object] = {"source": path.name, "status": "unavailable"}
    try:
        before = path.stat()
        with path.open("rb") as stream:
            stream.seek(max(0, before.st_size - 4 * 1024 * 1024))
            raw = stream.read(4 * 1024 * 1024)
        after = path.stat()
        changed = (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        info.update(
            status="changed-during-capture" if changed else "collected",
            source_bytes=before.st_size,
            captured_bytes=len(raw),
            truncated=before.st_size > len(raw),
            captured_ns=time.time_ns(),
            session_scope="mixed-or-unknown",
            interval_completeness="unknown",
        )
        return raw.decode("utf-8", errors="replace"), info
    except OSError:
        return "", info


def _collect_logs(log_path: Path, *, include: bool) -> tuple[str | None, dict[str, str]]:
    text, current = _read_log_snapshot(log_path)
    session = _latest_session(text)
    if not include:
        return session, {"logs/collection.json": json.dumps({"status": "opted-out"})}
    sources = [current]
    members = {"overlay.log": redact(text)} if text else {}
    segments = sorted(log_path.parent.glob("overlay.log.*"))
    for segment in segments[-5:]:
        text, info = _read_log_snapshot(segment)
        if text and session and any(_latest_session(line) == session for line in text.splitlines()):
            members[f"logs/{segment.name}"] = redact(text)
        elif text:
            info["status"] = "no-matching-session"
        sources.append(info)
    members["logs/collection.json"] = json.dumps(
        {
            "session": session,
            "sources": sources,
            "segments_omitted": max(0, len(segments) - 5),
            "scope": "best-effort rotating snapshots; complete session intervals not established",
        }
    )
    return session, members


def _session_records(log_path: Path, session: str | None) -> tuple[list[str], list[dict]]:
    """The reported session's lines from the active log and its rotated segments, oldest first."""
    segments = sorted(
        log_path.parent.glob("overlay.log.*"),
        key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0,
        reverse=True,
    )[:5]
    lines: list[str] = []
    sources: list[dict] = []
    for path in (*segments, log_path):
        text, info = _read_log_snapshot(path)
        matched = 0
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if (
                session is not None
                and isinstance(record, dict)
                and record.get("session") == session
            ):
                lines.append(line)
                matched += 1
        info["session_lines"] = matched
        sources.append(info)
    return lines, sources


def _current_format(lines: list[str]) -> bool:
    """Whether every line was written by a build that scrubs what `log_privacy` scrubs."""
    from saitenka.app.log_privacy import LOG_FORMAT

    return bool(lines) and all(json.loads(line).get("log_format") == LOG_FORMAT for line in lines)


def _collect_default(log_path: Path, *, include_log: bool) -> dict[str, str]:
    """Saitenka's own diagnostics for the latest session, from a log that labels media and content.

    A session logged by an older build ships neither its log nor its trace: those lines still carry
    the media names and cue text the current format keeps out.
    """
    text, _ = _read_log_snapshot(log_path)
    session = _latest_session(text)
    lines, sources = _session_records(log_path, session)
    current = _current_format(lines)
    if session is None:
        status = "no-session"
    elif not current:
        status = "predates-sanitised-format"
    elif not include_log:
        status = "opted-out"
    else:
        status = "collected"

    members: dict[str, str] = {}
    members.update(_collect_versions())
    doctor = _collect_doctor()
    members["doctor.json"] = doctor["doctor.json"]
    # Not `session.json`: the producer writes it unfiltered; the envelope is its allowlisted projection.
    members.update(_collect_metadata(session))
    if status == "collected":
        members["overlay.log"] = redact("\n".join(lines) + "\n")
    if current:
        members.update(_collect_telemetry(session))
    members["logs/collection.json"] = json.dumps(
        {
            "session": session,
            "status": status,
            "sources": sources,
            "scope": "the reported session's lines only",
        }
    )
    members["MANIFEST.txt"] = _default_manifest(members, session=session, status=status)
    return members


def _default_manifest(members: dict[str, str], *, session: str | None, status: str) -> str:
    lines = [
        f"saitenka {_overlay_version()} diagnostics bundle",
        f"generated: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"latest session: {session or 'n/a'}",
        "",
        "Created locally and NEVER uploaded by saitenka. Review before sharing.",
        "Saitenka's own log and trace for the latest session. Video and subtitle file names, cue text",
        "and looked-up words appear as digests, home paths as <HOME>. Cue timings, deck, note type",
        "and dictionary names remain.",
        "No configuration, mpv config or log, or crash text: those need `report --diagnostic-detail`.",
        "",
        f"log: {status}",
        "",
        "contents:",
    ]
    lines += [f"  - {name}" for name in members if name != "MANIFEST.txt"]
    return "\n".join(lines) + "\n"


def collect(*, include_log: bool = True, diagnostic_detail: bool = False) -> dict[str, str]:
    """Build the session's sanitized diagnostics, or explicitly requested sensitive detail."""
    from saitenka.app.paths import cache_dir

    if not diagnostic_detail:
        return _collect_default(cache_dir() / "overlay.log", include_log=include_log)

    log_path = cache_dir() / "overlay.log"  # resolve dynamically (respects $SAITENKA_CACHE_DIR)

    members: dict[str, str] = {}
    members.update(_collect_versions())
    members.update(_collect_doctor())
    members.update(_collect_config())
    members.update(_collect_mpv_conf())
    members.update(_collect_scripts())

    # The rotating overlay log — redacted; opt-out via --no-log (may contain filenames/sentences).
    session, logs = _collect_logs(log_path, include=include_log)
    members.update(logs)

    # mpv's own log (run launches mpv with --log-file) — the codec / sub-load / track-select side that
    # the overlay log can't see. Redacted + gated like the overlay log (it holds the video path).
    mpv_log = cache_dir() / "mpv.log"
    if include_log and mpv_log.exists():
        text, info = _read_log_snapshot(mpv_log)
        if text is not None:
            members["mpv.log"] = redact(text)
        members["logs/mpv.collection.json"] = json.dumps(info)

    if include_log:
        from saitenka.app.mpv_frame_diagnostics import collect as collect_frames

        members.update(
            {name: redact(text) for name, text in collect_frames(cache_dir(), session).items()}
        )

    members.update(_collect_dict_inventory())
    members.update(_collect_crashes())
    members.update(_collect_player_crashes())
    members.update(_collect_telemetry(session))
    members.update(_collect_operation_summary(session))
    members.update(_collect_metadata(session))

    members["MANIFEST.txt"] = _manifest(members, include_log=include_log, session=session)
    return members


def _collect_metadata(session: str | None) -> dict[str, str]:
    import tomllib

    from saitenka.app.config import config_path
    from saitenka.app.report_schema import (
        build_identity,
        configured_metadata,
        envelope,
    )

    configuration: dict = {"status": "unavailable"}
    try:
        with config_path().open("rb") as stream:
            data = stream.read(128 * 1024 + 1)
        if len(data) > 128 * 1024:
            configuration = {"status": "too-large"}
        else:
            configuration = {
                "status": "collected",
                **configured_metadata(tomllib.loads(data.decode())),
            }
    except (ValueError, UnicodeError, RecursionError):
        configuration = {"status": "invalid"}
    except OSError:
        pass
    producer, health, runtime, player, queries, options = _metadata_producer(session)
    payload = envelope(
        collector=build_identity(),
        producer=producer,
        configuration=configuration,
        health=health,
        runtime=runtime,
        player=player,
        queries=queries,
        options=options,
    )
    return {
        "diagnostics/envelope.json": json.dumps(payload, ensure_ascii=False, indent=2),
        "MANIFEST.txt": (
            "Local metadata-only diagnostics; NEVER uploaded by saitenka.\n"
            "No raw configuration, logs, traces, crash text, paths, cue text or pixels.\n"
            "Review before sharing. Exported bundles do not expire automatically.\n"
            "Contents: diagnostics/envelope.json\n"
        ),
    }


def _summary_health(raw: dict) -> dict:
    from saitenka.app.color_evidence import safe_color_metrics
    from saitenka.app.report_schema import count, operation_health

    health = operation_health(raw)
    if raw.get("sampled"):
        health.update(status="partial", sampled=True)
    if raw.get("diagnostic_loss"):
        health.update(status="partial", diagnostic_loss=count(raw["diagnostic_loss"]))
    health["subtitle_color"] = safe_color_metrics(raw.get("subtitle_color_metrics"))
    return health


def _metadata_producer(session: str | None) -> tuple[dict, dict, dict, dict, dict, dict]:
    from saitenka.app.option_evidence import safe_snapshot as safe_options
    from saitenka.app.paths import cache_dir
    from saitenka.app.player_evidence import safe_snapshot
    from saitenka.app.query_evidence import safe_snapshot as safe_queries
    from saitenka.app.render_evidence import safe_runtime_configuration
    from saitenka.app.report_schema import SCHEMA_VERSION, count, safe_identity

    producer: dict = {"status": "not-collected"}
    health: dict = {"status": "not-collected"}
    runtime: dict = {"status": "not-collected"}
    player: dict = {"status": "not-collected"}
    queries: dict = {"status": "not-collected"}
    options: dict = {"status": "not-collected"}
    if session is not None and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session):
        path = cache_dir() / "diagnostics" / f"session-{session}.json"
        try:
            _, raw = _diagnostic_json(path)
            health = {"status": "unavailable"}
            if raw.get("session") != session:
                producer = {"status": "session-mismatch"}
            elif type(raw.get("schema")) is not int or raw["schema"] != SCHEMA_VERSION:
                producer = {"status": "unsupported-schema"}
            else:
                producer = {
                    "status": "collected",
                    "identity": safe_identity(raw.get("producer")),
                    "captured_ns": count(raw.get("captured_ns")),
                    "clock": "unix-nanoseconds",
                    "session": "session-1",
                    "end": "shutdown-observed"
                    if raw.get("end") == "shutdown-observed"
                    else "unknown",
                }
                health = _summary_health(raw)
                runtime = safe_runtime_configuration(raw.get("runtime_configuration"))
                player = safe_snapshot(raw.get("player_configuration"))
                queries = safe_queries(raw.get("player_queries"))
                options = safe_options(raw.get("session_configuration"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
            producer = {"status": "unreadable-or-too-large"}
            health = {"status": "unavailable"}
    return producer, health, runtime, player, queries, options


def _manifest(members: dict[str, str], *, include_log: bool, session: str | None = None) -> str:
    lines = [
        f"saitenka {_overlay_version()} diagnostics bundle",
        f"generated: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"latest session: {session or 'n/a (log predates session ids)'}",
        "",
        "PRIVACY — read before sharing:",
        "  • API keys / tokens have been redacted from the config and log.",
        "  • This bundle is created locally and is NEVER uploaded anywhere by saitenka.",
        "  • It DOES include your config, mpv.conf/input.conf, and (unless --no-log) the logs,",
        "    which may contain video filenames and mined sentences. Home paths contain your username.",
        "  • If mpv crashed, it includes mpv's own native crash report (crashes/player/) — process",
        "    and loaded-library detail about your machine, no saitenka data.",
        "  • It does NOT include dictionaries, your Anki collection, videos, or other scripts' code.",
        "",
        f"log included: {'yes' if include_log else 'no (--no-log)'}",
        "",
        "contents:",
    ]
    lines += [f"  - {name}" for name in members if name != "MANIFEST.txt"]
    return "\n".join(lines) + "\n"


def build_report_bundle(
    dest_dir: str | Path | None = None,
    *,
    include_log: bool = True,
    diagnostic_detail: bool = False,
    timestamp: str | None = None,
    attachments: tuple[Path, ...] = (),
) -> Path:
    """Write the diagnostics zip and return its path. ``dest_dir`` defaults to a dedicated reports dir
    under the platform data dir (``%LOCALAPPDATA%\\saitenka\\reports`` on Windows) instead of cluttering
    the home root; a ``timestamp`` (``YYYYMMDD-HHMMSS``) can be injected for deterministic tests."""
    from saitenka.app.paths import data_dir
    from saitenka.app.report_attachments import collect_attachments
    from saitenka.app.report_reader import MAX_ARCHIVE_BYTES

    ts = timestamp or time.strftime("%Y%m%d-%H%M%S")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ts):
        raise ValueError("report timestamp must be a bounded filename component")
    base = Path(dest_dir).expanduser() if dest_dir else data_dir() / "reports"
    base.mkdir(parents=True, exist_ok=True)
    dest = base / f"saitenka-report-{ts}.zip"
    members = collect(include_log=include_log, diagnostic_detail=diagnostic_detail)
    attachment_members = collect_attachments(attachments)
    if attachment_members:
        members["MANIFEST.txt"] = (
            "Local diagnostic export with UNREDACTED user attachments.\n"
            "Attachment inventory and privacy: attachments/manifest.json\n"
            "Never uploaded automatically. User-owned exports never expire.\n"
            "Review all contents before sharing.\n"
        )
    size = sum(len(content.encode("utf-8")) for content in members.values()) + sum(
        len(content) for content in attachment_members.values()
    )
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError("diagnostic archive exceeds expanded byte limit")
    fd, temporary_name = tempfile.mkstemp(prefix=".saitenka-report-", suffix=".tmp", dir=base)
    temporary = Path(temporary_name)
    try:
        with (
            os.fdopen(fd, "w+b") as stream,
            zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zf,
        ):
            for name, content in members.items():
                zf.writestr(name, content)
            for name, attachment in attachment_members.items():
                zf.writestr(name, attachment)
        # A timestamp collision must not silently destroy an earlier user-owned bundle.
        os.link(temporary, dest)
    finally:
        temporary.unlink(missing_ok=True)
    return dest
