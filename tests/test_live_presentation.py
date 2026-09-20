"""Installed-consumer qualification, separate from the in-process live renderer harness."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(30),
    pytest.mark.skipif(
        os.environ.get("SAITENKA_PRESENTATION") != "1",
        reason="explicit installed presentation qualification",
    ),
]


@pytest.mark.parametrize("mode", ["patched", "shadow", "stock", "navigation", "input-changes"])
def test_installed_consumer_meets_its_declared_presentation_contract(tmp_path, mode):
    variable = "SAITENKA_STOCK_MPV" if mode == "stock" else "SAITENKA_LAYOUT_MPV"
    binary = os.environ.get(variable)
    assert binary, f"required qualification needs {variable}; absence is not a pass"
    command = [
        sys.executable,
        str(Path(__file__).parents[1] / "tools/presentation/run.py"),
        str(tmp_path / "run"),
        "--mpv",
        binary,
    ]
    if mode == "navigation":
        command.append("--navigation")
    if mode == "input-changes":
        command.append("--input-changes")
    if mode == "stock":
        command.append("--stock")
    if mode == "shadow":
        command.extend(("--source", "shadow", "--delay", "-1"))
    result = subprocess.run(command, check=False, timeout=28)
    if mode == "input-changes":
        qualification = json.loads(
            (tmp_path / "run/qualification.json").read_text(encoding="utf-8")
        )
        assert result.returncode == 1
        assert qualification["qualified"] is False
        assert qualification["scenario_complete"] is True
        assert qualification["reason"] == "native epoch cutover is unproven"
    else:
        assert result.returncode == 0
