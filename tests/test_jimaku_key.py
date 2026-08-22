"""jimaku API-key resolution + persistent storage.

Precedence: explicit (config/CLI) > $JIMAKU_API_KEY > OS keyring > private file.
"""

from __future__ import annotations

import stat
import sys
from types import ModuleType

from saitenka.app import jimaku


def _fake_keyring(monkeypatch) -> ModuleType:
    keyring = ModuleType("keyring")
    errors = ModuleType("keyring.errors")

    class KeyringError(Exception):
        pass

    errors.KeyringError = KeyringError
    errors.NoKeyringError = KeyringError
    keyring.errors = errors
    keyring.get_password = lambda *_args: None
    keyring.set_password = lambda *_args: None
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    return keyring


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
    keyring = _fake_keyring(monkeypatch)
    store: dict = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    assert jimaku.keychain_set("mykey") is True
    assert store["saitenka", "jimaku"] == "mykey"
    assert jimaku.keychain_get() == "mykey"


def test_keychain_returns_false_none_when_no_backend(monkeypatch):
    """No keyring backend (headless Linux) → set() is False, get() is None → caller falls back."""
    keyring = _fake_keyring(monkeypatch)

    def _boom(*_a, **_k):
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "set_password", _boom)
    monkeypatch.setattr(keyring, "get_password", _boom)
    assert jimaku.keychain_set("x") is False
    assert jimaku.keychain_get() is None


def test_keychain_falls_back_when_keyring_is_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
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


def test_store_key_falls_back_to_private_file_without_keyring(monkeypatch, tmp_path):
    """No keyring backend keeps jimaku usable without putting its secret in the main config."""
    from saitenka.app import init_wizard
    from saitenka.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    cfg.write_text('slang = "ja"\n\n[mine]\nkey = "Ctrl+m"\n\n[jimaku]\nkey = "LEGACY"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setattr("saitenka.app.jimaku.keychain_set", lambda _k: False)  # no backend

    method, backup = init_wizard.store_jimaku_key("MYKEY123")
    assert method == "file"
    loaded = load_config()
    assert loaded["jimaku"]["fetch"] is True  # setting a key enables jimaku fetch
    assert "key" not in loaded["jimaku"]
    assert loaded["mine"]["key"] == "Ctrl+m"  # dumps_toml preserved the other table
    assert jimaku.resolve_jimaku_key() == ("MYKEY123", "file")
    key_file = cfg.with_name("jimaku.key")
    assert key_file.read_text(encoding="utf-8") == "MYKEY123\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
        assert backup is not None and stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_store_key_uses_keyring_when_available(monkeypatch, tmp_path):
    """Keyring stores the secret; the config still records [jimaku].fetch=true (so run/attach act on
    it and the installer can see jimaku is set up) but NOT the key itself."""
    from saitenka.app import init_wizard
    from saitenka.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setattr("saitenka.app.jimaku.keychain_set", lambda _k: True)
    method, _ = init_wizard.store_jimaku_key("K")
    assert method == "keyring"
    loaded = load_config()
    assert loaded["jimaku"]["fetch"] is True
    assert "key" not in loaded["jimaku"]  # the secret stays in the keyring, not the config


def test_keyring_enabled_env_override_wins(monkeypatch, tmp_path):
    """$SAITENKA_JIMAKU_KEYRING is a one-off override that beats the config file."""
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("[jimaku]\nkeyring = true\n")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    for val, want in (("0", False), ("false", False), ("off", False), ("1", True), ("yes", True)):
        monkeypatch.setenv("SAITENKA_JIMAKU_KEYRING", val)
        assert jimaku.keyring_enabled() is want


def test_keyring_enabled_reads_config_and_defaults_true(monkeypatch, tmp_path):
    monkeypatch.delenv("SAITENKA_JIMAKU_KEYRING", raising=False)
    cfg = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    cfg.write_text("[jimaku]\nkeyring = false\n")
    assert jimaku.keyring_enabled() is False
    cfg.write_text("[jimaku]\nfetch = true\n")  # key absent → default enabled
    assert jimaku.keyring_enabled() is True


def test_resolve_skips_keychain_when_keyring_disabled(monkeypatch):
    """A disabled keyring must not issue the read at all (the Windows-AV trigger) — resolve falls
    straight to the file, never calling keychain_get."""
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setenv("SAITENKA_JIMAKU_KEYRING", "0")

    def _boom():
        raise AssertionError("keychain_get must not be called when the keyring is disabled")

    monkeypatch.setattr(jimaku, "keychain_get", _boom)
    monkeypatch.setattr(jimaku, "key_file_get", lambda: "filekey")
    assert jimaku.resolve_jimaku_key() == ("filekey", "file")


def test_store_key_prefer_file_forces_file_and_persists_optout(monkeypatch, tmp_path):
    """--file skips the keyring even when it works AND writes [jimaku].keyring=false so a later read
    also bypasses the Credential Locker (the Windows-AV escape hatch)."""
    from saitenka.app import init_wizard
    from saitenka.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))

    def _keyring_must_not_run(_k):
        raise AssertionError("keychain_set must not run under prefer_file")

    monkeypatch.setattr("saitenka.app.jimaku.keychain_set", _keyring_must_not_run)
    method, _ = init_wizard.store_jimaku_key("MYKEY123", prefer_file=True)
    assert method == "file"
    loaded = load_config()
    assert loaded["jimaku"]["keyring"] is False  # opt-out persisted
    assert loaded["jimaku"]["fetch"] is True
    assert cfg.with_name("jimaku.key").read_text(encoding="utf-8") == "MYKEY123\n"


