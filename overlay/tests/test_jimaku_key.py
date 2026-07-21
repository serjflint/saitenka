"""jimaku API-key resolution + macOS Keychain storage.

Precedence: explicit (config/CLI) > $JIMAKU_API_KEY > Keychain. The Keychain path is the one that
works under a GUI-launched plugin-mode mpv, so its coordinates and the resolver order matter.
"""

from __future__ import annotations

from overlay.app import jimaku


def test_resolve_prefers_explicit(monkeypatch):
    monkeypatch.setenv("JIMAKU_API_KEY", "envkey")
    monkeypatch.setattr(jimaku, "keychain_get", lambda: "kckey")
    assert jimaku.resolve_jimaku_key("cfgkey") == ("cfgkey", "config")


def test_resolve_env_over_keychain(monkeypatch):
    monkeypatch.setenv("JIMAKU_API_KEY", "envkey")
    monkeypatch.setattr(jimaku, "keychain_get", lambda: "kckey")
    assert jimaku.resolve_jimaku_key() == ("envkey", "env")


def test_resolve_falls_back_to_keychain(monkeypatch):
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setattr(jimaku, "keychain_get", lambda: "kckey")
    assert jimaku.resolve_jimaku_key() == ("kckey", "keychain")


def test_resolve_none_when_nothing_set(monkeypatch):
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setattr(jimaku, "keychain_get", lambda: None)
    assert jimaku.resolve_jimaku_key() == (None, "none")


def test_keychain_get_parses_security_output(monkeypatch):
    import subprocess

    monkeypatch.setattr(jimaku.sys, "platform", "darwin")

    class R:
        stdout = "secret-key\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert jimaku.keychain_get() == "secret-key"


def test_keychain_noop_off_macos(monkeypatch):
    monkeypatch.setattr(jimaku.sys, "platform", "linux")
    assert jimaku.keychain_get() is None
    assert jimaku.keychain_set("x") is False


def test_keychain_set_invokes_security_with_update_flag(monkeypatch):
    import subprocess

    calls = {}
    monkeypatch.setattr(jimaku.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.setdefault("cmd", cmd))
    assert jimaku.keychain_set("mykey") is True
    cmd = calls["cmd"]
    assert cmd[:2] == ["security", "add-generic-password"] and "-U" in cmd
    assert "saitenka-overlay" in cmd and "mykey" in cmd


def test_client_error_names_the_keychain_command(monkeypatch):
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setattr(jimaku, "keychain_get", lambda: None)
    try:
        jimaku.JimakuClient()
    except jimaku.JimakuError as e:
        assert "set-jimaku-key" in str(e)
    else:
        raise AssertionError("expected JimakuError")
