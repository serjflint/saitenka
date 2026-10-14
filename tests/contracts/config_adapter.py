from __future__ import annotations

import json
import sys
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
PROTOCOL = "saitenka.config/v0"
CAPABILITY = "config-v0"


def evaluate(scenario: dict) -> dict[str, object]:
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


def _read_path(value: object, path: str) -> object:
    for part in path.split("."):
        value = getattr(value, part)
    return value


def main() -> int:
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("type") == "hello":
            if request.get("protocol") != PROTOCOL or request.get("requires") != [CAPABILITY]:
                return 2
            response = {"type": "hello", "protocol": PROTOCOL, "capabilities": [CAPABILITY]}
        elif request.get("type") == "run" and request.get("protocol") == PROTOCOL:
            response = {
                "type": "result",
                "protocol": PROTOCOL,
                "scenario_id": request["scenario_id"],
                "status": "ok",
                "observations": evaluate(request),
            }
        else:
            return 2
        sys.stdout.write(json.dumps(response, separators=(",", ":"), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
