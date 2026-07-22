"""The ASCII, TTY-gated first-run build progress bar."""

from __future__ import annotations

import io

from overlay.app import progress


def test_format_bar_basic():
    s = progress.format_bar(0, 4, "JMdict")
    assert s.startswith("building [")
    assert "0%" in s and "1/4" in s and "JMdict" in s


def test_format_bar_subprogress_smooths_fill():
    s = progress.format_bar(2, 4, "Dict", 5, 10)  # (2 + 0.5) / 4 = 62%
    assert " 62%" in s and "3/4" in s and "bank 5/10" in s
    assert "#" in s and "-" in s  # partially filled


def test_format_bar_complete_is_fully_filled():
    s = progress.format_bar(4, 4, "")
    assert "100%" in s and "4/4" in s
    assert "-" not in s.split("]")[0]  # the bar segment has no empty cells


def test_format_bar_truncates_long_name():
    s = progress.format_bar(0, 1, "x" * 100)
    assert "x" * 30 in s and "x" * 31 not in s  # capped at 30 chars


class _Fake(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_buildbar_draws_on_tty():
    tty = _Fake(True)
    bar = progress.BuildBar(out=tty)
    bar.update(0, 4, "D")  # 0 done → working on the 1st of 4
    assert tty.getvalue().startswith("\r") and "1/4" in tty.getvalue()
    bar.close()
    assert tty.getvalue().endswith("\r")  # line cleared


def test_buildbar_silent_when_not_a_tty():
    notty = _Fake(False)
    bar = progress.BuildBar(out=notty)
    bar.update(1, 4, "D")
    bar.close()
    assert notty.getvalue() == ""  # piped / plugin-mode: no carriage-return spam
