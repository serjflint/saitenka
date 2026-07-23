"""Automatic crash capture — the three layers that together catch everything:

1. ``sys.excepthook`` — uncaught main-thread exceptions.
2. ``threading.excepthook`` (3.8+) — uncaught exceptions in worker threads (the IPC reader + prefetch
   workers), which ``sys.excepthook`` never sees, so they'd otherwise die silently.
3. ``faulthandler`` — C-level crashes (fugashi/Pillow segfaults) and a low-level traceback even when
   the interpreter is corrupted.

Privacy (Crashpad/Sentry/GDPR practice): crash reports are written **locally only and NEVER uploaded** —
collection is separated from transmission, so the user chooses to share via ``saitenka-overlay report``
(which bundles them). Secrets in argv / the log tail are redacted first, and old reports are pruned.
"""

from __future__ import annotations

import faulthandler
import platform
import sys
import threading
import time
import traceback
from pathlib import Path

_LOG_TAIL = 60  # lines of the overlay log to attach as context
_KEEP = 20  # cap stored crash reports (retention)
_SECRET_FLAGS = {"--jimaku-key", "--jimaku_key"}

_installed = False
_fault_fp = None  # keep the faulthandler file handle alive for the process lifetime


def crash_dir() -> Path:
    from overlay.app.paths import cache_dir

    return cache_dir() / "crashes"


def _redacted_command() -> str:
    """``sys.argv`` with the value after a secret flag (``--jimaku-key X``) and any inline secret
    scrubbed, so a crash log never captures the key."""
    from overlay.app.report import _redact_secrets

    parts: list[str] = []
    redact_next = False
    for a in sys.argv:
        if redact_next:
            parts.append("<redacted>")
            redact_next = False
        elif a in _SECRET_FLAGS:
            parts.append(a)
            redact_next = True
        else:
            parts.append(a)
    return _redact_secrets(" ".join(parts))


def _log_tail() -> str:
    from overlay.app.doctor import LOG_PATH
    from overlay.app.report import _redact_secrets

    if not LOG_PATH.exists():
        return "(no log)"
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-_LOG_TAIL:]
    except OSError:  # pragma: no cover
        return "(log unreadable)"
    return _redact_secrets("\n".join(lines))


def _perf_summary() -> str:
    """Recent op-latency percentiles + current RSS leading up to the crash — was the overlay janking
    or memory-heavy right before it died? Empty (no section) when nothing has been recorded yet (e.g.
    crash during startup)."""
    from overlay.app.perf import rss_mb, snapshot

    snap = snapshot()
    rss = rss_mb()
    if not snap and rss is None:
        return ""
    lines = [
        f"{op}: n={s['n']:.0f} p50={s['p50']:.1f}ms p95={s['p95']:.1f}ms max={s['max']:.1f}ms"
        for op, s in snap.items()
    ]
    if rss is not None:
        lines.append(f"rss: {rss:.0f}MB")
    return "\n--- recent op timings ---\n" + "\n".join(lines) + "\n"


def write_report(kind: str, tb_text: str, thread: str | None = None) -> Path:
    """Write one crash report (redacted) and prune old ones. Returns its path."""
    from overlay import __version__

    d = crash_dir()
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = d / f"crash-{ts}{('-' + thread) if thread else ''}.log"
    header = [
        f"saitenka-overlay {__version__} — crash ({kind})",
        f"time: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"platform: {platform.platform()}",
        f"python: {sys.version.split()[0]}",
        f"command: {_redacted_command()}",
    ]
    if thread:
        header.append(f"thread: {thread}")
    from overlay.app.report import redact

    body = (
        "\n".join(header)
        + "\n\n--- traceback ---\n"
        + tb_text
        + _perf_summary()
        + "\n--- recent log ---\n"
        + _log_tail()
        + "\n"
    )
    # Redact the WHOLE report (secrets + home/username in tracebacks/paths) — it may be shared as-is.
    path.write_text(redact(body), encoding="utf-8", newline="\n")
    _prune(d)
    return path


def _prune(d: Path) -> None:
    for stale in sorted(d.glob("crash-*.log"))[:-_KEEP]:
        stale.unlink(missing_ok=True)


def _notify(path: Path) -> None:  # pragma: no cover — stderr side effect
    print(
        f"\nsaitenka crashed — details saved to {path}\n"
        "Please run `saitenka-overlay report` to bundle it for a bug report (nothing is uploaded).",
        file=sys.stderr,
    )


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):  # Ctrl+C is not a crash
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        _notify(write_report("main-thread", tb))
    except Exception:  # never let crash handling itself crash
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)  # preserve the stderr traceback + exit code


def _thread_excepthook(args) -> None:
    if issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
        return
    tb = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    name = getattr(args.thread, "name", None)
    try:
        _notify(write_report("thread", tb, thread=name))
    except Exception:
        pass


def install() -> None:
    """Install all three layers (idempotent). Call once at startup, after logging is set up."""
    global _installed, _fault_fp
    if _installed:
        return
    _installed = True
    try:
        d = crash_dir()
        d.mkdir(parents=True, exist_ok=True)
        _fault_fp = open(d / "faulthandler.log", "a", encoding="utf-8")  # noqa: SIM115 — process-lifetime
        faulthandler.enable(file=_fault_fp)
    except Exception:  # pragma: no cover — faulthandler unavailable / unwritable dir
        pass
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
