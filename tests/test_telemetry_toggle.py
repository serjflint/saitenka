"""Tests for saitenka.app.telemetry_toggle: the enable/disable/status logic behind the `telemetry`
CLI command. The CLI wrapper itself (printing) is not unit-tested; these cover the observable
behaviour — what lands in overlay.toml and what the state read reports."""

from __future__ import annotations

import os
import tomllib

import pytest

from saitenka.app import telemetry_toggle
from saitenka.app.telemetry_toggle import set_enabled, telemetry_installed, telemetry_state


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
    os.utime(cfg, ns=(1_000_000_000, 1_000_000_000))
    changed, backup = set_enabled(enabled=True, dest=cfg)  # already true
    assert changed is False
    assert backup is None
    assert cfg.stat().st_mtime_ns == 1_000_000_000


def test_disable_flips_it_back_and_backs_up(tmp_path):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text(
        "# keep this comment\n[telemetry]\nenabled = true\n[jimaku]\nkey = 'secret'\n",
        encoding="utf-8",
    )
    changed, backup = set_enabled(enabled=False, dest=cfg)
    assert changed is True
    assert backup is not None and backup.exists()  # prior file preserved
    assert _read(cfg)["telemetry"]["enabled"] is False
    assert _read(cfg)["jimaku"]["key"] == "secret"
    assert "# keep this comment" in cfg.read_text(encoding="utf-8")


@pytest.mark.parametrize("installed", [False, True])
def test_telemetry_state_reflects_config_flag(tmp_path, monkeypatch, installed):
    cfg = tmp_path / "overlay.toml"
    set_enabled(enabled=True, dest=cfg)
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    # Isolate the cache dir too: export_dir() falls back to cache_dir()/telemetry, so without this the
    # developer's own real session traces leak in and trace_exists is spuriously True.
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(telemetry_toggle, "telemetry_installed", lambda: installed)
    st = telemetry_state()
    assert st.config_enabled is True
    assert st.extra_installed is installed
    assert st.trace_exists is False
    assert st.effective is installed


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
    assert telemetry_installed() is True
