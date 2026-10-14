from __future__ import annotations

from dataclasses import replace

from saitenka.app import config_editor
from saitenka.app.commands.attach import _build_attach_options
from saitenka.app.launch.run import RunFlags, _build_run_options

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


def evaluate(scenario: dict) -> object:
    if scenario["mode"].startswith("editor-"):
        return _evaluate_editor(scenario)
    if scenario["mode"] == "run":
        options = _build_run_options(
            scenario["config"], replace(BASE_FLAGS, **scenario.get("flags", {}))
        )
    elif scenario["mode"] == "attach":
        options = _build_attach_options(scenario["config"], mine=scenario.get("mine", {}))
    else:
        raise ValueError(f"unknown config adapter mode: {scenario['mode']!r}")
    paths = scenario.get("observe", scenario.get("expect", {}))
    return {path: _read_path(options, path) for path in paths}


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
