from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import verify_contract_evidence as evidence

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _witness() -> dict[str, str]:
    return {
        "id": "fault",
        "scenario_id": "scenario",
        "path": "subject.py",
        "find": "answer = 1",
        "replace": "answer = 2",
        "command": "check scenario",
        "failure_contains": "contract-mismatch scenario_id=scenario",
    }


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["check"], returncode, stdout="", stderr=stderr)


def _subject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "subject.py"
    path.write_text("answer = 1\n", encoding="utf-8")
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(evidence, "_require_clean", lambda _path: None)
    return path


def test_a_passing_baseline_and_expected_failure_verify_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _subject(tmp_path, monkeypatch)
    runs = iter(
        [
            _completed(0),
            _completed(1, "contract-mismatch scenario_id=scenario"),
        ]
    )
    monkeypatch.setattr(evidence, "_run_command", lambda _command: next(runs))

    assert evidence._run_witness(_witness()) is None
    assert path.read_text(encoding="utf-8") == "answer = 1\n"


def test_an_unrelated_crash_is_not_a_fault_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _subject(tmp_path, monkeypatch)
    runs = iter([_completed(0), _completed(1, "SyntaxError: invalid syntax")])
    monkeypatch.setattr(evidence, "_run_command", lambda _command: next(runs))

    assert evidence._run_witness(_witness()) == (
        "command failed without the expected failure discriminator"
    )
    assert path.read_text(encoding="utf-8") == "answer = 1\n"


def test_a_failing_baseline_is_not_a_fault_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _subject(tmp_path, monkeypatch)
    monkeypatch.setattr(evidence, "_run_command", lambda _command: _completed(1))

    assert evidence._run_witness(_witness()) == "baseline command failed"
    assert path.read_text(encoding="utf-8") == "answer = 1\n"


def test_a_mutated_timeout_is_rejected_and_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _subject(tmp_path, monkeypatch)
    runs: list[object] = [_completed(0), subprocess.TimeoutExpired("check", 30)]

    def run(_command: str) -> subprocess.CompletedProcess[str]:
        result = runs.pop(0)
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, subprocess.CompletedProcess)
        return result

    monkeypatch.setattr(evidence, "_run_command", run)

    assert evidence._run_witness(_witness()) == "mutated command timed out"
    assert path.read_text(encoding="utf-8") == "answer = 1\n"
