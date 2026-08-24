"""Plugin baking — the Windows backslash-escape crash and the single-line rewrite."""

from __future__ import annotations

import sys

import pytest

from saitenka.app.plugin import _bake_bin
from saitenka.mpvio.ipc import default_ipc_path

_LUA = "-- header comment\nlocal SAITENKA_BIN = 'saitenka'\nlocal mp = require 'mp'\n"


def test_bake_bin_windows_path_does_not_crash_on_backslash_escape():
    """A Windows exe path used as an ``re.sub`` REPLACEMENT STRING makes \\U/\\g look like escapes →
    ``re.PatternError: bad escape \\U`` (the real install-plugin/setup crash). The callable form must
    insert the path verbatim inside a lua ``[[...]]`` literal."""
    binp = r"C:\Users\LeoDu\.local\bin\saitenka.exe"
    out = _bake_bin(_LUA, binp)
    assert f"local SAITENKA_BIN = [[{binp}]]" in out
    assert "'saitenka'" not in out  # the bare declaration was replaced


def test_bake_bin_handles_pathological_escape_segments():
    for binp in (r"C:\Users\g\Umlaut\x\n", r"D:\3.Japanese\bin\saitenka.exe"):
        out = _bake_bin(_LUA, binp)  # must not raise
        assert f"[[{binp}]]" in out


def test_bake_bin_rewrites_only_the_first_declaration():
    doubled = _LUA + "local SAITENKA_BIN = 'x'\n"
    out = _bake_bin(doubled, "/abs/saitenka")
    assert out.count("local SAITENKA_BIN = [[/abs/saitenka]]") == 1


@pytest.mark.windows_sim
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

    from saitenka.mpvio.ipc import MpvIPC

    name = default_ipc_path("test-pipe")

    def _server():
        import _winapi

        h = _winapi.CreateNamedPipe(
            name,
            _winapi.PIPE_ACCESS_DUPLEX,
            # byte type + byte read-mode are both 0x0 and CPython's _winapi doesn't expose the BYTE
            # constants (only the MESSAGE ones); PIPE_WAIT alone is the byte-mode blocking pipe we want.
            _winapi.PIPE_WAIT,
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


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_named_pipe_command_returns_while_reader_is_blocked():  # pragma: no cover — Windows kernel
    import json
    import threading
    import time

    from saitenka.mpvio.ipc import MpvIPC

    name = default_ipc_path("test-full-duplex")

    def _server():
        import _winapi

        handle = _winapi.CreateNamedPipe(
            name,
            _winapi.PIPE_ACCESS_DUPLEX,
            _winapi.PIPE_WAIT,
            1,
            65536,
            65536,
            0,
            _winapi.NULL,
        )
        _winapi.ConnectNamedPipe(handle, _winapi.NULL)
        data, _error = _winapi.ReadFile(handle, 65536)
        command = json.loads(data)
        assert command["command"] == ["get_property", "sub-text"]
        reply = json.dumps(
            {
                "request_id": command["request_id"],
                "data": "current",
                "error": "success",
            }
        ).encode()
        _winapi.WriteFile(
            handle,
            b'{"event":"property-change","name":"sub-text","data":"current"}\n' + reply + b"\n",
        )
        _winapi.CloseHandle(handle)

    thread = threading.Thread(target=_server, daemon=True)
    thread.start()
    time.sleep(0.1)
    ipc = MpvIPC(name).connect(timeout=2)

    reply = ipc.command("get_property", "sub-text", timeout=2)

    assert reply["data"] == "current"
    assert reply["error"] == "success"
    assert isinstance(reply["request_id"], int)
    assert any(event.get("data") == "current" for event in ipc.drain_events())
    ipc.close()
    thread.join(1)


def test_windows_pipe_path_normalizes_japanese_yen_glyphs(monkeypatch):
    from saitenka.mpvio import ipc as ipc_module

    monkeypatch.setattr(ipc_module.sys, "platform", "win32")

    assert ipc_module.normalize_ipc_path("￥￥.￥pipe￥mpvsocket") == r"\\.\pipe\mpvsocket"
    assert ipc_module.normalize_ipc_path("mpvsocket") == r"\\.\pipe\mpvsocket"


def test_windows_attach_has_platform_default(monkeypatch):
    from saitenka.mpvio import ipc as ipc_module

    monkeypatch.setattr(ipc_module.sys, "platform", "win32")

    assert ipc_module.default_attach_ipc_path() == r"\\.\pipe\mpvsocket"


def test_the_baked_binary_is_the_one_asking_not_the_one_on_path(tmp_path, monkeypatch):
    """`which` answers which `saitenka` this shell would start, not which one is running.

    Under `uv run` the project's cache environment leads PATH, so `install-plugin` baked that
    instead of the tool install the user had just made — and a Finder-launched mpv then started a
    copy nobody chose. The console script beside the running interpreter is the one that asked.
    """
    from saitenka.app import plugin

    running = tmp_path / "tool" / "bin"
    running.mkdir(parents=True)
    (running / "saitenka").write_text("#!/bin/sh\n")
    (running / "python").write_text("#!/bin/sh\n")
    on_path = tmp_path / "cache-env" / "bin" / "saitenka"
    on_path.parent.mkdir(parents=True)
    on_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(plugin.sys, "executable", str(running / "python"))
    monkeypatch.setattr(plugin.shutil, "which", lambda _name: str(on_path))

    assert plugin.resolve_overlay_bin() == str(running / "saitenka")


def test_an_interpreter_with_no_console_script_still_falls_back_to_path(tmp_path, monkeypatch):
    """The negative control: `python -m saitenka` from a bare venv has no script beside it, and a
    PATH answer is better than baking a bare name a Finder-launched mpv cannot resolve."""
    from saitenka.app import plugin

    on_path = tmp_path / "bin" / "saitenka"
    on_path.parent.mkdir(parents=True)
    on_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(plugin.sys, "executable", str(tmp_path / "nowhere" / "python"))
    monkeypatch.setattr(plugin.shutil, "which", lambda _name: str(on_path))

    assert plugin.resolve_overlay_bin() == str(on_path)
