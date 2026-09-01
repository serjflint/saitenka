"""Reject an application import from the library layers — including under ``TYPE_CHECKING``.

`.importlinter` sets ``exclude_type_checking_imports = True`` globally, and its ``layers`` contract
says so in as many words. That is the right default for a *runtime* dependency graph, but it means a
typing-only edge from a lower layer to `saitenka.app` is invisible to every existing contract.

One had been sitting in `render/analysis.py` for exactly that reason: a renderer annotated against
`app.features.analysis.EpisodeAnalysis`. Nothing imported it at runtime, so no gate objected — but
the module could not be read, tested, or extracted without the application, which is what coupling
means. A `forbidden` contract cannot replace this check, because it would be blind in the same way.

The rule is deliberately the broad one: **nothing outside `saitenka.app` names `saitenka.app`.**
Narrowing it to a hand-listed cluster is how a gate ends up matching a name instead of a meaning.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = "src/saitenka/app/"


def _app_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Every name in this module that resolves to the application package.

    `ast.walk` ignores the enclosing statement, so an import nested in `if TYPE_CHECKING:` is found
    exactly like a module-level one — the whole point of this check.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "saitenka":
            found.extend(
                (node.lineno, f"from saitenka import {alias.name}")
                for alias in node.names
                if alias.name == "app"
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "saitenka.app" or node.module.startswith("saitenka.app."):
                found.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Import):
            found.extend(
                (node.lineno, f"import {alias.name}")
                for alias in node.names
                if alias.name == "saitenka.app" or alias.name.startswith("saitenka.app.")
            )
    return found


def tracked_library_sources() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "src/saitenka"], capture_output=True, text=True, cwd=ROOT, check=True
    ).stdout.split()
    return [f for f in listing if f.endswith(".py") and not f.startswith(APP)]


def violations() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for name in tracked_library_sources():
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
        found.extend((name, line, detail) for line, detail in _app_imports(tree))
    return sorted(found)


def main() -> int:
    found = violations()
    for name, line, detail in found:
        print(f"{name}:{line}: library layer imports the application: {detail}")
    if found:
        print(f"layer-independence: {len(found)} violation(s)", file=sys.stderr)
        return 1
    print(f"layer-independence: {len(tracked_library_sources())} library modules, none import app/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
