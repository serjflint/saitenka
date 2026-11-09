"""CLI arguments, mpv launch arguments, and public command output reference inputs."""

import json
from pathlib import Path

import pytest

from saitenka.app import cli
from saitenka.app.launch.run import jimaku_should_fetch, resolve_names
from saitenka.mpvio import launch

DATA = json.loads((Path(__file__).parent / "fixtures/cli_reference.json").read_text())


@pytest.mark.parametrize("record", DATA["mpv-launch"], ids=lambda r: r["id"])
def test_mpv_launch_arguments_reference(monkeypatch, record):
    v = record["input"]
    monkeypatch.setattr(launch.sys, "platform", v["platform"])
    argv = launch.build_mpv_argv(
        v["binary"],
        v["ipc_path"],
        v["log_path"],
        v["video"],
        launch.MpvLaunchOptions(**v["options"]),
        sub_path=v["sub_path"],
        en_sub_path=v["second_sub_path"],
    )
    assert argv == record["expected"]


@pytest.mark.parametrize("record", DATA["native-geometry-version"], ids=lambda r: r["id"])
def test_supported_native_geometry_versions(record):
    assert launch.supports_native_geometry_profile(record["input"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["parse"], ids=lambda r: r["id"])
def test_run_command_argument_values(record):
    _command, bound, *_ = cli.create_app().parse_args(record["input"]["args"])
    bound.apply_defaults()
    actual = {key: bound.arguments.get(key) for key in record["expected"]}
    assert json.loads(json.dumps(actual)) == record["expected"]


@pytest.mark.parametrize("record", DATA["parse-config"], ids=lambda r: r["id"])
def test_run_command_config_precedence(tmp_path, monkeypatch, record):
    path = tmp_path / "overlay.toml"
    path.write_text(record["input"]["config"])
    monkeypatch.setenv("SAITENKA_CONFIG", str(path))
    _command, bound, *_ = cli.create_app().parse_args(record["input"]["args"])
    bound.apply_defaults()
    assert {key: bound.arguments.get(key) for key in record["expected"]} == record["expected"]


@pytest.mark.parametrize("record", DATA["resolve-names"], ids=lambda r: r["id"])
def test_cli_resource_names_override_configuration(record):
    v = record["input"]
    assert resolve_names(v["flags"], v["config"], v["key"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["jimaku-decision"], ids=lambda r: r["id"])
def test_fetch_flag_configuration_and_existing_tracks(record):
    v = record["input"]
    assert (
        jimaku_should_fetch(
            explicit_flag=v["explicit"],
            cfg_fetch=v["configured"],
            video=v["video"],
            probe=lambda _video, _slang: v["probe"],
        )
        == record["expected"]
    )
