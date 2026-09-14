"""Consumed player options remain distinct from derived geometry and pixels."""

import json

import pytest
from test_native_subtitles import reader, settle_jobs
from test_render_evidence import _export, _setup
from util import record_spans

from saitenka.app import native_subtitles, player_evidence


def test_every_native_gate_option_has_an_explicit_sharing_policy():
    assert set(native_subtitles.GATE_OPTIONS) == player_evidence.OPTIONS


@pytest.mark.timeout(5)
def test_rejected_native_configuration_still_exports_its_player_inputs(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    session, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-ass-video-aspect-override"] = 1.25
    try:
        session.graph.playback.observe("sub-text", "猫を見る")
        session.graph.cue.settle()
        settle_jobs(session, ipc)
        payload = _export()
    finally:
        session.close()

    fields = payload["player_configuration"]["owners"][0]["configurations"][-1]["fields"]
    assert fields["sub-ass-video-aspect-override"] == {"status": "available", "value": 1.25}
    assert backend.requests == []
    geometry = payload["effective_runtime_configuration"]["owners"][0]
    assert geometry["configurations"] == []
    assert geometry["requested"] == geometry["published"] == {"status": "unknown"}
    assert geometry["renderer_selection"]["history"]


@pytest.mark.timeout(5)
def test_observed_cue_exports_consumed_player_options(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    spans = record_spans(monkeypatch)
    session, ipc, _ = reader(tmp_path)
    ipc.props["options/sub-line-spacing"] = 0.25
    try:
        session.graph.playback.observe("sub-text", "猫を見る")
        session.graph.cue.settle()
        settle_jobs(session, ipc)
        payload = _export()
    finally:
        session.close()

    evidence = payload["player_configuration"]
    owner = evidence["owners"][0]
    row = owner["configurations"][-1]
    assert row["fields"]["sub-line-spacing"] == {"status": "available", "value": 0.25}
    assert row["fields"]["blend-subtitles"] == {"status": "available", "value": False}
    assert row["fields"]["sub-font"] == {"status": "redacted"}
    assert evidence["applied"] == "unknown"
    assert payload["pixel_fidelity"]["status"] == "unknown"
    assert any(
        span["name"] == "player_configuration_read"
        and span["attrs"]["player_configuration_owner"] == owner["owner"]
        and span["attrs"]["player_configuration_revision"] == row["revision"]
        for span in spans
    )
    assert any(
        span["name"] == "subtitle_geometry_decision"
        and span["attrs"]["player_configuration_owner"] == owner["owner"]
        and span["attrs"]["player_configuration_revision"] == row["revision"]
        for span in spans
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, {"status": "unavailable", "reason": "reader-returned-none"}),
        (0, {"status": "available", "value": 0}),
        (False, {"status": "invalid"}),
        (float("inf"), {"status": "invalid"}),
        ("PRIVATE", {"status": "invalid"}),
    ],
)
def test_missing_and_invalid_numeric_values_are_not_zero(value, expected):
    assert player_evidence.fields({"sub-scale": value})["sub-scale"] == expected


def test_repeated_reads_do_not_evict_configuration_history(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = player_evidence.PlayerEvidence()
    for scale in range(6):
        for _ in range(3):
            evidence.record({"sub-scale": scale}, "authored-ass")

    owner = _export()["player_configuration"]["owners"][0]

    assert [row["revision"] for row in owner["configurations"]] == [3, 4, 5, 6]
    assert owner["configurations_evicted"] == 2


def test_closed_geometry_preserves_only_historical_options(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = player_evidence.PlayerEvidence()
    evidence.record({"sub-scale": 1.25}, "converted")

    evidence.close()

    owner = _export()["player_configuration"]["owners"][0]
    assert owner["closed"] is True
    assert owner["configurations"][0]["source_class"] == "converted"


def test_injected_sidecar_values_cannot_leak_text_or_ambiguous_owners(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = player_evidence.PlayerEvidence()
    evidence.record({"sub-font": "PRIVATE", "sub-scale": 1.25}, "authored-ass")
    raw = player_evidence.registry.snapshot()
    raw["owners"][0]["configurations"][0]["fields"]["sub-scale"] = {
        "status": "available",
        "value": "PRIVATE",
    }

    sanitized = player_evidence.safe_snapshot(raw)

    assert "PRIVATE" not in json.dumps(sanitized)
    assert sanitized["owners"][0]["configurations"][0]["fields"]["sub-scale"] == {
        "status": "invalid"
    }


def test_duplicate_owner_sidecar_is_rejected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    evidence = player_evidence.PlayerEvidence()
    evidence.record({"sub-scale": 1.25}, "authored-ass")
    raw = player_evidence.registry.snapshot()
    raw["owners"] *= 2

    assert player_evidence.safe_snapshot(raw) == {"status": "invalid"}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"schema": True},
        {"schema": 1, "owners": "private"},
        {"schema": 1, "owners": [{}]},
        {"schema": 1, "owners": [{}] * 5},
    ],
)
def test_malformed_player_evidence_cannot_claim_observed_settings(raw):
    assert "owners" not in player_evidence.safe_snapshot(raw)


def test_player_owner_retention_and_export_size_are_bounded(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for _ in range(6):
        evidence = player_evidence.PlayerEvidence()
        for scale in range(6):
            evidence.record({"sub-scale": scale}, "converted")

    payload = _export()["player_configuration"]

    assert len(payload["owners"]) == 4
    assert payload["owners_evicted"] == 2
    assert all(len(owner["configurations"]) == 4 for owner in payload["owners"])
    assert len(json.dumps(payload).encode()) < 64 * 1024