def test_store_key_no_backend_fallback_keeps_keyring_enabled(monkeypatch, tmp_path):
    """A no-backend fallback (headless Linux) uses the file but must NOT record keyring=false — the
    opt-out is only for a deliberate --file, not a transient missing backend."""
    from saitenka.app import init_wizard
    from saitenka.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setattr("saitenka.app.jimaku.keychain_set", lambda _k: False)  # no backend
    method, _ = init_wizard.store_jimaku_key("MYKEY123")
    assert method == "file"
    assert "keyring" not in load_config()["jimaku"]  # not a deliberate opt-out


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


def test_subs_cache_roundtrip(monkeypatch, tmp_path):
    """A synced sub is cached per (video, title, episode)+size and reused on a rewatch; a different
    episode or a re-encoded (resized) video misses so it re-fetches."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "[Erai] Show - 01.mkv"
    video.write_bytes(b"x" * 100)
    src = tmp_path / "dl.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:02,000\nねこ\n", encoding="utf-8")

    assert jimaku.cached_subs(video, "Show", 1) is None  # miss before store
    dest = jimaku.store_subs(video, "Show", 1, src)
    assert dest.exists() and dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert jimaku.cached_subs(video, "Show", 1) == dest  # rewatch → hit
    assert jimaku.cached_subs(video, "Show", 2) is None  # different episode misses
    video.write_bytes(b"x" * 200)  # re-encode changes the size
    assert jimaku.cached_subs(video, "Show", 1) is None  # → miss, re-fetch


def test_subs_cache_resync_modes_are_separate_but_a_hand_pick_is_visible(monkeypatch, tmp_path):
    """The `-raw` slot is written ONLY by the source picker (`subselect.py:176,214`), so it means
    "the user chose this by hand". A resyncing lookup therefore sees it — otherwise a deliberate
    pick survives exactly one session — while the raw lookup stays confined to its own slot.

    The trade this accepts: a picked source is unsynced by design (the picker's premise is that the
    user selected a natively co-timed release), so a mistimed pick now persists across launches
    instead of being replaced by the auto-fetched+resynced file. `Ctrl+Shift+T` force-refetches past
    it.
    """
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    src = tmp_path / "downloaded.srt"
    src.write_text("Japanese", encoding="utf-8")

    raw = jimaku.store_subs(video, "Show", 1, src, resync=False)

    assert jimaku.cached_subs(video, "Show", 1, resync=False) == raw
    assert jimaku.cached_subs(video, "Show", 1, resync=True) == raw


def test_subs_cache_reads_legacy_jimaku_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    legacy = tmp_path / "cache" / "jimaku" / jimaku.subs_cache_key(video, "Show", 1)
    legacy.parent.mkdir(parents=True)
    legacy.write_text("Japanese", encoding="utf-8")

    assert jimaku.cached_subs(video, "Show", 1, resync=True) == legacy


def test_key_paste_warning_flags_short_key():
    """A key far shorter than a real ~58-char token → warning (the hidden-prompt Ctrl+V trap that
    lands a single char on Windows). A full-length key → None; empty → None (callers handle empty as
    'no key entered')."""
    assert jimaku.key_paste_warning("\x16") is not None  # a lone ^V control char from Ctrl+V
    assert "only 1 character" in jimaku.key_paste_warning("x")
    assert "right-click" in jimaku.key_paste_warning("abc").lower()
    assert jimaku.key_paste_warning("") is None
    assert jimaku.key_paste_warning("a" * 58) is None


def test_prompt_for_key_reprompts_after_truncated_paste():
    """The hidden prompt warns + re-asks after a too-short entry, then accepts the full key."""
    entries = iter(["x", "a" * 58])  # a botched 1-char paste, then the real key
    out: list = []
    got = jimaku.prompt_for_key(
        getpass_fn=lambda _p: next(entries),
        input_fn=lambda _p: "y",  # "Re-enter the key? [Y/n]" → yes
        out=out.append,
    )
    assert got == "a" * 58
    assert any("only 1 character" in m for m in out)  # the user was told why


def test_prompt_for_key_returns_short_key_if_user_declines_reentry():
    """If the user declines to re-enter, the short value is returned as-is (we warn, never block)."""
    out: list = []
    got = jimaku.prompt_for_key(
        getpass_fn=lambda _p: "short", input_fn=lambda _p: "n", out=out.append
    )
    assert got == "short"


def test_write_config_preserves_comments(tmp_path):
    """B: write_config round-trips via tomlkit — an existing file's comments + untouched keys survive,
    only changed/new keys are written (was: dumps_toml dropped every comment)."""
    from saitenka.app.init_wizard import write_config

    cfg = tmp_path / "overlay.toml"
    cfg.write_text('# header comment\nslang = "ja"\n\n[mine]\n# which key mines\nkey = "Ctrl+m"\n')
    write_config(
        {"slang": "ja", "mine": {"key": "Ctrl+m", "deck": "D"}}, confirm=lambda _p: True, dest=cfg
    )
    text = cfg.read_text()
    assert "# header comment" in text and "# which key mines" in text  # comments survive
    assert 'key = "Ctrl+m"' in text  # unchanged key kept
    assert 'deck = "D"' in text  # new key added under [mine]
