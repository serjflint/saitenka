"""A tiny, dependency-free progress bar for the first-run dictionary index build.

That build is the one genuinely slow, disk-heavy startup step — each Yomitan dict is 25–66s of JSON
parse + SQLite writes, and a full rig has a dozen+ of them — so without feedback it looks hung. This
renders a single carriage-return-updated line that advances per source AND per term/kanji bank within
a source (so a long single-dict build still moves).

Deliberately minimal: **ASCII only** (the classic Windows console mangles block glyphs and ANSI — see
doctor.py), and it draws **only when stdout is a TTY**. Piped or plugin-mode (GUI mpv) runs get
nothing — there's no console there, and the in-mpv overlay shows its own loading spinner.
"""

from __future__ import annotations

import sys


def format_bar(
    done: int, total: int, name: str, sub_done: int = 0, sub_total: int = 0, width: int = 24
) -> str:
    """The bar as a plain string (no ``\\r``, no IO — pure, so it's unit-testable). ``done`` sources are
    fully finished; ``sub_done``/``sub_total`` is progress *within* the source now building, which
    smooths the fill between whole-source steps."""
    total = max(1, total)
    sub = (sub_done / sub_total) if sub_total else 0.0
    frac = min(1.0, max(0.0, (done + sub) / total))
    filled = round(frac * width)
    bar = "#" * filled + "-" * (width - filled)
    idx = min(done + 1, total)  # 1-based "which one are we on"
    tail = f"  bank {sub_done}/{sub_total}" if sub_total else ""
    return f"building [{bar}] {int(frac * 100):3d}%  {idx}/{total}  {name[:30]}{tail}"


class BuildBar:
    """Stateful renderer bound to an output stream. No-op unless ``out`` is a TTY."""

    _WIDTH = 79  # pad every line to a fixed width so a shorter line never leaves stale chars

    def __init__(self, out=None, width: int = 24):
        self.out = out if out is not None else sys.stdout
        self.width = width
        self.enabled = bool(getattr(self.out, "isatty", lambda: False)())

    def update(
        self, done: int, total: int, name: str, sub_done: int = 0, sub_total: int = 0
    ) -> None:
        if not self.enabled:
            return
        line = format_bar(done, total, name, sub_done, sub_total, self.width)
        self.out.write("\r" + line.ljust(self._WIDTH)[: self._WIDTH])
        self.out.flush()

    def close(self) -> None:
        """Erase the bar line so the following normal output starts clean."""
        if not self.enabled:
            return
        self.out.write("\r" + " " * self._WIDTH + "\r")
        self.out.flush()
