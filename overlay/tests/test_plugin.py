"""Plugin baking — the Windows backslash-escape crash and the single-line rewrite."""

from __future__ import annotations

import sys

import pytest

from overlay.app.plugin import _bake_bin
from overlay.mpvio.ipc import default_ipc_path

_LUA = "-- header comment\nlocal SAITENKA_BIN = 'saitenka-overlay'\nlocal mp = require 'mp'\n"


def test_bake_bin_windows_path_does_not_crash_on_backslash_escape():
    """A Windows exe path used as an ``re.sub`` REPLACEMENT STRING makes \\U/\\g look like escapes →
    ``re.PatternError: bad escape \\U`` (the real install-plugin/setup crash). The callable form must
    insert the path verbatim inside a lua ``[[...]]`` literal."""
    binp = r"C:\Users\LeoDu\.local\bin\saitenka-overlay.exe"
    out = _bake_bin(_LUA, binp)
    assert f"local SAITENKA_BIN = [[{binp}]]" in out
    assert "'saitenka-overlay'" not in out  # the bare declaration was replaced


def test_bake_bin_handles_pathological_escape_segments():
    for binp in (r"C:\Users\g\Umlaut\x\n", r"D:\3.Japanese\bin\saitenka-overlay.exe"):
        out = _bake_bin(_LUA, binp)  # must not raise
        assert f"[[{binp}]]" in out


def test_bake_bin_rewrites_only_the_first_declaration():
    doubled = _LUA + "local SAITENKA_BIN = 'x'\n"
    out = _bake_bin(doubled, "/abs/saitenka-overlay")
    assert out.count("local SAITENKA_BIN = [[/abs/saitenka-overlay]]") == 1


def test_default_ipc_path_windows_is_a_named_pipe(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert default_ipc_path("reader-abc") == r"\\.\pipe\saitenka-reader-abc"


def test_default_ipc_path_unix_is_a_socket_file(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    p = default_ipc_path("reader-abc")
    assert p.endswith("saitenka-reader-abc.sock")
    assert not p.startswith(r"\\.\pipe")


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_named_pipe_roundtrip():  # pragma: no cover — Windows-gated smoke
    import threading
    import time

    from overlay.mpvio.ipc import MpvIPC

    name = default_ipc_path("test-pipe")

    def _server():
        import _winapi

        h = _winapi.CreateNamedPipe(
            name,
            _winapi.PIPE_ACCESS_DUPLEX,
            _winapi.PIPE_TYPE_BYTE | _winapi.PIPE_READMODE_BYTE | _winapi.PIPE_WAIT,
            1,
            65536,
            65536,
            0,
            _winapi.NULL,
        )
        _winapi.ConnectNamedPipe(h, _winapi.NULL)
        _winapi.WriteFile(h, b'{"event":"property-change","name":"sub-text","data":"x"}\n')
        time.sleep(0.3)
        _winapi.CloseHandle(h)

    th = threading.Thread(target=_server, daemon=True)
    th.start()
    time.sleep(0.1)
    ipc = MpvIPC(name).connect(timeout=5)
    time.sleep(0.2)
    assert any(e.get("name") == "sub-text" for e in ipc.drain_events())
    ipc.close()
    th.join(2)
