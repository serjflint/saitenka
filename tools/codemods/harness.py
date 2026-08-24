"""The two halves every repo-wide rewrite here needs: a worklist, and an apply loop.

    uv run --group codemod python tools/codemods/<transform>.py [--check]

The worklist comes from `cluster_map`, not from a text sweep: `--member NAME` resolves the name to
an *attribute* match, so a same-named parameter and a mention in a comment are not sites — both of
those showed up in the greps this replaces, and a codemod that trusts them edits them.

LibCST rather than a regex or `ast.unparse` because formatting, comments and the goldens survive
the round trip untouched; a rewrite whose diff is the whole file is not reviewable.

`--check` is not a dry run for comfort. It is what makes the transform runnable twice: the second
run must report zero, and a transform that cannot say that has no way to prove it finished.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    import libcst as cst

ROOT = Path(__file__).resolve().parents[2]

#: The declaration being retired lives here; rewriting the class body turns a descriptor into a
#: nonsense assignment. A transform that renames host members excludes it.
CONTROLLER = ROOT / "src" / "saitenka" / "app" / "session_controller.py"


def worklist(members: Iterable[str]) -> list[Path]:
    """Every file holding an attribute site of any of these host members."""
    sys.path.insert(0, str(ROOT / "tools"))
    import cluster_map

    found: set[Path] = set()
    for member in members:
        found.update(ROOT / path for path in cluster_map.sites(member))
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
