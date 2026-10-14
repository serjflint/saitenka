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
ANSWER_KEYS = frozenset({"expect", "expect_contains"})
MODE_KEYS = {
    "inventory": frozenset({"scenario_id", "mode", "expect_contains"}),
    "parse": frozenset({"scenario_id", "mode", "args", "observe", "expect"}),
    "parse-config": frozenset({"scenario_id", "mode", "config", "args", "observe", "expect"}),
    "resolve-names": frozenset({"scenario_id", "mode", "cases", "expect"}),
    "jimaku-decision": frozenset({"scenario_id", "mode", "cases", "expect"}),
}


def scenario_request(scenario: dict) -> dict:
    mode = scenario.get("mode")
    allowed = MODE_KEYS.get(mode)
    if allowed is None:
        raise ValueError(f"unknown cli-v0 mode: {mode!r}")
    expected_keys = set(allowed)
    if mode == "parse" and "apply_defaults" in scenario:
        expected_keys.add("apply_defaults")
    if set(scenario) != expected_keys:
        raise ValueError(f"invalid cli-v0 scenario keys for {mode!r}")
    payload = {key: value for key, value in scenario.items() if key not in ANSWER_KEYS}
    return {"type": "run", "protocol": PROTOCOL, **payload}


def validate_request(request: dict) -> None:
    if ANSWER_KEYS & request.keys():
        raise ValueError("cli-v0 requests cannot contain answer keys")
    required = {"type", "protocol", "scenario_id", "mode"}
    if (
        not required <= request.keys()
        or request["type"] != "run"
        or request["protocol"] != PROTOCOL
    ):
        raise ValueError("invalid cli-v0 request envelope")
    mode = request["mode"]
    allowed = MODE_KEYS.get(mode)
    if allowed is None:
        raise ValueError(f"unknown cli-v0 mode: {mode!r}")
    request_keys = (allowed - ANSWER_KEYS) | {"type", "protocol"}
    if mode == "parse" and "apply_defaults" in request:
        request_keys |= {"apply_defaults"}
    if set(request) != request_keys:
        raise ValueError(f"invalid cli-v0 request keys for {mode!r}")


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
    validate_request(scenario)
    if scenario["mode"] == "inventory":
        return _inventory()
    if scenario["mode"] == "parse":
        return _parse(scenario)
    if scenario["mode"] == "parse-config":
        return _parse_with_config(scenario)
    if scenario["mode"] == "resolve-names":
        from saitenka.app.launch.run import resolve_names

        return [
            resolve_names(case["flags"], case["config"], case["key"]) for case in scenario["cases"]
        ]
    if scenario["mode"] == "jimaku-decision":
        from saitenka.app.launch.run import jimaku_should_fetch

        return [
            jimaku_should_fetch(
                explicit_flag=case["explicit"],
                cfg_fetch=case["configured"],
                video=case["video"],
                probe=lambda _video, _slang, answer=case["probe"]: answer,
            )
            for case in scenario["cases"]
        ]
    raise ValueError(f"unknown cli-v0 mode: {scenario['mode']!r}")
