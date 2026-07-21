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


def test_keychain_roundtrip_via_keyring(monkeypatch):
    """keychain_get/set delegate to the keyring library (cross-platform secret store)."""
    import keyring

    store: dict = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    assert jimaku.keychain_set("mykey") is True
    assert store[("saitenka-overlay", "jimaku")] == "mykey"
    assert jimaku.keychain_get() == "mykey"


def test_keychain_returns_false_none_when_no_backend(monkeypatch):
    """No keyring backend (headless Linux) → set() is False, get() is None → caller falls back."""
    import keyring

    def _boom(*a, **k):
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "set_password", _boom)
    monkeypatch.setattr(keyring, "get_password", _boom)
    assert jimaku.keychain_set("x") is False
    assert jimaku.keychain_get() is None


def test_client_error_names_the_keychain_command(monkeypatch):
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setattr(jimaku, "keychain_get", lambda: None)
    try:
        jimaku.JimakuClient()
    except jimaku.JimakuError as e:
        assert "set-jimaku-key" in str(e)
    else:
        raise AssertionError("expected JimakuError")


def test_store_key_falls_back_to_config_without_keyring(monkeypatch, tmp_path):
    """No keyring backend → the key is written into [jimaku].key, resolves from config, enables fetch,
    and preserves pre-existing tables."""
    from overlay.app import init_wizard
    from overlay.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    cfg.write_text('slang = "ja"\n\n[mine]\nkey = "Ctrl+m"\n')  # a pre-existing table must survive
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setattr("overlay.app.jimaku.keychain_set", lambda k: False)  # no backend

    method, _ = init_wizard.store_jimaku_key("MYKEY123")
    assert method == "config"
    loaded = load_config()
    assert loaded["jimaku"]["key"] == "MYKEY123"
    assert loaded["jimaku"]["fetch"] is True  # setting a key enables jimaku fetch
    assert loaded["mine"]["key"] == "Ctrl+m"  # dumps_toml preserved the other table


def test_store_key_uses_keyring_when_available(monkeypatch, tmp_path):
    """Keyring stores the secret; the config still records [jimaku].fetch=true (so run/attach act on
    it and the installer can see jimaku is set up) but NOT the key itself."""
    from overlay.app import init_wizard
    from overlay.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setattr("overlay.app.jimaku.keychain_set", lambda k: True)
    method, _ = init_wizard.store_jimaku_key("K")
    assert method == "keyring"
    loaded = load_config()
    assert loaded["jimaku"]["fetch"] is True
    assert "key" not in loaded["jimaku"]  # the secret stays in the keyring, not the config


def test_resolve_strips_whitespace_and_newlines(monkeypatch):
    """A stray trailing newline/space (paste artifact) must be stripped — else urllib rejects the
    Authorization header (ValueError: Invalid header value)."""
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setattr(jimaku, "keychain_get", lambda: "  kc-key\n")
    assert jimaku.resolve_jimaku_key() == ("kc-key", "keychain")
    assert jimaku.resolve_jimaku_key("  cfg-key \n") == ("cfg-key", "config")
    monkeypatch.setenv("JIMAKU_API_KEY", "env-key\n")
    monkeypatch.setattr(jimaku, "keychain_get", lambda: None)
    assert jimaku.resolve_jimaku_key() == ("env-key", "env")
