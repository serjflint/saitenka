from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

if __package__:
    from . import (
        analysis_adapter,
        cli_adapter,
        config_adapter,
        dictionary_adapter,
        history_adapter,
        picker_adapter,
        saitenka_session_adapter,
        sidebar_adapter,
        subtitle_adapter,
        tooltip_adapter,
        wordstate_adapter,
    )
    from . import protocol as session_protocol
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import analysis_adapter
    import cli_adapter
    import config_adapter
    import dictionary_adapter
    import history_adapter
    import picker_adapter
    import protocol as session_protocol
    import saitenka_session_adapter
    import sidebar_adapter
    import subtitle_adapter
    import tooltip_adapter
    import wordstate_adapter

Adapter = tuple[str, Callable[[dict], object]]
ADAPTERS: dict[str, Adapter] = {
    analysis_adapter.PROTOCOL: (analysis_adapter.CAPABILITY, analysis_adapter.evaluate),
    saitenka_session_adapter.PROTOCOL: (
        session_protocol.CAPABILITY,
        saitenka_session_adapter.execute,
    ),
    config_adapter.PROTOCOL: (config_adapter.CAPABILITY, config_adapter.evaluate),
    dictionary_adapter.PROTOCOL: (dictionary_adapter.CAPABILITY, dictionary_adapter.evaluate),
    history_adapter.PROTOCOL: (history_adapter.CAPABILITY, history_adapter.evaluate),
    picker_adapter.PROTOCOL: (picker_adapter.CAPABILITY, picker_adapter.evaluate),
    sidebar_adapter.PROTOCOL: (sidebar_adapter.CAPABILITY, sidebar_adapter.evaluate),
    cli_adapter.PROTOCOL: (cli_adapter.CAPABILITY, cli_adapter.evaluate),
    subtitle_adapter.PROTOCOL: (subtitle_adapter.CAPABILITY, subtitle_adapter.evaluate),
    tooltip_adapter.PROTOCOL: (tooltip_adapter.CAPABILITY, tooltip_adapter.evaluate),
    wordstate_adapter.PROTOCOL: (wordstate_adapter.CAPABILITY, wordstate_adapter.evaluate),
}


def _result(protocol: str, request: dict, execute: Callable[[dict], object]) -> dict:
    result = execute(request)
    if isinstance(result, dict) and result.get("type") == "result":
        return result
    return {
        "type": "result",
        "protocol": protocol,
        "scenario_id": request["scenario_id"],
        "status": "ok",
        "observations": result,
    }


def main() -> int:
    active: tuple[str, Adapter] | None = None
    for line in sys.stdin:
        request = json.loads(line)
        protocol = request.get("protocol")
        adapter = ADAPTERS.get(protocol)
        if request.get("type") == "hello":
            if adapter is None or request.get("requires") != [adapter[0]]:
                return 2
            active = protocol, adapter
            response = {"type": "hello", "protocol": protocol, "capabilities": [adapter[0]]}
        elif request.get("type") == "run" and active is not None and protocol == active[0]:
            if {"expect", "expect_contains"} & request.keys():
                return 2
            response = _result(protocol, request, active[1][1])
        else:
            return 2
        sys.stdout.write(json.dumps(response, separators=(",", ":"), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
