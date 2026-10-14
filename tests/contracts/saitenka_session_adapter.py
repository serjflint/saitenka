from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
ADAPTER_DIR = Path(__file__).resolve().parent
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from protocol import CAPABILITY, PROTOCOL, compact_json  # noqa: E402
from session_builder import build_session  # noqa: E402
from util import FakeIPC, session_gateway  # noqa: E402

from saitenka.app import subtitle_adapter  # noqa: E402
from saitenka.app.config import ReaderOptions  # noqa: E402
from saitenka.app.session.factory import SessionInfrastructure  # noqa: E402
from saitenka.app.subtitle_render import NullRenderer  # noqa: E402
from saitenka.runtime import ConnectionLost, ConnectionReady, ConnectionReplaced  # noqa: E402


def _json_value(value: object) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    return {"type": type(value).__name__}


class SaitenkaSessionAdapter:
    """Drive the assembled product and expose only its boundary transcript."""

    def __init__(self, given: dict) -> None:
        self.ipc = FakeIPC()
        self.ipc.props.update(given.get("mpv_properties", {}))
        self._capture = frozenset(given.get("capture", ["all"]))
        self.gateway = session_gateway(self.ipc)
        options = ReaderOptions().with_overrides(prefetch=False)
        self.session = build_session(
            self.ipc,
            options=options,
            infrastructure=SessionInfrastructure(renderer=NullRenderer()),
        )
        self.observations: list[dict] = []
        self._command_cursor = 0
        self._surface_ids: dict[object, str] = {}
        self._original_copy = subtitle_adapter.copy_clipboard
        subtitle_adapter.copy_clipboard = self._copy
        self._closed = False

    def _copy(self, text: str) -> None:
        self._append("clipboard", {"source": "clipboard", "kind": "write", "text": text})

    def _append(self, category: str, observation: dict) -> None:
        if "all" in self._capture or category in self._capture:
            self.observations.append(observation)

    def _collect_commands(self) -> None:
        for command in self.ipc.commands[self._command_cursor :]:
            normalized = self._normalize_command(command)
            if normalized is None:
                continue
            category, value = normalized
            self._append(
                category,
                {"source": "mpv", "kind": "command", "value": value},
            )
        self._command_cursor = len(self.ipc.commands)

    def _surface_id(self, raw: object) -> str:
        return self._surface_ids.setdefault(raw, f"surface-{len(self._surface_ids) + 1}")

    def _normalize_command(self, command: tuple) -> tuple[str, Any] | None:
        if command and command[0] == "observe_property" and len(command) >= 3:
            return "observer", ["observe-property", command[2]]
        if command and command[0] == "overlay-add":
            return "surface", ["surface-present", self._surface_id(command[1])]
        if command and command[0] == "overlay-remove":
            if command[1] not in self._surface_ids:
                return None
            return "surface", ["surface-absent", self._surface_id(command[1])]
        if command and command[0] == "osd-overlay":
            surface = self._surface_id(command[1])
            state = (
                "surface-absent" if len(command) > 2 and command[2] == "none" else "surface-present"
            )
            return "surface", [state, surface]
        if command and command[0] in {"define-section", "enable-section"}:
            return "binding", [command[0], command[1]]
        if command and command[0] == "show-text":
            return "notice", ["notice", command[1]]
        if command and command[0] == "sub-seek":
            return "seek", ["sub-seek", str(command[1])]
        return "mpv", _json_value(command)

    def perform(self, action: dict) -> None:
        name = action["action"]
        if name == "start":
            self.session.start()
        elif name == "emit_batch":
            for event in action["events"]:
                self.ipc.emit(event)
        elif name == "command":
            self.session.command(action["name"])
        elif name == "transport_lost":
            self.ipc.publish_runtime_event(ConnectionLost(action["epoch"]))
        elif name == "transport_ready":
            self.ipc.publish_runtime_event(ConnectionReady(action["epoch"]))
        elif name == "transport_replaced":
            self.ipc.publish_runtime_event(ConnectionReplaced(action["epoch"]))
        elif name == "drain_until_quiescent":
            unchanged = 0
            for _ in range(action["max_steps"]):
                before = len(self.ipc.commands)
                if not self.session.pump():
                    break
                unchanged = unchanged + 1 if len(self.ipc.commands) == before else 0
                if unchanged == 2:
                    break
            else:
                raise RuntimeError("session did not become boundary-quiescent")
        elif name == "close":
            self.close()
        elif name == "checkpoint":
            self._append(
                "checkpoint",
                {"source": "driver", "kind": "checkpoint", "name": action["name"]},
            )
        else:
            raise ValueError(f"unsupported session-v0 action: {name}")
        self._collect_commands()

    def run(self, actions: list[dict]) -> list[dict]:
        try:
            for action in actions:
                self.perform(action)
            return self.observations
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.session.close()
            self._collect_commands()
            self._append("termination", {"source": "adapter", "kind": "terminated"})
        finally:
            subtitle_adapter.copy_clipboard = self._original_copy
            self.gateway.close()


def execute(request: dict) -> dict:
    adapter = SaitenkaSessionAdapter(request["given"])
    observations = adapter.run(request["when"])
    return {
        "type": "result",
        "protocol": PROTOCOL,
        "scenario_id": request["scenario_id"],
        "status": "ok",
        "observations": observations,
    }


def main() -> int:
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("type") == "hello":
            if request.get("protocol") != PROTOCOL or CAPABILITY not in request.get("requires", []):
                return 2
            sys.stdout.write(
                compact_json({"type": "hello", "protocol": PROTOCOL, "capabilities": [CAPABILITY]})
                + "\n"
            )
            continue
        if request.get("type") != "run" or request.get("protocol") != PROTOCOL:
            return 2
        sys.stdout.write(compact_json(execute(request)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
