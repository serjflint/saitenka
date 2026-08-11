from __future__ import annotations

from typing import Any

import pytest
from ankiconnect_client import (
    AnkiConnectClient,
    AnkiConnectError,
    AnkiConnectProtocolError,
    AnkiConnectUnavailable,
)


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
