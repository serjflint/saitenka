"""Tests for the structlog pipeline (overlay.app.logsetup): the redaction processor and the
stdlib logging -> structlog bridge that every ``logging.getLogger(__name__)`` call site relies on.
"""

from __future__ import annotations

import json
import logging

from hypothesis import given
from hypothesis import strategies as st

from overlay.app import doctor as doc
from overlay.app.logsetup import _redact_event_dict, configure_logging


def _configure(tmp_path, monkeypatch):
    """Fresh root logger per test — configure_logging is idempotent (returns early once handlers
    are attached), so each test needs its own unhandled "overlay" logger."""
    root = logging.getLogger("overlay")
    for h in list(root.handlers):
        root.removeHandler(h)
    log_path = tmp_path / "overlay.log"
    configure_logging(log_path)
    return log_path


def _lines(log_path):
    return [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln]


@given(st.text(alphabet="abcdefABCDEF0123456789-_.", min_size=6, max_size=48))
def test_redaction_processor_scrubs_secret_from_event_dict(secret):
    event_dict = _redact_event_dict(
        None, "warning", {"event": f"auth failed token={secret}", "url": f"key={secret}"}
    )
    assert secret not in event_dict["event"]
    assert secret not in event_dict["url"]
    assert "<redacted>" in event_dict["event"] and "<redacted>" in event_dict["url"]


def test_stdlib_bridge_preserves_level_and_message(tmp_path, monkeypatch):
    log_path = _configure(tmp_path, monkeypatch)
    log = logging.getLogger("overlay.test")
    log.error("boom happened")

    (record,) = [d for d in _lines(log_path) if d["event"] == "boom happened"]
    assert record["level"] == "error"


def test_exception_info_lands_in_json(tmp_path, monkeypatch):
    log_path = _configure(tmp_path, monkeypatch)
    log = logging.getLogger("overlay.test")
    try:
        1 / 0  # noqa: B018 - deliberately triggers ZeroDivisionError for exc_info capture
    except ZeroDivisionError:
        log.exception("failed")

    (record,) = [d for d in _lines(log_path) if d["event"] == "failed"]
    assert "ZeroDivisionError" in record["exception"]


def test_doctor_recent_errors_tails_json_log(tmp_path, monkeypatch):
    log_path = _configure(tmp_path, monkeypatch)
    log = logging.getLogger("overlay.test")
    log.warning("fetch failed")
    log.error("auth failed")

    monkeypatch.setattr(doc, "LOG_PATH", log_path)
    c = doc.check_recent_errors()
    assert c.status == "warn"
    assert "fetch failed" in c.detail and "auth failed" in c.detail
