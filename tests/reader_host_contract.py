"""Ratcheted inventory of feature functions that accept the Reader host object."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _reader_aliases(tree: ast.Module) -> set[str]:
    aliases = {"Reader"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "Reader"
            )
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
                value = node.value
            elif isinstance(node, ast.TypeAlias):
                targets = (node.name,)
                value = node.value
            else:
                continue
            if value is None or not _expression_mentions_alias(value, aliases):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def _expression_mentions_alias(expression: ast.expr, aliases: set[str]) -> bool:
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


def _mentions_reader(annotation: ast.expr | None, aliases: set[str]) -> bool:
    if annotation is None:
        return False
    return _expression_mentions_alias(annotation, aliases)


def reader_parameter_counts(root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in sorted((root / "src/saitenka/app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _reader_aliases(tree)
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
                arg.arg == "reader" or _mentions_reader(arg.annotation, aliases) for arg in args
            ):
                counts[module] += 1
    return counts


def load_allowlist(path: Path) -> dict[str, int]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(
        not isinstance(module, str)
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 0
        for module, limit in value.items()
    ):
        raise ValueError("Reader host allow-list must map module names to non-negative limits")
    return value


def unexpected_reader_parameters(root: Path, allowlist: Path) -> set[str]:
    """Every module whose count differs from its baseline, in either direction.

    The raw comparison — used by the unit tests below against synthetic trees. The gate itself goes
    through :func:`enforce_reader_host_contract`, which treats the two directions differently.
    """
    limits = load_allowlist(allowlist)
    counts = reader_parameter_counts(root)
    modules = counts.keys() | limits.keys()
    return {
        f"{module}: current={counts.get(module, 0)} baseline={limits.get(module, 0)}"
        for module in modules
        if counts.get(module, 0) != limits.get(module, 0)
    }


def enforce_reader_host_contract(root: Path, allowlist: Path) -> set[str]:
    """Fail on GROWTH; tighten the baseline on a decrease. Returns only the modules that grew.

    The contract this file exists for is "no new `Reader` coupling", and only growth violates it. A
    decrease is the migration working, and failing on it cost a re-bless per conversion — the same
    ceremony the runtime-debt manifest had.

    Tightening rather than merely tolerating is what keeps the "no slack" property the strict
    comparison was protecting: a baseline left above the real count is headroom a future regression
    could slip back into unnoticed. Rewritten here, so there is never any.
    """
    limits = load_allowlist(allowlist)
    counts = reader_parameter_counts(root)
    grew = {
        f"{module}: current={counts.get(module, 0)} baseline={limits.get(module, 0)}"
        for module in counts.keys() | limits.keys()
        if counts.get(module, 0) > limits.get(module, 0)
    }
    if grew:
        return grew
    _write_allowlist(allowlist, counts, limits)
    return set()


def _write_allowlist(allowlist: Path, counts, limits: dict[str, int]) -> dict[str, int]:
    current = {module: count for module, count in sorted(counts.items()) if count}
    if current != limits:
        allowlist.write_text(
            json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return current


def bless(root: Path, allowlist: Path) -> dict[str, int]:
    """Accept the current counts, growth included — for a deliberate design decision.

    The gate refuses growth because the usual cause is a new feature function reaching for the
    host. The other cause is a trade: a total `Protocol` replacing `getattr` probes buys away a
    class of silent no-op and pays in mechanical rows. Hand-editing this fixture is how that used
    to be said, which is not saying it at all — this is, and the commit message carries the reason.
    """
    return _write_allowlist(allowlist, reader_parameter_counts(root), load_allowlist(allowlist))


if __name__ == "__main__":  # pragma: no cover - operator entry point
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent
    _allowlist = _root / "tests/fixtures/reader_host_allowlist.json"
    if sys.argv[1:2] != ["bless"]:
        print("usage: python tests/reader_host_contract.py bless")  # noqa: T201
        raise SystemExit(2)
    print(f"reader-host: blessed ({sum(bless(_root, _allowlist).values())} rows)")  # noqa: T201
