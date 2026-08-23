"""Persistent overlay config: TOML load, path expansion, precedence, env override."""

from dataclasses import fields
from pathlib import Path

from saitenka.app.config import (
    SubtitleGeometryOptions,
    TelemetryOptions,
    config_path,
    expand_paths,
    load_config,
    resolve_resync_split_penalty,
    resolve_telemetry,
    subtitle_geometry_options,
    warn_retired,
)


def test_resolve_resync_split_penalty_defaults_to_none_and_parses_a_float():
    assert (
        resolve_resync_split_penalty({}) is None
    )  # unset → no --split-penalty flag (alass default)
    assert resolve_resync_split_penalty({"resync_split_penalty": 3}) == 3.0
    assert resolve_resync_split_penalty({"resync_split_penalty": 0.5}) == 0.5


def test_load_config_and_expand_paths(tmp_path, monkeypatch):
    # $HOME is POSIX-only; set it so the var also expands on Windows (Path.home() there uses USERPROFILE).
    monkeypatch.setenv("HOME", str(Path.home()))
    p = tmp_path / "overlay.toml"
    p.write_text('slang = "ja"\ndicts = ["~/a.zip", "$HOME/b.zip"]\n[mine]\ndeck = "D"\n')
    cfg = load_config(p)
    assert cfg["slang"] == "ja"
    assert cfg["mine"]["deck"] == "D"
    ex = expand_paths(cfg["dicts"])
    # native separators (backslash on Windows) — ~ and $HOME both expanded
    assert ex == [str(Path.home() / "a.zip"), str(Path.home() / "b.zip")]


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


def test_a_retired_key_is_named_rather_than_silently_ignored(caplog):
    """A setting that stops working without saying so reads as "no effect on this machine", which
    is the one diagnosis nothing in `doctor` can correct."""
    with caplog.at_level("WARNING"):
        assert warn_retired({"perf": {"poll_interval": 0.01}}) == ["poll_interval"]

    assert "poll_interval" in caplog.text


def test_a_config_that_sets_nothing_retired_warns_about_nothing(caplog):
    with caplog.at_level("WARNING"):
        assert warn_retired({"perf": {"prefetch_workers": 2}, "slang": "ja"}) == []

    assert caplog.text == ""


def test_every_subtitle_geometry_setting_survives_the_loader():
    """Field by field, because the loader names each one and a field it forgets is invisible: the
    option keeps its default, the config file says otherwise, and nothing warns. `native_formats`
    shipped that way — `overlay.toml` asked for the SubRip tracks and got authored-ass.
    """
    written = {
        "native_visible": True,
        "native_formats": "all",
        "library_path": "/opt/lib/libass.dylib",
        "cache_max": 7,
        "lookahead": 4,
    }
    assert {f.name for f in fields(SubtitleGeometryOptions)} == set(written)

    loaded = subtitle_geometry_options({"subtitle_geometry": written})

    assert {f.name: getattr(loaded, f.name) for f in fields(loaded)} == written
