"""Wire ingress is distinct from registration, replay reads and applied pixels."""

import json

import pytest
from test_mpv_gateway import FakeIPC
from test_render_evidence import _export, _setup
from util import record_spans

from saitenka.app import query_evidence
from saitenka.app.session.mpv_gateway import MpvGateway
from saitenka.runtime import CloseRequested, ConnectionReady, PropertyObserved, SessionMailbox


def test_wire_observation_reaches_report_with_mailbox_identity(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    spans = record_spans(monkeypatch)
    ipc, mailbox = FakeIPC(), SessionMailbox()
    gateway = MpvGateway(ipc, mailbox)
    try:
        ipc.publish({"event": "property-change", "name": "sub-text/ass-full", "data": "PRIVATE"})
        envelope = mailbox.receive(timeout=0)
        payload = _export()
    finally:
        gateway.close()

    owner = payload["player_query_health"]["owners"][0]
    row = owner["ingress"]["recent"][0]
    assert envelope is not None and envelope.payload == PropertyObserved(
        "sub-text/ass-full", "PRIVATE"
    )
    assert (row["source"], row["outcome"], row["mailbox_sequence"]) == (
        "wire",
        "queued",
        envelope.sequence,
    )
    assert owner["ingress"]["counts"]["queued"] == 1
    assert payload["player_query_health"]["applied"] == "unknown"
    assert "PRIVATE" not in json.dumps(payload) + json.dumps(spans)
    assert [
        (
            span["attrs"]["query_owner"],
            span["attrs"]["ingress_sequence"],
            span["attrs"]["mailbox_sequence"],
        )
        for span in spans
        if span["name"] == "player_property_ingress"
    ] == [(owner["owner"], row["sequence"], envelope.sequence)]


@pytest.mark.parametrize(
    ("closed", "epoch", "expected"), [(False, 1, "stale-epoch"), (True, 0, "closed")]
)
def test_rejected_wire_event_is_not_reported_as_queued(
    monkeypatch, tmp_path, closed, epoch, expected
):
    _setup(monkeypatch, tmp_path)
    ipc, mailbox = FakeIPC(), SessionMailbox()
    gateway = MpvGateway(ipc, mailbox)
    if closed:
        gateway.close()
    try:
        ipc.publish({"event": "property-change", "name": "osd-dimensions", "data": {}}, epoch=epoch)
        payload = _export()
    finally:
        gateway.close()

    ingress = payload["player_query_health"]["owners"][0]["ingress"]
    assert mailbox.receive(timeout=0) is None
    assert ingress["counts"][expected] == 1 and ingress["counts"]["queued"] == 0
    assert ingress["recent"][0]["mailbox_sequence"] is None
    assert ingress["recent"][0]["connection_epoch"] == epoch


def test_full_mailbox_reports_failed_admission_and_preserves_close(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ipc, mailbox = FakeIPC(), SessionMailbox(normal_capacity=1)
    gateway = MpvGateway(ipc, mailbox)
    ipc.publish({"event": "file-loaded"})
    try:
        ipc.publish({"event": "property-change", "name": "osd-dimensions", "data": {}})
        payload = _export()
    finally:
        gateway.close()

    ingress = payload["player_query_health"]["owners"][0]["ingress"]
    assert ingress["counts"]["mailbox-full"] == 1 and ingress["counts"]["queued"] == 0
    assert isinstance(mailbox.receive(timeout=0).payload, CloseRequested)


@pytest.mark.timeout(5)
def test_reconnect_read_is_not_reported_as_a_wire_notification(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ipc = FakeIPC()
    ipc.reconnect_results = [True]
    gateway = MpvGateway(ipc, SessionMailbox())
    gateway.register_observers(("osd-dimensions",))
    try:
        ipc.connection_sink("lost", 0)
        while not any(isinstance(event, ConnectionReady) for event in ipc.drain(timeout=0.1)):
            pass
        payload = _export()
    finally:
        gateway.close()

    row = payload["player_query_health"]["owners"][0]["ingress"]["recent"][0]
    assert (row["source"], row["outcome"], row["connection_epoch"]) == ("replay-read", "queued", 1)


@pytest.mark.timeout(5)
def test_reconnect_buffering_is_not_mailbox_admission(monkeypatch, tmp_path):
    import threading

    _setup(monkeypatch, tmp_path)
    ipc = FakeIPC()
    ipc.reconnect_results = [True]
    gateway = MpvGateway(ipc, SessionMailbox())
    gateway.register_observers(("osd-dimensions",))
    ipc.replay_entered, ipc.replay_release = threading.Event(), threading.Event()
    ipc.connection_sink("lost", 0)
    try:
        assert ipc.replay_entered.wait(1)
        ipc.publish({"event": "property-change", "name": "osd-dimensions", "data": {}}, epoch=1)
        buffered = query_evidence.safe_snapshot(query_evidence.registry.snapshot())
        ipc.replay_release.set()
        while not any(isinstance(event, ConnectionReady) for event in ipc.drain(timeout=0.1)):
            pass
        payload = _export()
    finally:
        ipc.replay_release.set()
        gateway.close()

    assert buffered["owners"][0]["ingress"]["counts"]["queued"] == 0
    rows = payload["player_query_health"]["owners"][0]["ingress"]["recent"]
    assert [(row["source"], row["outcome"]) for row in rows] == [
        ("wire", "buffered"),
        ("replay-read", "queued"),
        ("wire", "queued"),
    ]


def test_untracked_property_names_and_values_never_enter_evidence(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, SessionMailbox())
    try:
        ipc.publish({"event": "property-change", "name": "PRIVATE", "data": "PRIVATE"})
        payload = _export()
    finally:
        gateway.close()

    assert payload["player_query_health"] == {"status": "unknown"}


def test_ingress_history_eviction_keeps_lifetime_stage_counts(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, SessionMailbox())
    try:
        for _ in range(20):
            ipc.publish({"event": "property-change", "name": "osd-dimensions"})
        ingress = _export()["player_query_health"]["owners"][0]["ingress"]
    finally:
        gateway.close()

    assert ingress["counts"]["queued"] == 20
    assert ingress["evicted"] == 12
    assert [row["sequence"] for row in ingress["recent"]] == list(range(13, 21))


def test_candidate_overflow_is_not_counted_as_buffered_or_queued(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ipc, mailbox = FakeIPC(), SessionMailbox()
    gateway = MpvGateway(ipc, mailbox)
    ipc.connection_sink("replaced", 1)
    try:
        for _ in range(257):
            ipc.publish({"event": "property-change", "name": "osd-dimensions"}, epoch=1)
        ingress = _export()["player_query_health"]["owners"][0]["ingress"]
    finally:
        gateway.close()

    assert ingress["counts"]["buffered"] == 256
    assert ingress["counts"]["candidate-full"] == 1
    assert ingress["counts"]["queued"] == 0
    assert isinstance(mailbox.receive(timeout=0).payload, CloseRequested)


@pytest.mark.timeout(5)
def test_observation_during_connection_attempt_is_not_ready(monkeypatch, tmp_path):
    import threading

    _setup(monkeypatch, tmp_path)
    recorded = threading.Event()

    class Connecting(FakeIPC):
        def reconnect_once(self):
            self.publish({"event": "property-change", "name": "osd-dimensions"})
            recorded.set()
            return False

    ipc = Connecting()
    gateway = MpvGateway(ipc, SessionMailbox())
    try:
        ipc.connection_sink("lost", 0)
        assert recorded.wait(1)
        ingress = _export()["player_query_health"]["owners"][0]["ingress"]
    finally:
        gateway.close()

    assert ingress["counts"]["not-ready"] == 1
    assert ingress["counts"]["queued"] == 0


def test_mailbox_exception_is_retained_without_swallowing_it(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    class BrokenMailbox(SessionMailbox):
        def publish(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE")

    ipc = FakeIPC()
    gateway = MpvGateway(ipc, BrokenMailbox())
    try:
        with pytest.raises(RuntimeError, match="PRIVATE"):
            ipc.publish({"event": "property-change", "name": "osd-dimensions"})
        payload = _export()
    finally:
        gateway.close()

    assert payload["player_query_health"]["owners"][0]["ingress"]["counts"]["exception"] == 1
    assert "PRIVATE" not in json.dumps(payload)


@pytest.mark.timeout(5)
def test_concurrent_ingress_does_not_lose_counts_or_reuse_identity(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    _setup(monkeypatch, tmp_path)
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, SessionMailbox())
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(
                pool.map(
                    lambda _: ipc.publish({"event": "property-change", "name": "osd-dimensions"}),
                    range(32),
                )
            )
        ingress = _export()["player_query_health"]["owners"][0]["ingress"]
    finally:
        gateway.close()

    assert ingress["counts"]["queued"] == 32
    assert [row["sequence"] for row in ingress["recent"]] == list(range(25, 33))


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "name", "source", "outcome", "sequence", "counts", "mailbox", "zero", "evicted"],
)
def test_malformed_ingress_cannot_establish_delivery(monkeypatch, tmp_path, mutation):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()
    evidence.ingress(
        {"event": "property-change", "name": "osd-dimensions"}, 0, "queued", mailbox_sequence=0
    )
    raw = query_evidence.registry.snapshot()
    ingress = raw["owners"][0]["ingress"]
    row = ingress["recent"][0]
    if mutation == "duplicate":
        ingress["recent"] *= 2
    elif mutation == "counts":
        ingress["counts"]["queued"] = True
    elif mutation == "zero":
        ingress["counts"]["queued"] = 0
    elif mutation == "evicted":
        ingress["evicted"] = 1
    else:
        field = {"name": "property", "mailbox": "mailbox_sequence"}.get(mutation, mutation)
        row[field] = "PRIVATE"

    assert query_evidence.safe_snapshot(raw)["owners"][0]["ingress"] == {"status": "invalid"}


def test_legacy_summary_does_not_claim_zero_notifications(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()
    evidence.command(lambda *_: {"error": "success"}, "get_property", "osd-dimensions", epoch=0)
    raw = query_evidence.registry.snapshot()
    del raw["owners"][0]["ingress"]

    assert query_evidence.safe_snapshot(raw)["owners"][0]["ingress"] == {"status": "unknown"}


def test_serialized_ingress_extra_fields_cannot_leak_content(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()
    evidence.ingress(
        {"event": "property-change", "name": "osd-dimensions"}, 0, "queued", mailbox_sequence=0
    )
    raw = query_evidence.registry.snapshot()
    ingress = raw["owners"][0]["ingress"]
    ingress["counts"]["PRIVATE"] = 1
    ingress["recent"][0].update(data="PRIVATE", path="PRIVATE")

    result = query_evidence.safe_snapshot(raw)

    assert result["owners"][0]["ingress"]["status"] == "collected"
    assert "PRIVATE" not in json.dumps(result)


def test_legacy_ingress_preserves_admission_but_cannot_claim_projection(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = query_evidence.QueryEvidence()
    evidence.ingress(
        {"event": "property-change", "name": "osd-dimensions"}, 0, "queued", mailbox_sequence=1
    )
    raw = query_evidence.registry.snapshot()
    ingress = raw["owners"][0]["ingress"]
    del ingress["schema"]
    for key in ("reduced", "projection-closed", "projection-stale-epoch", "projection-exception"):
        del ingress["counts"][key]

    result = query_evidence.safe_snapshot(raw)["owners"][0]["ingress"]

    assert result["status"] == "collected"
    assert result["counts"]["queued"] == 1
    assert result["counts"]["reduced"] is None
