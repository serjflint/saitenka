"""Unit tests for the `poe affected` selector's pure logic (tools/affected.py).

Covers the two properties that make it sound: the closure is reverse-TRANSITIVE (not one-hop, which
would drop tests reaching a module through an intermediary), and blind-spot changes route to a FULL run.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_AFF = Path(__file__).resolve().parent.parent / "tools" / "affected.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_affected", _AFF)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_closure_is_reverse_transitive_not_one_hop() -> None:
    a = _mod()
    graph = {
        "src/overlay/app/fsrs.py": ["src/overlay/app/scoring.py", "tests/test_fsrs.py"],
        "src/overlay/app/scoring.py": ["tests/test_coloring.py"],
    }
    # changing fsrs must reach test_coloring THROUGH scoring — the one-hop set would miss it
    assert a.closure_tests({"src/overlay/app/fsrs.py"}, graph) == {
        "tests/test_fsrs.py",
        "tests/test_coloring.py",
    }


def test_classify_routes_blindspots_to_full_source_to_graph_docs_ignored() -> None:
    a = _mod()
    full, overlay_py, changed_tests = a.classify(
        [
            "overlay/src/overlay/app/_nonexistent_mod.py",  # source seed (missing file → not dynamic)
            "overlay/tests/test_nonexistent.py",  # changed test → itself
            "overlay/tests/conftest.py",  # fixture injection → full
            "overlay/tests/golden/plain.png",  # data golden → full
            "deinflect/src/x.py",  # code dep overlay imports → full
            "README.md",  # docs → ignored (neither full nor a seed)
        ]
    )
    assert any("conftest" in f for f in full)
    assert any("golden" in f for f in full)
    assert any(f.startswith("deinflect/") for f in full)
    assert "src/overlay/app/_nonexistent_mod.py" in overlay_py
    assert "tests/test_nonexistent.py" in changed_tests
    assert all("README" not in f for f in (*full, *overlay_py))


def test_reaches_conftest_escalates() -> None:
    a = _mod()
    assert a.reaches_conftest({"tests/test_x.py", "tests/conftest.py"}) is True
    assert a.reaches_conftest({"tests/test_x.py", "tests/test_y.py"}) is False
