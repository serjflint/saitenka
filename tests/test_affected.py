"""Unit tests for the `poe affected` selector's pure logic (tools/affected.py).

Covers the two properties that make it sound: the closure is reverse-TRANSITIVE (not one-hop, which
would drop tests reaching a module through an intermediary), and blind-spot changes route to a FULL run.
"""

from __future__ import annotations

import importlib.util
import shlex
import tomllib
from pathlib import Path

import pytest

_AFF = Path(__file__).resolve().parent.parent / "tools" / "affected.py"
_PYPROJECT = _AFF.parent.parent / "pyproject.toml"


def _mod():
    spec = importlib.util.spec_from_file_location("_affected", _AFF)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _poe_marker_expression(task: str) -> str:
    tasks = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["tool"]["poe"]["tasks"]
    command = tasks[task]
    argv = shlex.split(command["shell"] if isinstance(command, dict) else command)
    return argv[argv.index("-m") + 1]


@pytest.mark.parametrize("task", ["test", "test-ft", "cov"])
def test_ordinary_test_tasks_exclude_live(task: str) -> None:
    assert "live" in _poe_marker_expression(task).replace("(", " ").replace(")", " ").split()


def test_affected_tier_matches_default_test_universe() -> None:
    assert _poe_marker_expression("test") == _mod().TIER


def test_closure_is_reverse_transitive_not_one_hop() -> None:
    a = _mod()
    graph = {
        "src/saitenka/app/dict_meta.py": [
            "src/saitenka/app/scoring.py",
            "tests/test_fsrs.py",
            "tests/conftest.py",
            "tool_tests/test_meta.py",
        ],
        "src/saitenka/app/scoring.py": ["tests/test_coloring.py"],
    }
    # changing fsrs must reach test_coloring THROUGH scoring — the one-hop set would miss it
    assert a.closure_tests({"src/saitenka/app/dict_meta.py"}, graph) == {
        "tests/test_fsrs.py",
        "tests/test_coloring.py",
    }


def test_classify_routes_blindspots_to_full_source_to_graph_docs_ignored() -> None:
    a = _mod()
    full, overlay_py, changed_tests = a.classify(
        [
            "src/saitenka/app/_nonexistent_mod.py",  # source seed (missing file → not dynamic)
            "tests/test_affected.py",  # changed test → itself
            "tests/test_nonexistent.py",  # deleted test → nothing left to run
            "tests/conftest.py",  # fixture injection → full
            "tests/golden/plain.png",  # data golden → full
            "deinflect/src/x.py",  # code dep overlay imports → full
            "README.md",  # docs → ignored (neither full nor a seed)
        ]
    )
    assert any("conftest" in f for f in full)
    assert any("golden" in f for f in full)
    assert any(f.startswith("deinflect/") for f in full)
    assert "src/saitenka/app/_nonexistent_mod.py" in overlay_py
    assert changed_tests == {"tests/test_affected.py"}
    assert all("README" not in f for f in (*full, *overlay_py))


def test_tooling_is_outside_application_selection() -> None:
    a = _mod()
    full, overlay_py, changed_tests = a.classify(
        ["tools/mutate/run.py", "tool_tests/test_mutate.py", "vibe/session_codemod.py"]
    )
    assert (full, overlay_py, changed_tests) == ([], set(), set())
