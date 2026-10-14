from __future__ import annotations

from dataclasses import replace

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


def evaluate(scenario: dict) -> dict[str, object]:
    if scenario["mode"] == "run":
        options = _build_run_options(
            scenario["config"], replace(BASE_FLAGS, **scenario.get("flags", {}))
        )
    elif scenario["mode"] == "attach":
        options = _build_attach_options(scenario["config"], mine=scenario.get("mine", {}))
    else:
        raise ValueError(f"unknown config adapter mode: {scenario['mode']!r}")
    return {path: _read_path(options, path) for path in scenario["expect"]}


def _read_path(value: object, path: str) -> object:
    for part in path.split("."):
        value = getattr(value, part)
    return value
