"""Profile commands execute through the public CLI and persist actual TOML files."""

import json
import re
import tomllib
from pathlib import Path

import pytest

from saitenka.app.cli import create_app

DATA = json.loads((Path(__file__).parents[2] / "fixtures/cli_reference.json").read_text())[
    "profile-commands"
]


@pytest.mark.parametrize("record", DATA, ids=lambda r: r["id"])
def test_profile_command_output_and_persistence(tmp_path, monkeypatch, capsys, record):
    path = tmp_path / "overlay.toml"
    initial = record["input"].get("config")
    if initial is not None:
        path.write_text(initial)
    monkeypatch.setenv("SAITENKA_CONFIG", str(path))
    app = create_app()
    expected = record["expected"]
    if expected["error"] is None:
        assert (
            app(record["input"]["args"], exit_on_error=False, result_action="return_value")
            == expected["exit_code"]
        )
    else:
        with pytest.raises((TypeError, ValueError), match=re.escape(expected["error"])):
            app(record["input"]["args"], exit_on_error=False, result_action="return_value")
    raw = path.read_text() if path.exists() else ""
    assert (tomllib.loads(raw) if raw else None) == expected["config"]
    stdout = [
        "backed up existing config" if line.startswith("backed up existing config") else line
        for line in capsys.readouterr().out.splitlines()
    ]
    assert stdout == expected["stdout"]
