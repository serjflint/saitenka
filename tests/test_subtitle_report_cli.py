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
