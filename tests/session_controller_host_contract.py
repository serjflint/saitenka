"""Detect application functions that accept the whole live session controller."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _aliases(tree: ast.Module) -> set[str]:
    aliases = {"SessionController"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "SessionController"
            )
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = (node.target,), node.value
            elif isinstance(node, ast.TypeAlias):
                targets, value = (node.name,), node.value
            else:
                continue
            if value is None or not _mentions(value, aliases):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def _mentions(expression: ast.expr, aliases: set[str]) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id in aliases)
        or (isinstance(node, ast.Attribute) and node.attr in aliases)
        or (
            isinstance(node, ast.Constant)
            and any(
                re.search(rf"(?:^|\W){re.escape(alias)}(?:$|\W)", str(node.value)) is not None
                for alias in aliases
            )
        )
        for node in ast.walk(expression)
    )


def session_controller_parameters(root: Path) -> set[str]:
    """Return application functions whose signature reaches the whole session controller."""
    found: set[str] = set()
    for path in sorted((root / "src/saitenka/app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _aliases(tree)
        module = path.relative_to(root / "src").with_suffix("").as_posix().replace("/", ".")
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None),
            )
            if any(
                arg.arg in {"reader", "session_controller"}
                or (arg.annotation is not None and _mentions(arg.annotation, aliases))
                for arg in args
            ):
                found.add(f"{module}:{node.name}")
    return found
