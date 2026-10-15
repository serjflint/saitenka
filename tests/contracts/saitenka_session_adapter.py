from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from saitenka_card import MineConfig

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
ADAPTER_DIR = Path(__file__).resolve().parent
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

if __package__:
    from .protocol import PROTOCOL, validate_request
else:
    from protocol import PROTOCOL, validate_request
from session_builder import build_session  # noqa: E402
from util import FakeIPC, session_gateway  # noqa: E402

from saitenka.app import subtitle_adapter  # noqa: E402
from saitenka.app.config import KeyOptions, ReaderOptions  # noqa: E402
from saitenka.app.session.factory import SessionInfrastructure, SessionServices  # noqa: E402
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
        self._binding_keys = tuple(given.get("capture_binding_keys", ()))
        self._binding_sections: dict[str, tuple[str | None, dict[str, str], bool]] = {}
        self._legacy_bindings: dict[str, str] = {}
        self._bindings_reported = False
        self.anki = _BoundaryAnki() if "mining" in given else None
        self._anki_cursor = 0
        self.gateway = session_gateway(self.ipc)
        options = ReaderOptions(keys=KeyOptions(**given.get("key_options", {}))).with_overrides(
            prefetch=False
        )
        self.session = build_session(
            self.ipc,
            options=options,
            services=(
                SessionServices(
                    anki=self.anki,
                    mining=MineConfig(**given["mining"].get("config", {})),
                )
                if self.anki is not None
                else SessionServices()
            ),
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
            observation = (
                value
                if isinstance(value, dict)
                else {"source": "mpv", "kind": "command", "value": value}
            )
            self._append(category, observation)
        self._command_cursor = len(self.ipc.commands)
        if self._binding_keys and self._binding_sections and not self._bindings_reported:
            self._bindings_reported = True
            self._append(
                "binding",
                {
                    "source": "mpv",
                    "kind": "binding-transport",
                    "legacy_keybind_count": len(self._legacy_bindings),
                },
            )
            for section, (flags, bindings, unique) in self._binding_sections.items():
                self._append(
                    "binding",
                    {
                        "source": "mpv",
                        "kind": "binding-section",
                        "name": section,
                        "flags": flags,
                        "binding_count": len(bindings),
                        "unique_keys": unique,
                    },
                )
            all_bindings = {
                key: (section, message)
                for section, (_flags, bindings, _unique) in self._binding_sections.items()
                for key, message in bindings.items()
            }
            all_bindings.update(
                {key: ("legacy-keybind", message) for key, message in self._legacy_bindings.items()}
            )
            for key in self._binding_keys:
                found = all_bindings.get(key)
                observation = {"source": "mpv", "kind": "binding", "key": key}
                if found is None:
                    observation["status"] = "absent"
                else:
                    observation.update(section=found[0], message=found[1], status="present")
                self._append("binding", observation)
        self._collect_anki()

    def _collect_anki(self) -> None:
        if self.anki is None:
            return
        for note in self.anki.added[self._anki_cursor :]:
            self._append(
                "anki",
                {"source": "anki", "kind": "add-note", "note": _json_value(note)},
            )
        self._anki_cursor = len(self.anki.added)

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
        if command and command[0] == "define-section":
            bindings = {}
            keys = []
            for line in command[2].splitlines():
                key, separator, action = line.partition(" ")
                if separator and action.startswith("script-message "):
                    keys.append(key)
                    bindings[key] = action.removeprefix("script-message ")
            self._binding_sections[command[1]] = (
                command[3] if len(command) > 3 else None,
                bindings,
                len(keys) == len(set(keys)),
            )
            return None
        if command and command[0] == "enable-section":
            return "binding", {
                "source": "mpv",
                "kind": "binding-section-enabled",
                "name": command[1],
            }
        if command and command[0] == "keybind":
            action = command[2] if len(command) > 2 else ""
            self._legacy_bindings[command[1]] = action.removeprefix("script-message ")
            return None
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
        elif name == "await_notes":
            for _ in range(action["max_steps"]):
                self.session.pump(0.01)
                self._collect_anki()
                if self.anki is not None and len(self.anki.added) >= action["count"]:
                    break
            else:
                raise RuntimeError("mining did not reach the Anki boundary")
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


class _BoundaryAnki:
    def __init__(self) -> None:
        self.added: list[dict] = []
        self.stored: list[str] = []

    def find_notes(self, _query: str) -> list[int]:
        return []

    def notes_info(self, _ids: list[int]) -> list[dict]:
        return []

    def can_add(self, _note: dict) -> bool:
        return True

    def add_note(self, note: dict) -> int:
        self.added.append(note)
        return len(self.added)

    def store_media(self, name: str, _path: object) -> str:
        self.stored.append(name)
        return name


def execute(request: dict) -> dict:
    validate_request(request)
    adapter = SaitenkaSessionAdapter(request["given"])
    observations = adapter.run(request["when"])
    return {
        "type": "result",
        "protocol": PROTOCOL,
        "scenario_id": request["scenario_id"],
        "status": "ok",
        "observations": observations,
    }
