"""Sharing boundaries and evidence attribution, through the serialized report artifact."""

from __future__ import annotations

import json
import zipfile

import pytest

from saitenka.app import report, report_schema


def _environment(monkeypatch, tmp_path):
    from saitenka.app import (
        option_evidence,
        player_evidence,
        profile_evidence,
        query_evidence,
        render_evidence,
    )

    monkeypatch.setattr(render_evidence, "registry", render_evidence.EvidenceRegistry())
    monkeypatch.setattr(
        option_evidence, "registry", render_evidence.EvidenceRegistry(kind="session_configuration")
    )
    monkeypatch.setattr(
        profile_evidence, "registry", render_evidence.EvidenceRegistry(kind="profiles")
    )
    monkeypatch.setattr(
        player_evidence, "registry", render_evidence.EvidenceRegistry(kind="player_configuration")
    )
    monkeypatch.setattr(
        query_evidence,
        "registry",
        render_evidence.EvidenceRegistry(kind="player_queries"),
    )
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    config = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(config))
    config.write_text("prefetch = true\n")
    monkeypatch.setattr(
        report_schema,
        "build_identity",
        lambda: {
            "overlay_build": "1.2.3+g1234567",
            "platform": "linux",
            "gil": "off",
            "python_version": "3.14.0",
            "editable": True,
        },
    )
    return config


def _summary(tmp_path, **overrides):
    (tmp_path / "overlay.log").write_text('{"session":"private-session"}\n')
    directory = tmp_path / "diagnostics"
    directory.mkdir(exist_ok=True)
    path = directory / "session-private-session.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "session": "private-session",
                "producer": {},
                "captured_ns": 12345,
                "end": "periodic",
                "pending": {},
                "outcomes": [],
                **overrides,
            }
        )
    )
    return path


def _envelope():
    return json.loads(report.collect()["diagnostics/envelope.json"])


@pytest.mark.parametrize("other_hash", ["a" * 64, "b" * 64])
def test_source_identity_compares_code_fingerprint_not_capture_time(other_hash):
    collector = {"source": {"sha256": "a" * 64, "captured_ns": 100}}
    producer = {"identity": {"source": {"sha256": other_hash, "captured_ns": 200}}}
    result = report_schema.envelope(
        collector=collector, producer=producer, configuration={}, health={}
    )
    assert result["identity_comparison"]["differing_fields"] == (
        [] if other_hash == "a" * 64 else ["source"]
    )


def test_default_zip_excludes_private_payloads_in_every_member(monkeypatch, tmp_path):
    config = _environment(monkeypatch, tmp_path)
    private = "PRIVATE-SENTENCE-秘密-93847"
    config.write_text(f'prefetch = true\nmpv_path = "{private}"\n', encoding="utf-8")
    _summary(
        tmp_path,
        producer={"overlay_build": private, "path": private},
        pending={private: 2},
        outcomes=[{"operation": private, "outcome": private, "count": 3}],
    )
    with (tmp_path / "overlay.log").open("a") as stream:
        stream.write(json.dumps({"session": "private-session", "message": private}) + "\n")
    (tmp_path / "mpv.log").write_text(private, encoding="utf-8")
    (tmp_path / "crashes").mkdir()
    (tmp_path / "crashes" / "faulthandler.log").write_text(private, encoding="utf-8")
    (tmp_path / "telemetry").mkdir()
    (tmp_path / "telemetry" / "trace.json").write_text(private, encoding="utf-8")

    archive = report.build_report_bundle(tmp_path / "reports", timestamp="privacy")

    with zipfile.ZipFile(archive) as bundle:
        members = {name: bundle.read(name).decode() for name in bundle.namelist()}
    assert set(members) == {"MANIFEST.txt", "diagnostics/envelope.json"}
    serialized = json.dumps(members, ensure_ascii=False)
    assert private not in serialized
    assert "private-session" not in serialized
    assert str(tmp_path) not in serialized
    payload = json.loads(members["diagnostics/envelope.json"])
    assert payload["operation_health"]["pending_total"] == 2
    assert payload["operation_health"]["terminal_totals"]["other"] == 3
    assert payload["producer"]["identity"]["overlay_build"] == "unknown"
    assert payload["effective_runtime_configuration"] == {"status": "unknown"}
    assert payload["pixel_fidelity"]["status"] == "unknown"


def test_producer_and_collector_mismatch_survives_export(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    _summary(tmp_path, producer={"overlay_build": "1.2.2+g7654321", "gil": "on"})

    payload = _envelope()

    assert payload["producer"]["identity"]["overlay_build"] == "1.2.2+g7654321"
    assert payload["collector"]["overlay_build"] == "1.2.3+g1234567"
    assert payload["identity_comparison"]["status"] == "different"
    assert set(payload["identity_comparison"]["differing_fields"]) == {"overlay_build", "gil"}


def test_missing_producer_is_unknown_not_collector_identity(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)

    payload = _envelope()

    assert payload["producer"] == {"status": "not-collected"}
    assert payload["operation_health"] == {"status": "not-collected"}
    assert payload["identity_comparison"]["status"] == "unknown"


@pytest.mark.parametrize(
    "identity",
    [
        None,
        [],
        {"overlay_build": "1-PRIVATE_SECRET"},
        {
            "overlay_build": ["private"],
            "gil": ["private"],
            "platform": "private",
            "editable": "private",
        },
    ],
)
def test_identity_schema_does_not_copy_freeform_metadata(identity):
    result = report_schema.safe_identity(identity)

    assert result == {
        "overlay_build": "unknown",
        "python_version": "unknown",
        "platform": "unknown",
        "gil": "unknown",
        "editable": None,
        "loaded_native_runtime": "unknown",
    }


def test_acknowledged_operations_do_not_assert_pixel_fidelity(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    _summary(tmp_path, outcomes=[{"operation": "tooltip", "outcome": "acknowledged", "count": 7}])

    payload = _envelope()

    assert payload["operation_health"]["terminal_totals"]["acknowledged"] == 7
    assert payload["pixel_fidelity"] == {"status": "unknown", "scope": "not-validated"}


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"schema": 0}, "unsupported-schema"),
        ({"schema": True}, "unsupported-schema"),
        ({"session": "different"}, "session-mismatch"),
    ],
)
def test_unrelated_or_unsupported_summary_cannot_claim_health(
    monkeypatch, tmp_path, overrides, status
):
    _environment(monkeypatch, tmp_path)
    _summary(tmp_path, **overrides)

    payload = _envelope()

    assert payload["producer"] == {"status": status}
    assert payload["operation_health"] == {"status": "unavailable"}


