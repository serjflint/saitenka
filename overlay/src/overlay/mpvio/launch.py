"""mpv launch — the pure argv construction, split out of ``cli.run`` so the flag/platform logic is
unit-testable without spawning mpv. The live ``subprocess.Popen`` stays in ``cli.run`` (its real
subprocess + IPC handshake is smoke-tested with a fake mpv in ``tests/test_launch.py``)."""

from __future__ import annotations

import os
import sys


def build_mpv_argv(
    mpv_bin: str,
    sock: str,
    mpv_log: str | os.PathLike,
    video_path: str | os.PathLike,
    *,
    slang: str,
    start: str,
    screenshot: bool,
    sub_path: str | os.PathLike | None = None,
    en_sub_path: str | os.PathLike | None = None,
    use_config: bool = True,
    fullscreen: bool = False,
) -> list[str]:
    """The mpv command line for ``run``: IPC server + logging + window/subtitle flags. Subtitle files
    are inserted just before the video arg (so they load as tracks, EN as the 2nd → secondary), and
    ``--no-config`` / ``--fullscreen`` go right after the binary."""
    cmd = [
        str(mpv_bin),
        f"--input-ipc-server={sock}",
        f"--log-file={mpv_log}",
        "--force-window=yes",
        "--keep-open=yes",
        f"--slang={slang}",
        "--sub-visibility=no",
        "--osd-level=0",
        "--pause" if screenshot else "--loop-file=inf",
        f"--start={start}",
        str(video_path),
    ]
    if sub_path:
        cmd.insert(-1, f"--sub-file={sub_path}")
    if en_sub_path:
        cmd.insert(-1, f"--sub-file={en_sub_path}")
    if sys.platform == "win32":
        # Windows d3d11 (the default GPU context) uses FLIP-MODEL presentation, which does NOT
        # re-present the window while paused — so an `overlay-add` (a new/updated subtitle or tooltip)
        # only becomes visible on the next real window event (a mouse move, a resize). That is the
        # "subtitle doesn't update until I move the mouse" bug. Forcing the blit model makes mpv honor
        # redraw requests while paused. Harmless no-op if a non-d3d11 context is selected.
        cmd.insert(1, "--d3d11-flip=no")
    if not use_config:
        cmd.insert(1, "--no-config")
    if fullscreen:
        cmd.insert(1, "--fullscreen")
    return cmd
