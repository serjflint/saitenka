"""mpv launch — the pure argv construction, split out of ``cli.run`` so the flag/platform logic is
unit-testable without spawning mpv. The live ``subprocess.Popen`` stays in ``cli.run`` (its real
subprocess + IPC handshake is smoke-tested with a fake mpv in ``tests/test_launch.py``)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import os


def build_mpv_argv(  # noqa: PLR0913  # arg-clump — bundle into a config object (#216)
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
    extra_args: list[str] | None = None,
) -> list[str]:
    """The mpv command line for ``run``: IPC server + logging + window/subtitle flags. Subtitle files
    are inserted just before the video arg (so they load as tracks, EN as the 2nd → secondary), and
    ``--no-config`` / ``--fullscreen`` go right after the binary.

    ``extra_args`` (SubMiner's ``-a/--args`` precedent) land after our own overridable defaults so a
    matching flag wins there (mpv is last-flag-wins), but before the socket/script-opts/log-file flags
    below — those three always win regardless of ``extra_args``: the Reader connects to the exact
    ``sock`` path, `report`/crashlog bundle the log from the fixed ``mpv_log`` path, and the script-opts
    marker prevents a globally-installed saitenka.lua from double-attaching (see the comment there)."""
    cmd = [
        str(mpv_bin),
        "--force-window=yes",
        "--keep-open=yes",
        f"--slang={slang}",
        "--sub-visibility=no",  # the overlay renders subs itself; this hides mpv's own sub layer
        # osd-level stays at mpv's default (1) so native OSD messages show — the z/Z/x sub-delay keys
        # (mpv builtins, repeatable) give feedback. sub-visibility=no already hides the subtitles, so
        # forcing osd-level=0 (an old over-broad hack) only silenced those messages.
        "--osd-level=1",
        f"--start={start}",
    ]
    if screenshot:
        # keep-open=yes already holds the last frame at EOF for the interactive path (so a finished file
        # freezes instead of closing, and #100 auto-advance can see eof-reached). Screenshot mode wants
        # the FIRST frame held, so it pauses up front instead.
        cmd.append("--pause")
    cmd.extend(extra_args or [])
    cmd.extend(
        [
            f"--input-ipc-server={sock}",
            # A globally-installed saitenka.lua (from `install-plugin`, for the ATTACH workflow) still
            # autoloads under mpv's own script-autoload — `--no-config` doesn't suppress that. It reuses
            # whatever input-ipc-server is already set (see saitenka.lua's ensure_socket()), so without
            # this marker it would spawn a SECOND `saitenka attach` onto the socket `run` mode
            # already owns: two independent Reader/telemetry instances driving one mpv. This script-opt
            # is the handshake — saitenka.lua's spawn_overlay() checks it and no-ops when set.
            "--script-opts=saitenka-managed=yes",
            f"--log-file={mpv_log}",
        ]
    )
    if sub_path:
        cmd.append(f"--sub-file={sub_path}")
    if en_sub_path:
        cmd.append(f"--sub-file={en_sub_path}")
    cmd.append(str(video_path))
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
