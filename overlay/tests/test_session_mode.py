"""The ``session: mode=…`` log line each entry point emits before the reader loop.

A bundled ``report`` needs to know which entry point produced a trace — run/attach/plugin behave
differently (async dep load, other mpv scripts sharing input). Nothing consumes the line
programmatically, so its only contract is *that it fires*; before this, no test referenced it, so a
silent removal/rename was invisible. Run-mode is smoke-testable at its seam here; attach mode connects
to a live mpv (``cli.attach`` is ``# pragma: no cover``) and is left to the live tier."""

from __future__ import annotations

import logging

from overlay.app import cli_run


class _FakeReader:
    """Just enough Reader for the non-demo ``run`` branch: ``run()`` stands in for the blocking loop."""

    def __init__(self) -> None:
        self.ran = False

    def run(self) -> None:
        self.ran = True


def test_run_session_logs_mode_run_before_the_loop(caplog):
    reader = _FakeReader()
    with caplog.at_level(logging.INFO, logger="overlay.app.cli_run"):
        cli_run._execute_reader_session(
            reader,
            ipc=None,
            demo=cli_run.DemoSpec(),  # no demo_word / screenshot → the real interactive branch
            video=None,
            translate_key="t",
        )
    assert reader.ran  # the branch under test is the one that actually enters the loop
    assert "session: mode=run" in caplog.text
