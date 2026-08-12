from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


class Transport(Protocol):
    def send(self, payload: dict[str, Any], *, timeout: float) -> Any: ...


class Observer(Protocol):
    def request_started(self, action: str) -> None: ...

    def response_received(self, action: str, size: int) -> None: ...


class PhaseObserver(Protocol):
    def phase(self, name: str, action: str) -> AbstractContextManager[Any]: ...


@runtime_checkable
class PhasedTransport(Protocol):
    def send_phased(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
        observer: PhaseObserver,
    ) -> Any: ...


class UrllibTransport:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def send(self, payload: dict[str, Any], *, timeout: float) -> Any:
        return self._send(payload, timeout=timeout, observer=None)

    def send_phased(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
        observer: PhaseObserver,
    ) -> Any:
        return self._send(payload, timeout=timeout, observer=observer)

    def _send(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
        observer: PhaseObserver | None,
    ) -> Any:
        request = urllib.request.Request(  # noqa: S310 # caller supplies a local AnkiConnect endpoint
            self.endpoint,
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        action = str(payload.get("action", ""))
        if observer is None:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 # local endpoint
                return json.loads(response.read())
        with (
            observer.phase("http_call", action),
            urllib.request.urlopen(request, timeout=timeout) as response,  # noqa: S310 # local endpoint
        ):
            raw = response.read()
        with observer.phase("json_parse", action):
            return json.loads(raw)
