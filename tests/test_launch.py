"""Real-process and version-probe boundaries for mpv launch.

The portable CLI corpus owns command-line construction; these tests retain behavior a pure adapter cannot
establish: socket readiness and actionable failure from the actual version probe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from saitenka.mpvio.ipc import MpvIPC
from saitenka.mpvio.launch import (
    MpvLaunchError,
    MpvLaunchOptions,
    build_mpv_argv,
)

FAKE_MPV = Path(__file__).resolve().parent / "fake_mpv.py"


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


def _fake_mpv(tmp_path: Path, monkeypatch, *, returncode=0, stdout="", stderr="") -> Path:
    exe = tmp_path / "mpv.exe"
    exe.touch()
    exe.chmod(0o755)

    def run(argv, **kwargs):
        assert argv == [str(exe), "--version"]
        assert kwargs.get("capture_output") and (
            kwargs.get("encoding") or kwargs.get("text") or kwargs.get("universal_newlines")
        )
        assert not kwargs.get("check")
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return exe


def _launch_native(mpv_path: Path, tmp_path: Path):
    from saitenka.app.launch.run import _launch_mpv_and_connect

    return _launch_mpv_and_connect(
        {"mpv_path": str(mpv_path)},
        tmp_path,
        tmp_path / "video.mkv",
        MpvLaunchOptions(slang="jpn", start="0", native_visible=True),
        sub_path=None,
        en_sub_path=None,
    )


def test_an_mpv_that_cannot_run_is_not_reported_as_too_old(tmp_path, monkeypatch):
    broken = _fake_mpv(
        tmp_path,
        monkeypatch,
        returncode=134,
        stderr="dyld: Library not loaded: libunibreak.7.dylib\n",
    )

    with pytest.raises(MpvLaunchError) as refused:
        _launch_native(broken, tmp_path)

    assert "could not run" in str(refused.value)
    assert "libunibreak.7.dylib" in str(refused.value)
    assert "needs mpv" not in str(refused.value)


def test_an_old_mpv_is_refused_with_the_version_it_reported(tmp_path, monkeypatch):
    old = _fake_mpv(tmp_path, monkeypatch, stdout="mpv v0.37.0 Copyright\n")

    with pytest.raises(MpvLaunchError, match=r"needs mpv ≥ 0\.40, found 0\.37"):
        _launch_native(old, tmp_path)
