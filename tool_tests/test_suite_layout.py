"""Structural contract for test ownership and reusable migration tooling."""

from __future__ import annotations

import ast
from pathlib import Path

import app_package_layout

ROOT = Path(__file__).resolve().parents[1]


def test_feature_suites_match_the_declared_feature_packages() -> None:
    suites = {path.name for path in (ROOT / "tests" / "features").iterdir() if path.is_dir()}

    assert suites == app_package_layout.FEATURE_PACKAGES


def test_executable_tools_contain_no_pytest_modules() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "tools").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name.startswith(("test_", "Test"))
            for node in ast.walk(tree)
        ):
            offenders.append(path.name)

    assert offenders == []


def test_only_reusable_codemod_infrastructure_is_committed() -> None:
    codemods = {path.name for path in (ROOT / "tools" / "codemods").glob("*.py")}

    assert codemods == {"harness.py", "move_member.py"}
