"""Planted +/- controls for the uv-only PreToolUse hook (.agents/hooks/block-bare-python.py).

A restraint hook is only worth its denials if it bites the footgun AND stays silent on the
legitimate forms — a false deny that blocks `uv run python` or `.venv/bin/python` is worse than the
prose it replaces. So this pins both edges: bare `pip`/`python` invocations are caught; uv-prefixed,
path-qualified, argument-position, and quoted mentions are not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".agents" / "hooks" / "block-bare-python.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_block_bare_python", _HOOK)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# --- negative controls: the footgun is caught ----------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "pip install requests",
        "pip3 install -r reqs.txt",
        "python foo.py",
        "python3 -m pytest",
        "python3.13 -m pip install atheris",
        "pipx run black .",
        "virtualenv .venv",
        "sudo python setup.py install",
        "PYTHONPATH=. python run.py",
        "env python -c pass",
        "ls && python foo.py",
        "$(python -c code)",
    ],
)
def test_bare_interpreter_is_flagged(cmd: str) -> None:
    assert _mod().offending(cmd) is not None


# --- positive controls: legitimate forms pass ----------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "uv run python -m pytest",
        "uv run --extra full python tools/docs_check.py refs",
        "uvx ruff check",
        "uv pip install requests",
        "uv add requests",
        ".venv/bin/python foo.py",
        "/usr/bin/python3 --version",
        "sudo /tmp/venv-py-spy/bin/python examples/bench.py",
        "which python",
        "brew install python",
        "type -a python3",
        "git commit -m 'use python not bare'",
        'echo "run python foo.py"',
        "ipython notebook",  # not the python family
        "poe test",
    ],
)
def test_legitimate_forms_pass(cmd: str) -> None:
    assert _mod().offending(cmd) is None


def test_heredoc_body_is_data_not_command() -> None:
    cmd = "cat <<'EOF'\npython should not trip here\nEOF"
    assert _mod().offending(cmd) is None


def test_deny_payload_shape() -> None:
    # main() reads stdin JSON and prints a PreToolUse deny; assert the offending seam feeds it.
    mod = _mod()
    hit = mod.offending("pip install evil")
    assert hit == "pip"
