"""The ``session: mode=…`` log line each entry point emits before the reader loop.

A bundled ``report`` needs to know which entry point produced a trace — run/attach/plugin behave
differently (async dep load, other mpv scripts sharing input). Nothing consumes the line
programmatically, so its only contract is *that it fires*; before this, no test referenced it, so a
silent removal/rename was invisible. Run-mode is smoke-testable at its seam here; attach mode connects
to a live mpv (``cli.attach`` is ``# pragma: no cover``) and is left to the live tier."""

from __future__ import annotations

import logging
import threading

import pytest

from saitenka.app.launch import run as cli_run
from saitenka.app.tokenize import Token


class _FakeReader:
    """Just enough Reader for the non-demo ``run`` branch: ``run()`` stands in for the blocking loop."""

    def __init__(self) -> None:
        self.ran = False

    def run(self) -> None:
        self.ran = True


def test_run_session_logs_mode_run_before_the_loop(caplog):
    reader = _FakeReader()
    with caplog.at_level(logging.INFO, logger="saitenka.app.launch.run"):
        cli_run._execute_reader_session(
            reader,
            ipc=None,
            demo=cli_run.DemoSpec(),  # no demo_word / screenshot → the real interactive branch
            video=None,
            translate_key="t",
        )
    assert reader.ran  # the branch under test is the one that actually enters the loop
    assert "session: mode=run" in caplog.text


@pytest.mark.timeout(5)
def test_demo_waits_for_annotation_before_hovering_or_capturing(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class Reader:
        osd = (1280, 720)

        def __init__(self) -> None:
            self.tokens = []
            self.hovered = None

        def refresh_osd(self) -> None:
            pass

        def _get(self, _name):
            return "猫"

        def prepare_subtitle_blocking(self, _text: str) -> None:
            started.set()
            assert release.wait(2)
            self.tokens = [Token("猫", "猫", "猫", "名詞", 0, 1)]

        def set_hover(self, index: int) -> None:
            self.hovered = index

        def _mark_interactive_ready(self) -> None:
            assert self.tokens and self.hovered == 0
            self.ready = True

    reader = Reader()
    monkeypatch.setattr(cli_run.time, "sleep", lambda _seconds: None)
    thread = threading.Thread(
        target=cli_run._execute_reader_session,
        args=(reader, None, cli_run.DemoSpec(demo_word="猫")),
        kwargs={"video": None, "translate_key": "t"},
    )
    thread.start()
    assert started.wait(1)
    assert reader.tokens == [] and reader.hovered is None

    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert reader.hovered == 0
    assert reader.ready is True
