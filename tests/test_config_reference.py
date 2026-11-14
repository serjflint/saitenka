"""Persistent configuration inputs and effective configuration values."""

import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from saitenka.app import config

DATA = json.loads(
    (Path(__file__).parent / "fixtures/config_reference.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("record", DATA["resync-split"], ids=lambda r: r["id"])
def test_resync_configuration_reference(record):
    assert config.resolve_resync_split_penalty(record["input"]["config"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["load"], ids=lambda r: r["id"])
def test_configuration_file_contents(tmp_path, record):
    path = tmp_path / "overlay.toml"
    content = record["input"].get("content")
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert config.load_config(path) == record["expected"]


@pytest.mark.parametrize("record", DATA["config-path"], ids=lambda r: r["id"])
def test_explicit_configuration_path_wins_over_environment(tmp_path, monkeypatch, record):
    monkeypatch.setenv("SAITENKA_CONFIG", str(tmp_path / "env.toml"))
    assert (
        config.config_path(tmp_path / "explicit.toml" if record["input"]["explicit"] else None).name
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["expand-paths"], ids=lambda r: r["id"])
def test_configured_resource_paths(monkeypatch, record):
    for name, value in record["input"].get("env", {}).items():
        monkeypatch.setenv(name, value)
    assert [Path(path) for path in config.expand_paths(record["input"]["paths"])] == [
        Path(path) for path in record["expected"]
    ]


@pytest.mark.parametrize("record", DATA["telemetry"], ids=lambda r: r["id"])
def test_telemetry_configuration_and_environment(monkeypatch, record):
    v = record["input"]
    for name, value in v.get("env", {}).items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert asdict(config.resolve_telemetry(v["config"])) == record["expected"]


@pytest.mark.parametrize("record", DATA["retired"], ids=lambda r: r["id"])
def test_retired_configuration_warnings(record):
    assert config.warn_retired(record["input"]["config"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["geometry"], ids=lambda r: r["id"])
def test_native_subtitle_configuration(record):
    assert asdict(config.subtitle_geometry_options(record["input"]["config"])) == record["expected"]


@pytest.mark.parametrize(
    "record",
    [r for r in DATA["jlpt"] if not isinstance(r["expected"], dict)],
    ids=lambda r: r["id"],
)
def test_jlpt_display_configuration(record):
    assert config.resolve_jlpt_underlines(record["input"]["config"]) == record["expected"]


@pytest.mark.parametrize(
    "record", [r for r in DATA["jlpt"] if isinstance(r["expected"], dict)], ids=lambda r: r["id"]
)
def test_invalid_jlpt_configuration_is_rejected(record):
    with pytest.raises(TypeError, match=re.escape(record["expected"]["error"])):
        config.resolve_jlpt_underlines(record["input"]["config"])
