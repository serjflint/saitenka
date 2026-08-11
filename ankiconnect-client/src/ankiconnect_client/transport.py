from __future__ import annotations

import json
import urllib.request
from typing import Any, Protocol


class Transport(Protocol):
    def send(self, payload: dict[str, Any], *, timeout: float) -> Any: ...


class Observer(Protocol):
    def request_started(self, action: str) -> None: ...

    def response_received(self, action: str, size: int) -> None: ...


class UrllibTransport:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def send(self, payload: dict[str, Any], *, timeout: float) -> Any:
        request = urllib.request.Request(  # noqa: S310 # caller supplies a local AnkiConnect endpoint
            self.endpoint,
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 # local endpoint
            return json.loads(response.read())
