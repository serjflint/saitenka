from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import tool_json as tj


def _completed(stdout: str, *, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(["instrument"], returncode, stdout, stderr)


def test_explicit_empty_collection_is_valid(monkeypatch, tmp_path):
    monkeypatch.setattr(tj.subprocess, "run", lambda *_args, **_kwargs: _completed("[]\n"))
    assert tj.run_json(["instrument"], tmp_path, list) == []


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (_completed("", returncode=2, stderr="boom"), "instrument failed"),
        (_completed(""), "empty output"),
        (_completed("not-json"), "malformed JSON"),
        (_completed("{}"), "expected list"),
    ],
)
def test_untrustworthy_instrument_output_fails_closed(monkeypatch, tmp_path, result, message):
    monkeypatch.setattr(tj.subprocess, "run", lambda *_args, **_kwargs: result)
    with pytest.raises(tj.InstrumentError, match=message):
        tj.run_json(["instrument"], tmp_path, list)


def test_missing_instrument_fails_closed(monkeypatch, tmp_path):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(tj.subprocess, "run", missing)
    with pytest.raises(tj.InstrumentError, match="unavailable"):
        tj.run_json(["instrument"], tmp_path, list)


def test_repository_root_uses_the_launch_directory(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    nested = root / "nested"
    nested.mkdir(parents=True)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return _completed(f"{root}\n")

    monkeypatch.setattr(tj.subprocess, "run", run)

    assert tj.repository_root(nested) == root
    assert calls[0][1]["cwd"] == nested


def test_repository_path_anchors_relative_paths(tmp_path):
    root = tmp_path / "repo"
    assert tj.repository_path(root, None, ".ledger.jsonl") == root / ".ledger.jsonl"
    assert tj.repository_path(root, Path("state/ledger.jsonl"), ".ledger.jsonl") == (
        root / "state/ledger.jsonl"
    )
    absolute = tmp_path / "external.jsonl"
    assert tj.repository_path(root, absolute, ".ledger.jsonl") == absolute
