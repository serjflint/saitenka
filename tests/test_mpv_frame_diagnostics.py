"""Frame evidence must be complete and belong to the collected session."""

import json

import pytest

from saitenka.app.mpv_frame_diagnostics import (
    collect,
    launch_environment,
    record_player,
    trace_health,
)
from saitenka.mpvio.diagnostics import command_identity


def test_unwritable_diagnostic_directory_does_not_prevent_launch(tmp_path):
    cache = tmp_path / "not-a-directory"
    cache.write_text("occupied")
    assert launch_environment("unused", cache, "current") is None


def test_corrupt_provenance_does_not_abort_a_running_player(tmp_path, caplog):
    from util import FakeIPC

    (tmp_path / "mpv-frame-current.json").write_text("null")
    record_player(FakeIPC(), tmp_path, "current")
    assert "mpv frame provenance unavailable" in caplog.text


@pytest.mark.parametrize("receipt", [[], None, "invalid"])
def test_invalid_frame_receipt_does_not_abort_report(tmp_path, receipt):
    (tmp_path / "mpv-frame-current.json").write_text(json.dumps(receipt))
    assert collect(tmp_path, "current") == {}


def test_overlapping_launches_keep_their_own_player_receipts(tmp_path):
    from util import FakeIPC

    binary = tmp_path / "mpv"
    binary.write_bytes(b"diagnostic binary")
    launch_environment(str(binary), tmp_path, "first")
    launch_environment(str(binary), tmp_path, "second")
    ipc = FakeIPC()
    record_player(ipc, tmp_path, "first")

    first = json.loads(collect(tmp_path, "first")["diagnostics/mpv-frame.json"])
    second = json.loads(collect(tmp_path, "second")["diagnostics/mpv-frame.json"])
    assert first["ipc_connection"] == ipc.diagnostic_id
    assert "ipc_connection" not in second
    assert first["binary_sha256"] == second["binary_sha256"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"", "missing-or-incomplete"),
        (b"row\n", "missing-or-incomplete"),
        (b"row\n# health recorded=1 attempted=1 overflow=0\n", "complete"),
        (b"row\n# health recorded=1 attempted=2 overflow=1\n", "overflow"),
        (b"row\n# health recorded=2 attempted=2 overflow=0\n", "invalid-health"),
    ],
)
def test_frame_health_does_not_promote_partial_evidence(raw, expected):
    assert trace_health(raw) == expected


@pytest.mark.parametrize("session", ["current", "previous"])
def test_frame_bundle_requires_matching_session(tmp_path, session):
    (tmp_path / f"mpv-frame-{session}.json").write_text(json.dumps({"session": "current"}))
    trace = "row\n# health recorded=1 attempted=1 overflow=0\n"
    (tmp_path / "mpv-frame-current.tsv").write_text(trace)
    result = collect(tmp_path, session)
    if session == "current":
        assert result["diagnostics/mpv-frame.tsv"] == trace
        assert json.loads(result["diagnostics/mpv-frame.json"])["probe_status"] == "complete"
    else:
        assert result == {}


def test_payload_identity_uses_the_mpv_probe_fnv1a_protocol():
    assert command_identity(("osd-overlay", 1001, "ass-events", "hello")) == {
        "command": "osd-overlay",
        "overlay_id": "1001",
        "payload_hash": "a430d84680aabd0b",
    }
