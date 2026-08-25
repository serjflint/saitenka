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
        "src/saitenka/app/fsrs.py": ["src/saitenka/app/scoring.py", "tests/test_fsrs.py"],
        "src/saitenka/app/scoring.py": ["tests/test_coloring.py"],
    }
    # changing fsrs must reach test_coloring THROUGH scoring — the one-hop set would miss it
    assert a.closure_tests({"src/saitenka/app/fsrs.py"}, graph) == {
        "tests/test_fsrs.py",
        "tests/test_coloring.py",
    }


def test_classify_routes_blindspots_to_full_source_to_graph_docs_ignored() -> None:
    a = _mod()
    full, overlay_py, changed_tests = a.classify(
        [
            "src/saitenka/app/_nonexistent_mod.py",  # source seed (missing file → not dynamic)
            "tests/test_nonexistent.py",  # changed test → itself
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
    assert "tests/test_nonexistent.py" in changed_tests
    assert all("README" not in f for f in (*full, *overlay_py))


def test_tools_edit_routes_to_full() -> None:
    # tests file-LOAD tools via importlib.util (no static import edge ruff can see); without this the
    # selector returned 0 tests and silently skipped the tool's own test. Must route to a full run.
    a = _mod()
    full, overlay_py, _ = a.classify(["tools/mutate/run.py"])
    assert "tools/mutate/run.py" in full
    assert not overlay_py


def test_tool_test_edit_selects_the_changed_test() -> None:
    a = _mod()
    full, overlay_py, changed_tests = a.classify(["tool_tests/test_example.py"])

    assert full == []
    assert overlay_py == {"tool_tests/test_example.py"}
    assert changed_tests == {"tool_tests/test_example.py"}


def test_closure_excludes_conftest() -> None:
    # conftest sits in nearly every module's closure (it imports the god-objects); including it would
    # collapse every selection to a full run and it isn't a runnable test anyway.
    a = _mod()
    graph = {"src/saitenka/app/x.py": ["tests/test_x.py", "tests/conftest.py"]}
    assert a.closure_tests({"src/saitenka/app/x.py"}, graph) == {"tests/test_x.py"}


def test_closure_includes_nested_application_and_tool_tests() -> None:
    a = _mod()
    graph = {
        "tools/x.py": [
            "tests/features/mining/test_x.py",
            "tool_tests/test_x.py",
        ]
    }

    assert a.closure_tests({"tools/x.py"}, graph) == {
        "tests/features/mining/test_x.py",
        "tool_tests/test_x.py",
    }
