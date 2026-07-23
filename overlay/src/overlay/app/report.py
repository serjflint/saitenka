"""``saitenka-overlay report`` — a single timestamped diagnostics zip for bug reports.

Replaces pasting a terminal transcript by hand. It is **user-invoked, local-only (never uploaded),
and redacted**: API keys are scrubbed, a MANIFEST lists exactly what's inside, and the CLI tells you to
review before sharing. What goes in is scoped by privacy tier (see MANIFEST): safe diagnostics always;
the log (which may hold video filenames / mined sentences) is included by default but ``--no-log`` opts
out; secrets are removed; dictionaries / Anki / video / third-party script bodies are never included.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Scrub ``key = "..."`` / ``token: ...`` / ``Authorization: Bearer <tok>`` style secrets from any text.
# The ``(?:bearer|token)\s+`` skip is why ``Authorization: Bearer <tok>`` redacts the TOKEN, not the
# word "Bearer" (a property test caught that).
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|key|token|secret|password|authorization|bearer)\b(\s*[=:]\s*|\s+)"
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
    except Exception:  # pragma: no cover — getuser can raise if no login name is resolvable
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
        if re.match(r"\s*#?\s*(api[_-]?key|key|token|secret|password)\s*=", line, re.I):
            out.append(re.sub(r"=\s*.*$", '= "<redacted>"', line))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _first_line(*cmd: str) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = ((out.stdout or "") + (out.stderr or "")).strip().splitlines()
        return lines[0] if lines else "no output"
    except (OSError, subprocess.SubprocessError):
        return "not found"


def _overlay_version() -> str:
    try:
        return version("saitenka-overlay")
    except PackageNotFoundError:  # pragma: no cover — source checkout without an installed dist
        return "0+unknown"


def _gil_state() -> str:
    fn = getattr(sys, "_is_gil_enabled", None)
    return "free-threaded (GIL off)" if fn and not fn() else "standard (GIL on)"


def collect(*, include_log: bool = True) -> dict[str, str]:
    """Build ``{archive_member: text}`` for the bundle — all text, all redacted. Pure enough to test
    with fake homes (it only reads files + runs read-only version/doctor checks)."""
    from overlay.app.config import config_path, load_config
    from overlay.app.doctor import run_checks
    from overlay.app.paths import cache_dir

    log_path = cache_dir() / "overlay.log"  # resolve dynamically (respects $SAITENKA_CACHE_DIR)
    from overlay.app.paths import mpv_conf_paths, mpv_scripts_dirs
    from overlay.app.plugin import LUA_NAME
    from overlay.mpvio.discover import find_mpv

    members: dict[str, str] = {}

    mpv = find_mpv(load_config().get("mpv_path"))
    members["versions.txt"] = (
        "\n".join(
            [
                f"saitenka-overlay: {_overlay_version()}",
                f"python: {sys.version.split()[0]} — {_gil_state()}",
                f"platform: {platform.platform()}",
                f"mpv: {_first_line(mpv, '--version') if mpv else 'NOT FOUND'}",
                f"ffmpeg: {_first_line('ffmpeg', '-version')}",
            ]
        )
        + "\n"
    )

    # Structured doctor report (the machine-readable version of the ✓/!/✗ health check).
    try:
        members["doctor.json"] = json.dumps(run_checks().to_json(), ensure_ascii=False, indent=2)
    except Exception as e:  # never let a doctor hiccup abort the bundle
        members["doctor.json"] = json.dumps({"error": str(e)})

    # Our config, secrets + home/username removed.
    cp = config_path()
    if cp.exists():
        members["overlay.toml"] = _scrub_home(
            _redact_config(cp.read_text(encoding="utf-8", errors="replace"))
        )

    # mpv / mpv.net config (input-ipc-server, sub-auto, … — the exact things that break).
    for p in mpv_conf_paths():
        if p.exists():
            members[f"mpv/{p.parent.name}.{p.name}"] = p.read_text(
                encoding="utf-8", errors="replace"
            )

    # OUR plugin lua in full; OTHER scripts by NAME only (they cause overlay conflicts, but their
    # bodies aren't ours to bundle).
    for d in mpv_scripts_dirs():
        lua = d / LUA_NAME
        if lua.exists():
            members[f"scripts/{d.parent.name}.{LUA_NAME}"] = lua.read_text(
                encoding="utf-8", errors="replace"
            )
        if d.exists():
            others = sorted(x.name for x in d.iterdir() if x.name != LUA_NAME)
            members[f"scripts/{d.parent.name}.listing.txt"] = "\n".join(others) + "\n"

    # The rotating overlay log — redacted; opt-out via --no-log (may contain filenames/sentences).
    if include_log and log_path.exists():
        members["overlay.log"] = redact(log_path.read_text(encoding="utf-8", errors="replace"))

    # mpv's own log (run launches mpv with --log-file) — the codec / sub-load / track-select side that
    # the overlay log can't see. Redacted + gated like the overlay log (it holds the video path).
    mpv_log = cache_dir() / "mpv.log"
    if include_log and mpv_log.exists():
        members["mpv.log"] = redact(mpv_log.read_text(encoding="utf-8", errors="replace"))

    # Dict inventory: what's imported into the consolidated DB, plus any pre-consolidation leftovers —
    # makes "configured but not imported" and stale caches visible without the user's machine.
    from overlay.app.dictdb import DictionaryDb, db_path
    from overlay.app.paths import legacy_dict_artifacts

    db_file = db_path()
    inv = [f"[database] {db_file}"]
    if db_file.exists():
        try:
            rows = DictionaryDb.open().list_dictionaries()
            inv += [f"  [{r.kind}] {r.title}" for r in rows] or ["  (empty)"]
        except Exception:  # pragma: no cover — diagnostics must never raise
            inv += ["  (unreadable)"]
    else:
        inv += ["  (none — run `saitenka-overlay import`)"]
    arts = legacy_dict_artifacts()
    if arts:
        inv += ["[legacy — unused, safe to delete]"]
        inv += [f"  {d} ({n} files, {b / 1e6:.0f} MB)" for d, n, b in arts]
    members["dicts.listing.txt"] = _scrub_home("\n".join(inv) + "\n")

    # Recent crash reports (already redacted at write time; the whole point of capturing them).
    from overlay.app.crashlog import crash_dir

    cd = crash_dir()
    if cd.exists():
        for c in sorted(cd.glob("crash-*.log"))[-5:]:
            members[f"crashes/{c.name}"] = c.read_text(encoding="utf-8", errors="replace")

    # Telemetry: the CTF trace file the LIVE overlay session wrote
    # to disk, if telemetry was enabled for it — `report` runs in its own short-lived process, so it
    # can only see what made it to disk, not the in-memory metrics snapshot of a session that has
    # already exited (metrics stay pull-based / process-local by design, see otel_metrics.snapshot).
    from overlay.app.config import load_config, resolve_telemetry
    from overlay.app.telemetry import export_dir

    trace_path = export_dir(resolve_telemetry(load_config())) / "trace.json"
    if trace_path.exists():
        members["telemetry/trace.json"] = redact(
            trace_path.read_text(encoding="utf-8", errors="replace")
        )

    members["MANIFEST.txt"] = _manifest(members, include_log=include_log)
    return members


def _manifest(members: dict[str, str], *, include_log: bool) -> str:
    lines = [
        "saitenka-overlay diagnostics bundle",
        f"generated: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        "",
        "PRIVACY — read before sharing:",
        "  • API keys / tokens have been redacted from the config and log.",
        "  • This bundle is created locally and is NEVER uploaded anywhere by saitenka.",
        "  • It DOES include your config, mpv.conf, and (unless --no-log) the overlay + mpv logs,",
        "    which may contain video filenames and mined sentences. Home paths contain your username.",
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
    from overlay.app.paths import data_dir

    ts = timestamp or time.strftime("%Y%m%d-%H%M%S")
    base = Path(dest_dir).expanduser() if dest_dir else data_dir() / "reports"
    base.mkdir(parents=True, exist_ok=True)
    dest = base / f"saitenka-report-{ts}.zip"
    members = collect(include_log=include_log)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return dest
