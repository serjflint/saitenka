from __future__ import annotations

import contextlib
import importlib
import io
import os
import re
import tempfile
from pathlib import Path

PROTOCOL = "saitenka.cli/v0"
CAPABILITY = "cli-v0"


def _cli():
    from saitenka.app import cli

    return cli


def _json_value(value: object) -> object:
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _parse(scenario: dict) -> dict[str, object]:
    cli = _cli()
    _command, bound, *_ = cli.app.parse_args(scenario["args"])
    if scenario.get("apply_defaults"):
        bound.apply_defaults()
    return {key: _json_value(bound.arguments.get(key)) for key in scenario["observe"]}


def _parse_with_config(scenario: dict) -> dict[str, object]:
    cli = _cli()
    previous = os.environ.get("SAITENKA_CONFIG")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "overlay.toml"
        path.write_text(scenario["config"], encoding="utf-8")
        os.environ["SAITENKA_CONFIG"] = str(path)
        importlib.reload(cli)
        try:
            return _parse(scenario)
        finally:
            if previous is None:
                os.environ.pop("SAITENKA_CONFIG", None)
            else:
                os.environ["SAITENKA_CONFIG"] = previous
            importlib.reload(cli)


def _inventory() -> dict[str, object]:
    app = _cli().app
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.suppress(SystemExit):
        app(["run", "--help"], exit_on_error=False)
    flags = sorted(set(re.findall(r"--[a-z][a-z0-9-]*", output.getvalue())))
    commands = sorted(name for name in app if not name.startswith("-"))
    return {"flags": flags, "commands": commands}


def evaluate(scenario: dict) -> object:
    if scenario["mode"] == "inventory":
        return _inventory()
    if scenario["mode"] == "parse":
        return _parse(scenario)
    if scenario["mode"] == "parse-config":
        return _parse_with_config(scenario)
    raise ValueError(f"unknown cli-v0 mode: {scenario['mode']!r}")
