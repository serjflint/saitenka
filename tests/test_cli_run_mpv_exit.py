"""``_log_mpv_exit`` (launch/run.py): mpv's own crash (e.g. a GPU-driver SIGSEGV) drops the IPC socket
the same way a clean quit does — this is the one place that checks the real exit code (``proc.
returncode``) so a crash shows up in overlay.log/report instead of reading as a normal quit."""

import logging

import pytest

from saitenka.app.launch.run import _log_mpv_exit


@pytest.mark.parametrize("returncode", [0, None], ids=["clean-quit", "unset-after-force-kill"])
def test_silent_on_non_failure_exit(caplog, returncode):
    with caplog.at_level(logging.WARNING):
        _log_mpv_exit(returncode)
    assert caplog.records == []


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(-11, ("signal 11", "SIGSEGV")), (1, ("non-zero status 1",))],
    ids=["crash-signal", "nonzero-status"],
)
def test_reports_mpv_failure(caplog, returncode, expected):
    with caplog.at_level(logging.WARNING):
        _log_mpv_exit(returncode)
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert all(fragment in msg for fragment in expected)
