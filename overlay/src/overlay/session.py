"""A short per-process session id, stamped on every log line (overlay.log) and telemetry span
(trace.json) so a bug report can be tied to the exact run that produced it — overlay.log accumulates
across many runs, and a report is built by a *separate* process later, so without this there's no way
to tell which lines / spans belong to the session the user is complaining about."""

from __future__ import annotations

import secrets
import time

_SESSION_ID: str | None = None


def session_id() -> str:
    """Cached per-process id: a ``HHMMSS`` start-time prefix (human-correlatable with the report's
    timestamped filename) plus a random suffix (collision-safe across concurrent runs). Stable for the
    life of the process; first call fixes it."""
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = f"{time.strftime('%H%M%S')}-{secrets.token_hex(2)}"
    return _SESSION_ID
