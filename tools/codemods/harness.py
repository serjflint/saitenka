"""The two halves every repo-wide rewrite here needs: a worklist, and an apply loop.

    uv run --group codemod python tools/codemods/<transform>.py [--check]

The worklist is built from Python attribute nodes, not text: a same-named parameter and a mention
in a comment are not sites, so a codemod cannot accidentally edit either.

LibCST rather than a regex or `ast.unparse` because formatting, comments and the goldens survive
the round trip untouched; a rewrite whose diff is the whole file is not reviewable.

`--check` is not a dry run for comfort. It is what makes the transform runnable twice: the second
run must report zero, and a transform that cannot say that has no way to prove it finished.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    import libcst as cst

ROOT = Path(__file__).resolve().parents[2]

#: The declaration being retired lives here; rewriting the class body turns a descriptor into a
#: nonsense assignment. A transform that renames host members excludes it.
CONTROLLER = ROOT / "src" / "saitenka" / "app" / "session" / "controller.py"
_SWEPT = ("src", "tests", "tools", "examples", "install", ".agents")


def worklist(members: Iterable[str]) -> list[Path]:
    """Every Python file holding an attribute site with one of these names."""
    wanted = set(members)
    found: set[Path] = set()
    for base in _SWEPT:
        for path in sorted((ROOT / base).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            if any(
                isinstance(node, ast.Attribute) and node.attr in wanted for node in ast.walk(tree)
            ):
                found.add(path)
    return sorted(found)


def apply(
    label: str,
    paths: Iterable[Path],
    make: type[cst.CSTTransformer],
    *,
    check: bool = False,
) -> int:
    """Run the transform over the worklist; return the number of sites rewritten.

    The transformer must expose a `count` of the sites it changed: a file is only rewritten when it
    reports one, so an unchanged file keeps its mtime and stays out of the diff.
    """
    import libcst as cst  # the codemod group is opt-in, not a tool-wide dependency

    total, touched = 0, 0
    for path in paths:
        source = path.read_text(encoding="utf-8")
        transformer = make()
        tree = cst.parse_module(source).visit(transformer)
        changed: int = getattr(transformer, "count", 0)
        if not changed:
            continue
        total += changed
        touched += 1
        if not check:
            path.write_text(tree.code, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"{label}: {verb} {total} site(s) in {touched} file(s)")  # this is a CLI
    return total
