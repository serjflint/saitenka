from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

if __package__:
    from . import cli_adapter, config_adapter, saitenka_session_adapter
    from . import protocol as session_protocol
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cli_adapter
    import config_adapter
    import protocol as session_protocol
    import saitenka_session_adapter

Adapter = tuple[str, Callable[[dict], object]]
ADAPTERS: dict[str, Adapter] = {
    saitenka_session_adapter.PROTOCOL: (
        session_protocol.CAPABILITY,
        saitenka_session_adapter.execute,
    ),
    config_adapter.PROTOCOL: (config_adapter.CAPABILITY, config_adapter.evaluate),
    cli_adapter.PROTOCOL: (cli_adapter.CAPABILITY, cli_adapter.evaluate),
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
