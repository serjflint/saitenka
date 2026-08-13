"""``_log_mpv_exit`` (launch/run.py): mpv's own crash (e.g. a GPU-driver SIGSEGV) drops the IPC socket
the same way a clean quit does — this is the one place that checks the real exit code (``proc.
returncode``) so a crash shows up in overlay.log/report instead of reading as a normal quit."""

import logging

from saitenka.app.launch.run import _log_mpv_exit


def test_silent_on_clean_quit(caplog):
    with caplog.at_level(logging.WARNING):
        _log_mpv_exit(0)
    assert caplog.records == []


def test_silent_when_returncode_unset():
    # proc.wait() never completed (the TimeoutExpired/force-kill branch) — nothing to report; that
    # exit is ours, not mpv's.
    _log_mpv_exit(None)  # must not raise


def test_decodes_the_crash_signal(caplog):
    with caplog.at_level(logging.WARNING):
        _log_mpv_exit(-11)  # SIGSEGV
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "signal 11" in msg and "SIGSEGV" in msg


def test_reports_nonzero_status(caplog):
    with caplog.at_level(logging.WARNING):
        _log_mpv_exit(1)
    assert len(caplog.records) == 1
    assert "non-zero status 1" in caplog.records[0].getMessage()
