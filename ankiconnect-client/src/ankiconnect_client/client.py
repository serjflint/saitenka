from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from ankiconnect_client.errors import (
    AnkiConnectError,
    AnkiConnectProtocolError,
    AnkiConnectUnavailable,
)
from ankiconnect_client.transport import (
    Observer,
    PhasedTransport,
    PhaseObserver,
    Transport,
    UrllibTransport,
)


class AnkiConnectClient:
    """Synchronous AnkiConnect v6 client with an injectable transport."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:8765",
        api_key: str | None = None,
        *,
        transport: Transport | None = None,
        observer: Observer | None = None,
    ):
        self.endpoint = endpoint
        self.api_key = api_key
        self.transport = transport or UrllibTransport(endpoint)
        self.observer = observer

    def call(
        self,
        action: str,
        *,
        timeout: float = 20,
        attempts: int = 2,
        phase_observer: PhaseObserver | None = None,
        **params: Any,
    ) -> Any:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        payload: dict[str, Any] = {"action": action, "version": 6, "params": params}
        if self.api_key:
            payload["key"] = self.api_key
        for attempt in range(attempts):
            if self.observer is not None:
                self.observer.request_started(action)
            try:
                if phase_observer is not None and isinstance(self.transport, PhasedTransport):
                    response = self.transport.send_phased(
                        payload, timeout=timeout, observer=phase_observer
                    )
                else:
                    response = self.transport.send(payload, timeout=timeout)
            except OSError as exc:
                if attempt + 1 == attempts:
                    raise AnkiConnectUnavailable(
                        f"AnkiConnect unreachable at {self.endpoint}: {exc}"
                    ) from exc
                time.sleep(min(0.3 * (attempt + 1), 1.0))
                continue
            if not isinstance(response, dict) or "result" not in response:
                raise AnkiConnectProtocolError(f"malformed response for {action!r}")
            if self.observer is not None:
                self.observer.response_received(action, len(repr(response)))
            if response.get("error") is not None:
                raise AnkiConnectError(str(response["error"]))
            return response["result"]
        raise AssertionError("unreachable")

    def multi(self, actions: list[dict[str, Any]], **call_options: Any) -> list[Any]:
        result = self.call("multi", actions=actions, **call_options)
        if not isinstance(result, list):
            raise AnkiConnectProtocolError("multi result is not a list")
        return result

    def store_media(self, filename: str, path: str | Path) -> str:
        return str(self.call("storeMediaFile", filename=filename, path=str(Path(path).resolve())))

    def retrieve_media(self, filename: str) -> bytes | None:
        data = self.call("retrieveMediaFile", filename=filename)
        return base64.b64decode(data) if data else None

    def find_notes(self, query: str) -> list[int]:
        return list(self.call("findNotes", query=query) or [])

    def notes_mod_time(self, ids: list[int]) -> list[dict[str, Any]]:
        """`[{"noteId": …, "mod": …}]` — the cheap call a cache diff needs, so reconciling a deck
        does not have to fetch every note's fields to learn which ones changed."""
        return self.call("notesModTime", notes=ids) or []

    def notes_info(self, ids: list[int]) -> list[dict[str, Any]]:
        return list(self.call("notesInfo", notes=ids) or [])

    def model_field_names(self, model: str) -> list[str]:
        return list(self.call("modelFieldNames", modelName=model) or [])

    def can_add(self, note: dict[str, Any]) -> bool:
        return bool((self.call("canAddNotes", notes=[note]) or [False])[0])

    def add_note(self, note: dict[str, Any]) -> int:
        return int(self.call("addNote", note=note))

    def delete_notes(self, ids: list[int]) -> None:
        self.call("deleteNotes", notes=ids)
