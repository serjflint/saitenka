"""The print() allowlist is written twice — ruff's and ast-grep's copies must name the same files.

ruff bans `print` per-file (`T201`) and ast-grep's `no-print-in-lib` bans it per-rule. Both encode one
fact: which modules are CLI surface. They drifted once already — extracting `cli.py`'s subcommands into
`app/commands/` updated ruff and left ast-grep flagging 114 legitimate prints, which is how a warning
tier becomes background noise. Comparing the EXPANDED file sets (not the glob strings) is what makes the
two spellings — `*wizard*.py` vs. two explicit paths — compare equal.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "sgconfig" / "rules" / "no-print-in-lib.yml"
LIBRARY = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "src" / "saitenka").rglob("*.py"))


def _expand(patterns: list[str]) -> set[str]:
    return {f for f in LIBRARY if any(Path(f).full_match(p) for p in patterns)}


def _ruff_print_surface() -> set[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
    return _expand([glob for glob, rules in ignores.items() if "print" in rules])


def _ast_grep_print_surface() -> set[str]:
    return _expand(yaml.safe_load(RULE.read_text(encoding="utf-8"))["ignores"])


def test_print_allowlists_name_the_same_modules() -> None:
    assert _ast_grep_print_surface() == _ruff_print_surface()


def test_print_allowlist_is_not_empty() -> None:
    """Negative control: a glob that matched nothing would make the assertion above vacuously true."""
    assert len(_ruff_print_surface()) > 5
