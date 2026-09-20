from __future__ import annotations

import json
from typing import TYPE_CHECKING

from saitenka.app.commands.diagnostics import subtitle_report
from saitenka.app.subtitle_report import geometry_records, load_trace

if TYPE_CHECKING:
    from pathlib import Path


def _trace(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "traceEvents": [
                    {
                        "name": "subtitle_pixel_ownership",
                        "ph": "X",
                        "ts": 1,
                        "dur": 0,
                        "args": {
                            "event": "legacy-stage-result",
                            "owner_before": "unknown",
                            "owner_after": "legacy",
                            "visibility": "false",
                            "accepted": True,
                            "selection": "must not be emitted",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_installed_subtitle_report_command_explains_ownership(tmp_path: Path, capsys) -> None:
    trace = _trace(tmp_path / "trace.json")

    assert subtitle_report(str(trace)) == 0

    output = capsys.readouterr().out
    assert "legacy-stage-result: unknown -> legacy" in output
    assert "must not be emitted" not in output


def test_installed_subtitle_report_command_requires_prior_telemetry(tmp_path: Path, capsys) -> None:
    assert subtitle_report(str(tmp_path)) == 1

    assert "enable telemetry before reproducing" in capsys.readouterr().err


def test_installed_subtitle_report_command_rejects_malformed_archive(
    tmp_path: Path, capsys
) -> None:
    malformed = tmp_path / "broken.zip"
    malformed.write_text("not a zip", encoding="utf-8")

    assert subtitle_report(str(malformed)) == 1

    assert "not a valid report archive" in capsys.readouterr().err


def test_geometry_records_are_text_free(tmp_path: Path) -> None:
    records = geometry_records(load_trace(_trace(tmp_path / "trace.json")))

    assert records == [
        {
            "name": "subtitle_pixel_ownership",
            "ts": 1,
            "args": {
                "event": "legacy-stage-result",
                "owner_before": "unknown",
                "owner_after": "legacy",
                "visibility": "false",
                "accepted": True,
            },
        }
    ]


def test_source_report_separates_native_scanning_from_shadow_paint(tmp_path: Path, capsys):
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps(
            {
                "traceEvents": [
                    {
                        "name": "subtitle_geometry_source",
                        "dur": 0,
                        "ph": "X",
                        "ts": 1,
                        "args": {
                            "configured_source": "auto",
                            "selected_source": "mpv",
                            "scan_source": "mpv",
                            "paint_source": "shadow",
                            "paint_allowed": True,
                            "paint_reason": "eligible",
                            "reason": "scan-only",
                            "eligible_tokens": 3,
                            "cue_revision": 7,
                            "generation": 9,
                            "text": "private subtitle",
                        },
                    }
                ]
            }
        )
    )

    assert subtitle_report(str(trace)) == 0

    output = capsys.readouterr().out
    assert "configured=auto selected=mpv scan=mpv paint=shadow" in output
    assert "cue=7 generation=9" in output
    assert "private subtitle" not in output
    assert "text" not in geometry_records(load_trace(trace))[0]["args"]


def test_deeply_nested_metadata_envelope_reports_unavailable(tmp_path: Path, capsys):
    import zipfile

    bundle = tmp_path / "nested.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("diagnostics/envelope.json", "[" * 2000 + "0" + "]" * 2000)

    assert subtitle_report(str(bundle)) == 1

    assert "no telemetry trace found" in capsys.readouterr().err
