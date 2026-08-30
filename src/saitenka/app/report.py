"""``saitenka report`` — a single timestamped diagnostics zip for bug reports.

Replaces pasting a terminal transcript by hand. It is **user-invoked, local-only (never uploaded),
and redacted**: API keys are scrubbed, a MANIFEST lists exactly what's inside, and the CLI tells you to
review before sharing. What goes in is scoped by privacy tier (see MANIFEST): safe diagnostics always;
the log (which may hold video filenames / mined sentences) is included by default but ``--no-log`` opts
out; secrets are removed; dictionaries / Anki / video / third-party script bodies are never included.
"""

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

    out = text.replace(str(Path.home()), "<HOME>")
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
    members = {"doctor.json": doctor_json}
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
        for pattern in ("crash-*.log", "shutdown-hang-*.log"):
            for c in sorted(cd.glob(pattern))[-5:]:
                members[f"crashes/{c.name}"] = c.read_text(encoding="utf-8", errors="replace")
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


def _collect_telemetry() -> dict[str, str]:
    """The CTF trace file the LIVE overlay session wrote to disk, if telemetry was enabled for it —
    `report` runs in its own short-lived process, so it can only see what made it to disk, not the
    in-memory metrics snapshot of a session that has already exited (metrics stay pull-based /
    process-local by design, see otel_metrics.snapshot)."""
    from saitenka.app.config import load_config, resolve_telemetry
    from saitenka.app.telemetry import export_dir, latest_trace

    trace_path = latest_trace(
        export_dir(resolve_telemetry(load_config()))
    )  # newest per-session trace
    if trace_path is None or not trace_path.exists():
        return {}
    return {  # stable member name inside the zip, regardless of the on-disk timestamped filename
        "telemetry/trace.json": redact(trace_path.read_text(encoding="utf-8", errors="replace"))
    }


def _latest_session(log_text: str) -> str | None:
    """The session id of the most recent run in the (JSON-lines) overlay log — the run a fresh report
    is almost always about. None if the log predates session stamping."""
    for line in reversed(log_text.splitlines()):
        try:
            sid = json.loads(line).get("session")
        except (ValueError, AttributeError):
            continue
        if sid:
            return str(sid)
    return None


def collect(*, include_log: bool = True) -> dict[str, str]:
    """Build ``{archive_member: text}`` for the bundle — all text, all redacted. Pure enough to test
    with fake homes (it only reads files + runs read-only version/doctor checks)."""
    from saitenka.app.paths import cache_dir

    log_path = cache_dir() / "overlay.log"  # resolve dynamically (respects $SAITENKA_CACHE_DIR)

    members: dict[str, str] = {}
    members.update(_collect_versions())
    members.update(_collect_doctor())
    members.update(_collect_config())
    members.update(_collect_mpv_conf())
    members.update(_collect_scripts())

    # The rotating overlay log — redacted; opt-out via --no-log (may contain filenames/sentences).
    session: str | None = None
    if include_log and log_path.exists():
        members["overlay.log"] = redact(log_path.read_text(encoding="utf-8", errors="replace"))
        session = _latest_session(members["overlay.log"])

    # mpv's own log (run launches mpv with --log-file) — the codec / sub-load / track-select side that
    # the overlay log can't see. Redacted + gated like the overlay log (it holds the video path).
    mpv_log = cache_dir() / "mpv.log"
    if include_log and mpv_log.exists():
        members["mpv.log"] = redact(mpv_log.read_text(encoding="utf-8", errors="replace"))

    members.update(_collect_dict_inventory())
    members.update(_collect_crashes())
    members.update(_collect_player_crashes())
    members.update(_collect_telemetry())

    members["MANIFEST.txt"] = _manifest(members, include_log=include_log, session=session)
    return members


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
    timestamp: str | None = None,
) -> Path:
    """Write the diagnostics zip and return its path. ``dest_dir`` defaults to a dedicated reports dir
    under the platform data dir (``%LOCALAPPDATA%\\saitenka\\reports`` on Windows) instead of cluttering
    the home root; a ``timestamp`` (``YYYYMMDD-HHMMSS``) can be injected for deterministic tests."""
    from saitenka.app.paths import data_dir

    ts = timestamp or time.strftime("%Y%m%d-%H%M%S")
    base = Path(dest_dir).expanduser() if dest_dir else data_dir() / "reports"
    base.mkdir(parents=True, exist_ok=True)
    dest = base / f"saitenka-report-{ts}.zip"
    members = collect(include_log=include_log)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return dest