@pytest.mark.parametrize(
    "body",
    ['{"pending":', "[" * 2000, " " * (report.DIAGNOSTIC_JSON_LIMIT + 1)],
    ids=["truncated", "deeply-nested", "too-large"],
)
def test_unreadable_summary_is_not_an_empty_success(monkeypatch, tmp_path, body):
    _environment(monkeypatch, tmp_path)
    _summary(tmp_path).write_text(body)

    payload = _envelope()

    assert payload["producer"]["status"] == "unreadable-or-too-large"
    assert payload["operation_health"] == {"status": "unavailable"}


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ("prefetch =", "invalid"),
        ("x" * (128 * 1024 + 1), "too-large"),
    ],
    ids=["invalid", "too-large"],
)
def test_invalid_config_reports_no_effective_values(monkeypatch, tmp_path, body, status):
    config = _environment(monkeypatch, tmp_path)
    config.write_text(body)

    payload = _envelope()

    assert payload["configuration"] == {"status": status}
    assert payload["effective_runtime_configuration"]["status"] == "not-collected"


def test_config_allowlist_rejects_wrong_types_and_nonfinite_values():
    metadata = report_schema.configured_metadata(
        {
            "tip_scale": 10**400,
            "tip_height": float("nan"),
            "prefetch": "true",
            "resync": 1,
            "subtitle_geometry": {"native_visible": 1, "lookahead": True, "cache_max": "3"},
        }
    )

    assert metadata["fields"] == {}


def test_config_values_are_attributed_to_file_not_live_session():
    metadata = report_schema.configured_metadata(
        {
            "tip_scale": 1.3,
            "prefetch": False,
            "subtitle_geometry": {"native_visible": True, "native_formats": "authored-ass"},
        }
    )

    assert metadata["fields"] == {
        key: {"value": value, "origin": "collector-config-file"}
        for key, value in {
            "tip_scale": 1.3,
            "prefetch": False,
            "subtitle_geometry.native_visible": True,
            "subtitle_geometry.native_formats": "authored-ass",
        }.items()
    }


def test_report_reads_supported_tooltip_height_file_key(monkeypatch, tmp_path):
    config = _environment(monkeypatch, tmp_path)
    config.write_text("tip_height = 0.27\ntip_max_frac = 0.99\n")

    payload = _envelope()

    assert payload["configuration"]["fields"] == {
        "tip_height": {"value": 0.27, "origin": "collector-config-file"},
    }


@pytest.mark.parametrize("pending", [{"operation": -1}, {"operation": True}, {"operation": "0"}])
def test_invalid_operation_counts_cannot_claim_clean_health(pending):
    result = report_schema.operation_health({"pending": pending, "outcomes": []})

    assert result["status"] == "invalid"
    assert "pending_total" not in result


def test_disabled_telemetry_reports_not_collected_without_writing_summary(monkeypatch, tmp_path):
    from saitenka import session
    from saitenka.app import telemetry

    _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(session, "session_id", lambda: "private-session")
    monkeypatch.setattr(telemetry, "is_enabled", lambda: False)
    (tmp_path / "overlay.log").write_text('{"session":"private-session"}\n')

    telemetry.save_operation_summary()

    payload = _envelope()
    assert payload["producer"]["status"] == "not-collected"
    assert payload["operation_health"]["status"] == "not-collected"
    assert not (tmp_path / "diagnostics").exists()


def test_timestamp_collision_preserves_original_bundle(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    directory = tmp_path / "reports"
    directory.mkdir()
    destination = directory / "saitenka-report-fixed.zip"
    destination.write_bytes(b"existing user report")

    with pytest.raises(FileExistsError):
        report.build_report_bundle(directory, timestamp="fixed")

    assert destination.read_bytes() == b"existing user report"
    assert list(directory.iterdir()) == [destination]


def test_empty_cli_shutdown_does_not_publish_a_session_summary(monkeypatch, tmp_path):
    from saitenka import session
    from saitenka.app import telemetry

    _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(session, "session_id", lambda: "cli")

    telemetry.save_operation_summary(end="shutdown-observed")

    assert not (tmp_path / "diagnostics").exists()


def test_failed_zip_write_publishes_nothing(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)

    def full_disk(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", full_disk)

    with pytest.raises(OSError, match="disk full"):
        report.build_report_bundle(tmp_path / "reports", timestamp="failed")

    assert list((tmp_path / "reports").iterdir()) == []


@pytest.mark.parametrize("timestamp", ["../../outside", "x" * 81, "/absolute"])
def test_report_timestamp_cannot_escape_destination(tmp_path, timestamp):
    with pytest.raises(ValueError, match="filename component"):
        report.build_report_bundle(tmp_path / "reports", timestamp=timestamp)

    assert not (tmp_path / "reports").exists()
