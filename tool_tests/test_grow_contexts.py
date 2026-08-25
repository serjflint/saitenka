"""Tests for the grow coverage-context producer (aggregation core). Run explicitly:
    uv run python -m pytest tool_tests/test_grow_contexts.py

Only the pure `aggregate` is tested — the real coverage run is subprocess glue, like sharpen_triage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import grow_contexts as gc
from tool_json import InstrumentError

BASE = Path("/repo/src/saitenka").resolve()


def _f(rel: str) -> str:
    return str(BASE / rel)


def test_aggregate_counts_uncovered_plus_weakly_covered():
    measured = [_f("app/x.py")]
    missing = {_f("app/x.py"): [10, 11]}  # 2 uncovered lines
    contexts = {
        _f("app/x.py"): {
            1: ["tests.a", "tests.b"],  # well-specified (2 contexts) — not weak
            2: ["tests.a"],  # weak (1 context)
            3: [""],  # incidental-only (0 real contexts) — weak
        }
    }
    out = gc.aggregate(measured, BASE, lambda f: missing[f], lambda f: contexts[f])
    assert out["app/x.py"]["under_spec"] == 2 + 2  # 2 uncovered + (line2 + line3) weak
    assert out["app/x.py"]["uncovered_lines"] == [10, 11]
    assert out["app/x.py"]["weak_lines"] == [
        {"line": 2, "test_nodeids": ["tests.a"]},
        {"line": 3, "test_nodeids": []},
    ]
    assert out["app/x.py"]["test_nodeids"] == ["tests.a", "tests.b"]


def test_aggregate_ignores_files_outside_the_source_base():
    outside = "/usr/lib/python/site-packages/dep.py"
    out = gc.aggregate([outside], BASE, lambda _: [1], lambda _: {})
    assert out == {}


def test_aggregate_a_fully_specified_module_scores_zero():
    measured = [_f("app/clean.py")]
    contexts = {
        _f("app/clean.py"): {1: ["t.a", "t.b"], 2: ["t.a", "t.c", "t.d"]}
    }  # all ≥2 contexts
    out = gc.aggregate(measured, BASE, lambda _: [], lambda f: contexts[f])
    assert out["app/clean.py"]["under_spec"] == 0
    assert out["app/clean.py"]["weak_lines"] == []


def test_inspect_summarizes_one_module_without_rerunning_coverage(monkeypatch, tmp_path, capsys):
    payload = {
        "version": 3,
        "modules": {
            "app/x.py": {
                "under_spec": 1,
                "uncovered_lines": [7],
                "weak_lines": [],
                "test_nodeids": [],
            }
        },
    }
    (tmp_path / "contexts.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["grow_contexts.py", "--inspect", "contexts.json", "--module", "app/x.py"]
    )

    gc.main()

    assert json.loads(capsys.readouterr().out) == {
        "under_spec": 1,
        "uncovered_lines": 1,
        "weak_lines": 0,
        "test_nodeids": 0,
        "line_evidence": "available",
    }


def test_validate_v3_rejects_malformed_line_evidence():
    import pytest

    with pytest.raises(InstrumentError, match="invalid v3"):
        gc.validate_row(
            {
                "under_spec": 1,
                "uncovered_lines": "7",
                "weak_lines": [],
                "test_nodeids": [],
            },
            "app/x.py",
            3,
        )
