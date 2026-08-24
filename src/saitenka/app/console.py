"""The runtime's user-facing stdout — the one place core code is allowed to write to the terminal.

`logsetup.py` attaches a WARNING-level console handler, so `log.info` reaches the log file and nothing
the user watching mpv can see. A startup banner or a session summary has to go to stdout directly. This
module is that surface: the runtime calls :func:`announce` instead of carrying its own `print` plus the
two per-site suppressions (ruff T201, ast-grep `no-print-in-lib`) a bare one needs.
"""

from __future__ import annotations

PREFIX = "[saitenka]"


def announce(message: str) -> None:
    """One user-facing line on stdout. Log separately — this is presentation, not a record."""
    print(f"{PREFIX} {message}")  # this module IS the sanctioned stdout surface
