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

from overlay.mpvio.ipc import MpvIPC
from overlay.mpvio.launch import build_mpv_argv

FAKE_MPV = Path(__file__).resolve().parent / "fake_mpv.py"


def _argv(**over) -> list[str]:
    kw = {"slang": "jpn", "start": "1", "screenshot": False}
    kw.update(over)
    return build_mpv_argv("mpv", "/tmp/s.sock", "/tmp/mpv.log", "video.mkv", **kw)


def test_core_flags_ipc_server_and_video_last():
    argv = _argv()
    assert argv[0] == "mpv"
    assert "--input-ipc-server=/tmp/s.sock" in argv
    assert "--log-file=/tmp/mpv.log" in argv
    assert argv[-1] == "video.mkv"
    assert "--loop-file=inf" in argv and "--pause" not in argv


def test_screenshot_pauses_instead_of_looping():
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
        slang="jpn",
        start="1",
        screenshot=False,
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
