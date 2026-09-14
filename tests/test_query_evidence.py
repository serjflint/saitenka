"""Gateway replies survive metadata export without their private data or error strings."""

import json

import pytest
from test_mpv_gateway import FakeIPC
from test_render_evidence import _export, _setup
from util import record_spans

from saitenka.app import query_evidence
from saitenka.app.session.mpv_gateway import MpvGateway
from saitenka.app.session.playback_observation import OBSERVED_PROPERTIES
from saitenka.runtime import ConnectionReady, SessionMailbox


def test_query_evidence_properties_are_in_the_production_observer_set():
    assert set(OBSERVED_PROPERTIES) >= query_evidence.PROPERTIES


@pytest.mark.timeout(5)
def test_gateway_subscription_and_read_outcomes_reach_report(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    spans = record_spans(monkeypatch)

    class Replies(FakeIPC):
        def command(self, *args, **kwargs):
            super().command(*args, **kwargs)
            if args[0] == "observe_property":
                return {"error": "property not found", "data": "PRIVATE"}
            return {"error": "success", "data": False}

    ipc = Replies()
    gateway = MpvGateway(ipc, SessionMailbox())
    try:
        replies = gateway.register_observers(("options/sub-scale",))
        payload = _export()
    finally:
        gateway.close()

    owner = payload["player_query_health"]["owners"][0]
    assert [(row["verb"], row["outcome"]) for row in owner["commands"]] == [
        ("observe_property", "property-not-found"),
        ("get_property", "success"),
    ]
    assert ipc.commands == [
        ("observe_property", 1, "options/sub-scale"),
        ("get_property", "options/sub-scale"),
    ]
    assert replies == {"options/sub-scale": {"error": "success", "data": False}}
    assert "PRIVATE" not in json.dumps(payload)
    assert payload["player_query_health"]["applied"] == "unknown"
    assert {
        span["attrs"]["query_sequence"]
        for span in spans
        if span["name"] == "player_property_command"
        and span["attrs"]["query_owner"] == owner["owner"]
    } == {row["sequence"] for row in owner["commands"]}


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"error": "success", "data": None}, "success-null"),
        ({"error": "success"}, "success-missing-data"),
        ({"error": "success", "data": 0}, "success"),
        ({"error": "timeout"}, "timeout"),
        ({"error": "disconnected", "data": False}, "disconnected"),
        ({"error": "stale-epoch"}, "stale-epoch"),
        ({"error": "property unavailable"}, "property-unavailable"),
        ({"error": "PRIVATE"}, "error"),
        ({"data": True}, "malformed"),
        (None, "malformed"),
    ],
)
def test_reply_classification_does_not_invent_values_or_error_causes(reply, expected):
    assert query_evidence.reply_outcome(reply, "get_property") == expected


