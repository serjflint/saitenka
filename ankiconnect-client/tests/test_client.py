from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from ankiconnect_client import (
    AnkiConnectClient,
    AnkiConnectError,
    AnkiConnectProtocolError,
    AnkiConnectUnavailable,
)
from ankiconnect_client.transport import UrllibTransport


class FakeTransport:
    def __init__(self, *responses: Any):
        self.responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    def send(self, payload: dict[str, Any], *, timeout: float) -> Any:
        assert timeout > 0
        self.payloads.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_call_frames_v6_request_and_returns_result():
    transport = FakeTransport({"result": [1, 2], "error": None})
    client = AnkiConnectClient(api_key="secret", transport=transport)

    assert client.call("findNotes", query="deck:D") == [1, 2]
    assert transport.payloads == [
        {"action": "findNotes", "version": 6, "params": {"query": "deck:D"}, "key": "secret"}
    ]


def test_application_error_is_not_retried():
    transport = FakeTransport(
        {"result": None, "error": "deck not found"},
        {"result": 1, "error": None},
    )

    with pytest.raises(AnkiConnectError, match="deck not found"):
        AnkiConnectClient(transport=transport).call("addNote", attempts=2)
    assert len(transport.payloads) == 1


def test_transport_error_is_retried_then_typed(monkeypatch):
    transport = FakeTransport(OSError("down"), OSError("still down"))
    monkeypatch.setattr("ankiconnect_client.client.time.sleep", lambda _seconds: None)

    with pytest.raises(AnkiConnectUnavailable, match="still down"):
        AnkiConnectClient(transport=transport).call("version", attempts=2)


def test_malformed_response_is_rejected():
    with pytest.raises(AnkiConnectProtocolError):
        AnkiConnectClient(transport=FakeTransport({"error": None})).call("version")


def test_urllib_transport_observes_http_and_json_parse_separately(monkeypatch):
    phases: list[tuple[str, str]] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"result": 6, "error": null}'

    class Observer:
        @contextmanager
        def phase(self, name: str, action: str):
            phases.append((name, action))
            yield

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())

    result = AnkiConnectClient(transport=UrllibTransport("http://127.0.0.1:8765")).call(
        "version", phase_observer=Observer()
    )

    assert result == 6
    assert phases == [("http_call", "version"), ("json_parse", "version")]
