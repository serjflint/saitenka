"""Persistent overlay config: TOML load, path expansion, precedence, env override."""

from pathlib import Path

from overlay.app.config import (
    TelemetryOptions,
    config_path,
    expand_paths,
    load_config,
    resolve_telemetry,
)


def test_load_config_and_expand_paths(tmp_path):
    p = tmp_path / "overlay.toml"
    p.write_text('slang = "ja"\ndicts = ["~/a.zip", "$HOME/b.zip"]\n[mine]\ndeck = "D"\n')
    cfg = load_config(p)
    assert cfg["slang"] == "ja"
    assert cfg["mine"]["deck"] == "D"
    home = str(Path.home())
    ex = expand_paths(cfg["dicts"])
    assert ex == [f"{home}/a.zip", f"{home}/b.zip"]  # ~ and $HOME both expanded


def test_missing_config_is_empty(tmp_path):
    assert load_config(tmp_path / "nope.toml") == {}


def test_malformed_config_is_empty(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("this is = = not valid toml [[[")
    assert load_config(p) == {}


def test_config_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_CONFIG", str(tmp_path / "x.toml"))
    assert config_path() == tmp_path / "x.toml"
    assert (
        config_path(tmp_path / "explicit.toml") == tmp_path / "explicit.toml"
    )  # arg wins over env


def test_expand_paths_handles_none():
    assert expand_paths(None) == []


def test_resolve_telemetry_defaults_off():
    assert resolve_telemetry({}) == TelemetryOptions()
    assert resolve_telemetry({}).enabled is False


def test_resolve_telemetry_round_trips_table():
    cfg = {"telemetry": {"enabled": True, "export_dir": "/tmp/tel", "sample_hot_path": 0.05}}
    opts = resolve_telemetry(cfg)
    assert opts == TelemetryOptions(enabled=True, export_dir="/tmp/tel", sample_hot_path=0.05)


def test_resolve_telemetry_otel_sdk_disabled_wins_over_config(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    cfg = {"telemetry": {"enabled": True}}
    assert resolve_telemetry(cfg).enabled is False
