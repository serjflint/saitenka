"""Tests for the structlog pipeline (saitenka.app.logsetup): the redaction processor and the
stdlib logging -> structlog bridge that every ``logging.getLogger(__name__)`` call site relies on.
"""

from __future__ import annotations

import json
import logging

from hypothesis import given
from hypothesis import strategies as st

from saitenka.app import doctor as doc
from saitenka.app.logsetup import (
    CONSOLE_LOGGER_NAME,
    _add_session,
    _drop_session,
    _redact_event_dict,
    configure_logging,
    user_facing_logger,
)


def _configure(tmp_path):
    """Fresh root logger per test — configure_logging is idempotent (returns early once handlers
    are attached), so each test needs its own unhandled "saitenka" logger. The user-facing child is
    cleared too: its handler is attached separately and would otherwise stack across tests."""
    for name in ("saitenka", CONSOLE_LOGGER_NAME):
        logger = logging.getLogger(name)
        for h in list(logger.handlers):
            logger.removeHandler(h)
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


def test_stdlib_bridge_preserves_level_and_message(tmp_path):
    log_path = _configure(tmp_path)
    log = logging.getLogger("saitenka.test")
    log.error("boom happened")

    (record,) = [d for d in _lines(log_path) if d["event"] == "boom happened"]
    assert record["level"] == "error"


def test_exception_info_lands_in_json(tmp_path):
    log_path = _configure(tmp_path)
    log = logging.getLogger("saitenka.test")
    try:
        1 / 0  # noqa: B018  # deliberately raises, to exercise exc_info capture
    except ZeroDivisionError:
        log.exception("failed")

    (record,) = [d for d in _lines(log_path) if d["event"] == "failed"]
    assert "ZeroDivisionError" in record["exception"]


def test_session_is_stamped_on_the_json_file_log(tmp_path):
    log_path = _configure(tmp_path)
    logging.getLogger("saitenka.test").warning("hello")

    (record,) = [d for d in _lines(log_path) if d["event"] == "hello"]
    assert record.get("session")  # file lines carry the session for report run-attribution


def test_console_processor_drops_session_but_file_processor_keeps_it():
    """The session is quoted once at launch; it stays in the JSON file (report attribution) but is
    stripped from the human-readable console line so it isn't repeated on every stderr warning."""
    stamped = _add_session(None, "warning", {"event": "x"})
    assert stamped.get("session")

    assert "session" not in _drop_session(None, "warning", dict(stamped))


def test_a_user_facing_line_reaches_the_terminal_and_the_file(tmp_path, capsys):
    """The banner and the session summary were `print` calls precisely because the stderr handler is
    WARNING. That made them invisible to `overlay.log`, so a report bundle never carried them."""
    log_path = _configure(tmp_path)
    user_facing_logger().info("runtime: %s · %d prefetch worker(s)", "GIL", 4)

    assert capsys.readouterr().err.strip() == "[saitenka] runtime: GIL · 4 prefetch worker(s)"
    (record,) = [d for d in _lines(log_path) if d["event"].startswith("runtime:")]
    assert record["level"] == "info"


def test_a_user_facing_line_is_printed_once(tmp_path, capsys):
    """The record propagates to the root's file handler; the root's stderr handler must not take it
    as well, or every banner appears twice — once plain, once log-rendered."""
    _configure(tmp_path)
    user_facing_logger().info("session: 12 cues")

    assert capsys.readouterr().err.count("session: 12 cues") == 1


def test_an_ordinary_info_line_stays_off_the_terminal(tmp_path, capsys):
    """Negative control: this is a channel for the two lines addressed to the user, not a global
    console level. Without it the same test passes for any change that just lowers `sh`."""
    _configure(tmp_path)
    logging.getLogger("saitenka.test").info("cache warmed")

    assert "cache warmed" not in capsys.readouterr().err


def test_a_user_facing_line_is_redacted(tmp_path, capsys):
    """What the print bypassed. `no-print-in-lib` names this as the reason it exists."""
    _configure(tmp_path)
    user_facing_logger().info("session: token=%s", "abcdef0123456789")

    assert "abcdef0123456789" not in capsys.readouterr().err


def test_doctor_recent_errors_tails_json_log(tmp_path, monkeypatch):
    log_path = _configure(tmp_path)
    log = logging.getLogger("saitenka.test")
    log.warning("fetch failed")
    log.error("auth failed")

    monkeypatch.setattr(doc, "LOG_PATH", log_path)
    c = doc.check_recent_errors()
    assert c.status == "warn"
    assert "fetch failed" in c.detail and "auth failed" in c.detail
