from __future__ import annotations

from dataclasses import replace

from saitenka.app import config_editor
from saitenka.app.command_defaults import resolve_mine_model
from saitenka.app.commands.attach import _build_attach_options
from saitenka.app.launch.run import (
    RunDepsRequest,
    RunFlags,
    _build_run_options,
    _run_effective_config,
)

BASE_FLAGS = RunFlags(
    mine_key="Ctrl+m",
    mine_all_key="Shift+m",
    translate_key="t",
    preview_key="p",
    tip_height=0.4,
    tip_scale=0.0,
    pause_on_tooltip=True,
    hover_switch_delay=0.15,
    no_audio_play=False,
    mine_preview=True,
    auto_translate=False,
    prefetch=True,
    layout_engine="default",
)
PROTOCOL = "saitenka.config/v0"
CAPABILITY = "config-v0"
ANSWER_KEYS = frozenset({"expect", "expect_contains"})
MODE_KEYS = {
    "run": (frozenset({"scenario_id", "mode", "config", "flags", "expect"}), True),
    "attach": (frozenset({"scenario_id", "mode", "config", "mine", "expect"}), True),
    "editor-catalog": (frozenset({"scenario_id", "mode", "fields", "expect"}), False),
    "editor-defaults": (frozenset({"scenario_id", "mode", "checks", "expect"}), False),
    "editor-coerce": (frozenset({"scenario_id", "mode", "checks", "expect"}), False),
    "mine-model": (frozenset({"scenario_id", "mode", "configs", "expect"}), False),
    "run-effective": (
        frozenset({"scenario_id", "mode", "scoped_config", "cli", "observe", "expect"}),
        False,
    ),
}


def scenario_request(scenario: dict) -> dict:
    mode = scenario.get("mode")
    spec = MODE_KEYS.get(mode)
    if spec is None or set(scenario) != spec[0]:
        raise ValueError(f"invalid config-v0 scenario keys for {mode!r}")
    payload = {key: value for key, value in scenario.items() if key not in ANSWER_KEYS}
    if spec[1]:
        payload["observe"] = list(scenario["expect"])
    return {"type": "run", "protocol": PROTOCOL, **payload}


def validate_request(request: dict) -> None:
    if ANSWER_KEYS & request.keys():
        raise ValueError("config-v0 requests cannot contain answer keys")
    required = {"type", "protocol", "scenario_id", "mode"}
    if (
        not required <= request.keys()
        or request["type"] != "run"
        or request["protocol"] != PROTOCOL
    ):
        raise ValueError("invalid config-v0 request envelope")
    mode = request["mode"]
    spec = MODE_KEYS.get(mode)
    if spec is None:
        raise ValueError(f"unknown config-v0 mode: {mode!r}")
    allowed = (spec[0] - ANSWER_KEYS) | {"type", "protocol"}
    if spec[1]:
        allowed |= {"observe"}
    if set(request) != allowed:
        raise ValueError(f"invalid config-v0 request keys for {mode!r}")


def evaluate(scenario: dict) -> object:
    validate_request(scenario)
    if scenario["mode"].startswith("editor-"):
        return _evaluate_editor(scenario)
    if scenario["mode"] == "mine-model":
        return [resolve_mine_model(config) for config in scenario["configs"]]
    if scenario["mode"] == "run-effective":
        return _evaluate_run_config(scenario)
    if scenario["mode"] == "run":
        options = _build_run_options(
            scenario["config"], replace(BASE_FLAGS, **scenario.get("flags", {}))
        )
    elif scenario["mode"] == "attach":
        options = _build_attach_options(scenario["config"], mine=scenario.get("mine", {}))
    else:
        raise ValueError(f"unknown config adapter mode: {scenario['mode']!r}")
    paths = scenario["observe"]
    return {path: _read_path(options, path) for path in paths}


def _evaluate_run_config(scenario: dict) -> dict[str, object]:
    cli = scenario.get("cli", {})
    request = RunDepsRequest(
        mine=False,
        mine_deck="",
        mine_model="",
        mine_key="",
        mine_all_key="",
        mine_normalize_audio=False,
        mine_animated_screenshot=False,
        raw_mine={},
        known_cfg=None,
        known="",
        color=True,
        dict_titles=cli.get("dict_titles", []),
        freq_titles=cli.get("freq_titles", []),
        pitch_titles=cli.get("pitch_titles", []),
        scoped_cfg=scenario["scoped_config"],
    )
    effective = _run_effective_config(request)
    return {key: effective[key] for key in scenario["observe"]}


def _evaluate_editor(scenario: dict):
    catalog = config_editor.catalog()
    by_label = {spec.label: spec for spec in catalog}
    by_path = {spec.toml_path: spec for spec in catalog}
    if scenario["mode"] == "editor-catalog":
        return {
            label: {
                "kind": by_label[label].kind,
                "choices": list(by_label[label].choices),
                "path": list(by_label[label].toml_path),
                "optional": by_label[label].optional,
                "help": bool(by_label[label].help),
                "default": by_label[label].default,
            }
            for label in scenario["fields"]
        }
    if scenario["mode"] == "editor-defaults":
        return [
            config_editor.current_default(
                by_path[tuple(check.get("path", [check["field"]]))], check["config"]
            )
            for check in scenario["checks"]
        ]
    if scenario["mode"] == "editor-coerce":
        results = []
        for check in scenario["checks"]:
            try:
                results.append(
                    {"value": config_editor.coerce(by_label[check["field"]], check["input"])}
                )
            except ValueError:
                results.append({"error": "ValueError"})
        return results
    raise ValueError(f"unknown editor mode: {scenario['mode']!r}")


def _read_path(value: object, path: str) -> object:
    for part in path.split("."):
        value = getattr(value, part)
    return value
