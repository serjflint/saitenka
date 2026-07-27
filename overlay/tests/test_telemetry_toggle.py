"""Tests for overlay.app.telemetry_toggle: the enable/disable/status logic behind the `telemetry`
CLI command. The CLI wrapper itself (printing) is not unit-tested; these cover the observable
behaviour — what lands in overlay.toml and what the state read reports."""

from __future__ import annotations

import tomllib

from overlay.app import telemetry_toggle
from overlay.app.telemetry_toggle import set_enabled, telemetry_installed, telemetry_state


def _read(path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_set_enabled_writes_the_flag_into_a_fresh_config(tmp_path):
    cfg = tmp_path / "overlay.toml"
    changed, backup = set_enabled(enabled=True, dest=cfg)
    assert changed is True
    assert backup is None  # nothing pre-existing to back up
    assert _read(cfg)["telemetry"]["enabled"] is True


def test_set_enabled_is_idempotent_and_writes_nothing_when_already_set(tmp_path):
    cfg = tmp_path / "overlay.toml"
    set_enabled(enabled=True, dest=cfg)
    mtime = cfg.stat().st_mtime_ns
    changed, backup = set_enabled(enabled=True, dest=cfg)  # already true
    assert changed is False
    assert backup is None
    assert cfg.stat().st_mtime_ns == mtime  # file untouched — no needless rewrite/backup


def test_disable_flips_it_back_and_backs_up(tmp_path):
    cfg = tmp_path / "overlay.toml"
    set_enabled(enabled=True, dest=cfg)
    changed, backup = set_enabled(enabled=False, dest=cfg)
    assert changed is True
    assert backup is not None and backup.exists()  # prior file preserved
    assert _read(cfg)["telemetry"]["enabled"] is False


def test_set_enabled_preserves_other_config_and_comments(tmp_path):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("# my overlay config\n[jimaku]\nkey = 'secret'  # keep me\n", encoding="utf-8")
    set_enabled(enabled=True, dest=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "# my overlay config" in text  # tomlkit round-trip kept the comment
    assert "# keep me" in text
    data = _read(cfg)
    assert data["jimaku"]["key"] == "secret"  # untouched sibling table survives
    assert data["telemetry"]["enabled"] is True


def test_telemetry_state_reflects_config_flag(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    set_enabled(enabled=True, dest=cfg)
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    st = telemetry_state()
    assert st.config_enabled is True
    assert st.trace_path.name == "trace.json"
    # extra is installed in the dev env (via [full]); effective tracks config ∧ extra ∧ ¬killswitch.
    assert st.effective is (st.config_enabled and st.extra_installed and not st.kill_switch)


def test_kill_switch_forces_not_effective_even_when_config_enabled(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    set_enabled(enabled=True, dest=cfg)
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    st = telemetry_state()
    assert st.config_enabled is True
    assert st.kill_switch is True
    assert st.effective is False  # kill switch wins


def test_disabled_config_reports_not_effective(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))  # no [telemetry] table at all
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    st = telemetry_state()
    assert st.config_enabled is False
    assert st.effective is False


def test_telemetry_installed_true_in_dev_env():
    # The dev/test env installs [full], which includes the telemetry extra.
    assert telemetry_installed() is True
    assert telemetry_toggle.INSTALL_HINT.endswith("saitenka-overlay[telemetry]'")