@pytest.mark.timeout(5)
def test_reconnect_replaces_query_epoch_through_gateway_replay(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ipc = FakeIPC()
    ipc.reconnect_results = [True]
    gateway = MpvGateway(ipc, SessionMailbox())
    gateway.register_observers(("osd-dimensions",))
    try:
        ipc.connection_sink("lost", 0)
        ready = False
        while not ready:
            ready = any(isinstance(event, ConnectionReady) for event in ipc.drain(timeout=0.1))
        payload = _export()
    finally:
        gateway.close()

    owner = payload["player_query_health"]["owners"][0]
    assert owner["connection_epoch"] == 1
    assert [(row["sequence"], row["outcome"]) for row in owner["commands"]] == [
        (3, "success"),
        (4, "success"),
    ]


@pytest.mark.parametrize("new_epoch", [0, 1])
def test_late_reply_cannot_overwrite_a_newer_query(monkeypatch, tmp_path, new_epoch):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()

    def newer_then_old(*_args):
        evidence.command(
            lambda *_args: {"error": "timeout"}, "get_property", "osd-dimensions", epoch=new_epoch
        )
        return {"error": "success", "data": "PRIVATE"}

    evidence.command(newer_then_old, "get_property", "osd-dimensions", epoch=0)

    owner = _export()["player_query_health"]["owners"][0]
    assert owner["connection_epoch"] == new_epoch
    assert owner["stale_completions"] == 1
    assert [(row["sequence"], row["outcome"]) for row in owner["commands"]] == [(2, "timeout")]


def test_pending_command_is_visible_before_reply(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()
    captured = []

    def capture(*_args):
        captured.append(_export()["player_query_health"])
        return {"error": "success", "data": 0}

    evidence.command(capture, "get_property", "osd-dimensions", epoch=0)

    assert captured[0]["owners"][0]["commands"][0]["outcome"] == "pending"
    assert captured[0]["owners"][0]["commands"][0]["completed_ns"] is None


def test_exception_is_recorded_without_swallowing_or_exporting_its_message(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()

    def fail(*_args):
        raise RuntimeError("PRIVATE")

    with pytest.raises(RuntimeError, match="PRIVATE"):
        evidence.command(fail, "get_property", "osd-dimensions", epoch=0)

    payload = _export()
    assert payload["player_query_health"]["owners"][0]["commands"][0]["outcome"] == "exception"
    assert "PRIVATE" not in json.dumps(payload)


def test_close_does_not_promote_late_reply_to_completed_state(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()

    def close_then_reply(*_args):
        evidence.close()
        return {"error": "success"}

    evidence.command(close_then_reply, "get_property", "osd-dimensions", epoch=0)

    owner = _export()["player_query_health"]["owners"][0]
    assert owner["closed"] is True
    assert owner["stale_completions"] == 1
    assert owner["commands"][0]["outcome"] == "pending"


@pytest.mark.parametrize(
    "raw",
    [None, {"schema": True}, {"schema": 1, "owners": [{}]}, {"schema": 1, "owners": [{}] * 5}],
)
def test_malformed_query_evidence_cannot_establish_query_success(raw):
    assert "owners" not in query_evidence.safe_snapshot(raw)


def test_query_export_rejects_ambiguous_command_identity(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()
    evidence.command(lambda *_args: {"error": "success"}, "get_property", "osd-dimensions", epoch=0)
    raw = query_evidence.registry.snapshot()
    raw["owners"][0]["commands"] *= 2

    assert query_evidence.safe_snapshot(raw) == {"status": "invalid"}


def test_serialized_command_extra_fields_and_error_text_are_not_shared(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()
    evidence.command(lambda *_args: {"error": "success"}, "get_property", "osd-dimensions", epoch=0)
    raw = query_evidence.registry.snapshot()
    raw["owners"][0]["commands"][0].update(outcome="PRIVATE", data="PRIVATE", path="PRIVATE")

    result = query_evidence.safe_snapshot(raw)

    assert "PRIVATE" not in json.dumps(result)
    assert result["owners"][0]["commands"][0]["outcome"] == "unknown"


def test_all_evidence_histories_fit_the_report_reader_budget(monkeypatch, tmp_path):
    from dataclasses import replace

    from test_subtitle_pipeline import request

    from saitenka.app import player_evidence, render_evidence

    _setup(monkeypatch, tmp_path)
    for _ in range(6):
        queries = query_evidence.QueryEvidence()
        for name in sorted(query_evidence.PROPERTIES):
            queries.ingress(
                {"event": "property-change", "name": name}, 0, "queued", mailbox_sequence=1
            )
            queries.command(
                lambda *_args: {"error": "success"}, "observe_property", 1, name, epoch=0
            )
            queries.command(
                lambda *_args: {"error": "success", "data": 0}, "get_property", name, epoch=0
            )
        geometry = render_evidence.GeometryEvidence()
        player = player_evidence.PlayerEvidence()
        for width in range(1920, 1926):
            geometry.describe(replace(request(0), frame_size=(width, 1080)))
            player.record({"sub-scale": width}, "authored-ass")

    payload = _export()

    assert payload["producer"]["status"] == "collected"
    assert payload["player_query_health"]["owners_evicted"] == 4
    assert len(payload["player_query_health"]["owners"]) == 2
    assert len((tmp_path / "diagnostics" / "session-runtime-test.json").read_bytes()) <= 128 * 1024
