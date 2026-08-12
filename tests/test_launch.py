"""mpv launch boundary. Two layers (SubMiner's split): the pure ``build_mpv_argv`` argv logic as unit
tests, and one real-subprocess smoke that launches a fake mpv and confirms the IPC handshake — the
``run`` path (``pragma: no cover``) that actually spawns mpv."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from saitenka.mpvio.ipc import MpvIPC
from saitenka.mpvio.launch import MpvLaunchOptions, build_mpv_argv

FAKE_MPV = Path(__file__).resolve().parent / "fake_mpv.py"

_OPT_FIELDS = ("slang", "start", "screenshot", "use_config", "fullscreen", "extra_args")


def _argv(**over) -> list[str]:
    kw = {"slang": "jpn", "start": "1", "screenshot": False}
    kw.update(over)
    subs = {k: kw.pop(k) for k in ("sub_path", "en_sub_path") if k in kw}
    opts = MpvLaunchOptions(**{k: v for k, v in kw.items() if k in _OPT_FIELDS})
    return build_mpv_argv("mpv", "/tmp/s.sock", "/tmp/mpv.log", "video.mkv", opts, **subs)


def test_core_flags_ipc_server_and_video_last():
    argv = _argv()
    assert argv[0] == "mpv"
    assert "--input-ipc-server=/tmp/s.sock" in argv
    assert "--log-file=/tmp/mpv.log" in argv
    assert argv[-1] == "video.mkv"
    # No loop: keep-open=yes holds the last frame at EOF, and a real EOF is what #100 auto-advance
    # observes. Nothing pauses the interactive path up front.
    assert "--loop-file=inf" not in argv and "--pause" not in argv
    assert "--keep-open=yes" in argv


def test_native_subs_are_always_center_aligned():
    # Whatever mpv renders itself (the fallback: a track the overlay doesn't take over) must be centered,
    # never left-aligned — including ASS subs, via --sub-ass-justify.
    argv = _argv()
    assert "--sub-align-x=center" in argv
    assert "--sub-justify=center" in argv
    assert "--sub-ass-justify=yes" in argv


def test_centering_never_touches_vertical_position():
    # Centering is HORIZONTAL only. We must never emit a vertical-position flag (--sub-align-y defaults to
    # bottom, --sub-pos to 100=bottom), so subs stay pinned at the bottom and a user's mpv.conf wins.
    joined = " ".join(_argv())
    assert "--sub-align-y" not in joined
    assert "--sub-pos" not in joined


def test_screenshot_pauses_on_the_first_frame():
    argv = _argv(screenshot=True)
    assert "--pause" in argv and "--loop-file=inf" not in argv


def test_subtitle_files_inserted_before_the_video_arg():
    argv = _argv(sub_path="a.srt", en_sub_path="b.srt")
    assert argv[-1] == "video.mkv"  # video stays last
    vi = argv.index("video.mkv")
    assert argv.index("--sub-file=a.srt") < vi
    assert argv.index("--sub-file=b.srt") < vi
    # EN sub after JP sub → loads as the 2nd (secondary/translation) track
    assert argv.index("--sub-file=a.srt") < argv.index("--sub-file=b.srt")


def test_no_config_and_fullscreen_go_after_the_binary_not_at_slot_0():
    argv = _argv(use_config=False, fullscreen=True)
    assert argv[0] == "mpv"  # never displaces the binary
    assert "--no-config" in argv[:4] and "--fullscreen" in argv[:4]


def test_script_opts_mark_the_launch_as_run_managed():
    # A globally-installed saitenka.lua plugin autoloads regardless of --no-config and would
    # otherwise double-attach onto this same socket (see saitenka.lua's spawn_overlay guard).
    assert "--script-opts=saitenka-managed=yes" in _argv()


def test_extra_args_can_override_our_own_defaults():
    # mpv is last-flag-wins: a user --mpv-arg re-stating one of our own overridable defaults
    # (here --slang) must win, matching SubMiner's -a/--args precedent.
    argv = _argv(extra_args=["--slang=en"])
    assert argv.index("--slang=en") > argv.index("--slang=jpn")


def test_extra_args_cannot_override_the_ipc_socket_or_log_file():
    # These two are load-bearing for our own code (the Reader connects to `sock`; `report` bundles
    # the fixed mpv_log path) — an --mpv-arg re-stating either must still lose.
    argv = _argv(extra_args=["--input-ipc-server=/tmp/evil.sock", "--log-file=/tmp/evil.log"])
    assert argv.index("--input-ipc-server=/tmp/s.sock") > argv.index(
        "--input-ipc-server=/tmp/evil.sock"
    )
    assert argv.index("--log-file=/tmp/mpv.log") > argv.index("--log-file=/tmp/evil.log")


def test_d3d11_flip_disabled_only_on_windows(monkeypatch):
    # The flip-model swapchain doesn't re-present while paused → overlay updates only show on a
    # window event. We force the blit model on Windows; other platforms must not get the flag.
    monkeypatch.setattr("saitenka.mpvio.launch.sys.platform", "win32")
    assert "--d3d11-flip=no" in _argv()
    monkeypatch.setattr("saitenka.mpvio.launch.sys.platform", "darwin")
    assert "--d3d11-flip=no" not in _argv()


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32", reason="fake mpv uses AF_UNIX; named-pipe variant is R5"
)
def test_launched_process_serves_the_ipc_socket(tmp_path):
    """The run-vs-attach launch path over a REAL subprocess: a process spawned with
    ``--input-ipc-server=<sock>`` must create that socket, and our reader must connect and receive the
    unsolicited events it pushes — the boundary a Popen mock can't prove."""
    # AF_UNIX sun_path is capped (~104 on macOS); tmp_path (/var/folders/…) overflows it — use /tmp.
    sock = f"/tmp/sait-fake-{os.getpid()}.sock"
    Path(sock).unlink(missing_ok=True)
    log = tmp_path / "argv.json"
    argv = build_mpv_argv(
        sys.executable,
        sock,
        str(tmp_path / "mpv.log"),
        "video.mkv",
        MpvLaunchOptions(slang="jpn", start="1", screenshot=False),
    )
    # run fake_mpv.py as the "mpv binary", carrying the same argv build() produced (+ a log sink)
    proc = subprocess.Popen([sys.executable, str(FAKE_MPV), *argv[1:], f"--fake-log={log}"])
    try:
        ipc = MpvIPC(sock).connect(timeout=5)
        deadline = time.monotonic() + 2
        events: list = []
        while time.monotonic() < deadline and not events:
            events = [e for e in ipc.drain_events() if e.get("name") == "sub-text"]
            time.sleep(0.02)
        assert events, "no event from the launched fake mpv over the real socket"
        ipc.close()
        logged = json.loads(log.read_text())
        assert any(a.startswith("--input-ipc-server=") for a in logged)  # launch really passed it
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        Path(sock).unlink(missing_ok=True)
