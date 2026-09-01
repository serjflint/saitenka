"""Reject a docstring that names a source file which does not exist.

Every instance found when this was written came from the same event: a module moved, and the prose
describing it did not. Two modules still delegated to a tokenizer file that the package extraction
had renamed, and `token_cache.py` compared its locking to a `Dictionary` cache attribute that #472
deleted. All of them read as current, which is the whole problem — a stale reference is worse than
none, because it is confidently wrong.

(Deliberately phrased without naming the dead files in backticks: this gate reads its own docstring,
and an example would be indistinguishable from the defect. It caught this file on the first run.)

`poe docs-refs` already does this for the docs site. This is the same rule one layer in, for the
prose that ships inside the package.

Scope is deliberately files, not symbols. A file path is decidable from `git ls-files` with no
imports and no side effects; resolving `:class:` targets needs the whole package imported, which is
a heavier and more fragile thing to put in a gate. The symbol sweep is a scrub, not a contract.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: ``foo/bar.py`` or ``bar.py`` inside single or double backticks.
REFERENCE = re.compile(r"`{1,2}~?([\w/.]+\.py)`{1,2}")
#: Working notes are git-ignored by design, so a pointer to one is not a broken reference —
#: AGENTS.md explicitly sanctions pointing at a repo-local `vibe/` plan.
EXEMPT_PREFIXES = ("vibe/",)


def _tracked() -> set[str]:
    """Tracked files **plus** untracked ones git would not ignore.

    `git ls-files` alone skips a file that has been written but not yet added, so a new module's
    docstrings went unchecked until the commit that introduced them — which is exactly when a gate
    is supposed to speak. This one reported clean on its own first run for that reason, and only
    failed once `git add` had made it visible to itself.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout.split()
    return set(listing)


def _docstrings(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = ast.get_docstring(node)
            if doc:
                yield getattr(node, "lineno", 1), doc


def violations() -> list[tuple[str, int, str]]:
    tracked = _tracked()
    # A bare `foo.py` is a legitimate shorthand for a file that exists somewhere in the tree.
    basenames = {Path(path).name for path in tracked}
    found: list[tuple[str, int, str]] = []
    for name in sorted(path for path in tracked if path.endswith(".py")):
        try:
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
        except SyntaxError:
            continue
        for line, doc in _docstrings(tree):
            found.extend(
                (name, line, ref)
                for ref in REFERENCE.findall(doc)
                if ref not in tracked
                and Path(ref).name not in basenames
                and not ref.startswith(EXEMPT_PREFIXES)
            )
    return found


def main() -> int:
    found = violations()
    for name, line, ref in found:
        print(f"{name}:{line}: docstring names a file that does not exist: {ref}")
    if found:
        print(f"docstring-refs: {len(found)} stale reference(s)", file=sys.stderr)
        return 1
    print("docstring-refs: every file named in a docstring exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
