"""Fail when mining target, index, transaction, or store ownership escapes its boundary."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"

_OWNER = "mining_controller.py"
_COMPOSITION = "session_controller.py"
_CONSTRUCTORS = {
    "MinedCardStore": {_OWNER},
    "MinedSet": {_OWNER},
    "MiningIndexState": {_OWNER},
    "MiningSpec": {_OWNER, "reader_deps.py"},
    "MiningTarget": {_OWNER, "reader_deps.py"},
    "MiningTransaction": {_OWNER},
}
_OWNER_MUTATORS = {
    "clear_mining_target": {_OWNER, _COMPOSITION},
    "publish_mining_target": {_OWNER, _COMPOSITION},
    "record_mined_expression": {_OWNER},
    "select_mining_spec": {_OWNER, _COMPOSITION},
}
_OWNED_ATTRIBUTES = {
    "_anki_probe",
    "_mined_index",
    "_mined_seed",
    "_mined_store",
    "_mining_spec",
    "_mining_target",
    "_scratch_dir",
}
_LEGACY_HOST_FIELDS = {"anki", "mine_cfg", "mined_seed", "mined_store"}
_RETIRED_SESSION_FACADE = {
    "_add_duplicate",
    "_mine_token",
    "bulk_mine",
    "mine_current",
    "mine_current_video",
}


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line: int
    rule: str
    detail: str


def _site(path: Path) -> str:
    try:
        return path.resolve().relative_to(APP.resolve()).as_posix()
    except ValueError:
        return path.name


def _call_name(node: ast.Call) -> str | None:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _attributes(node: ast.AST) -> list[ast.Attribute]:
    if isinstance(node, ast.Attribute):
        return [node, *_attributes(node.value)]
    if isinstance(node, ast.Starred):
        return _attributes(node.value)
    if isinstance(node, ast.List | ast.Tuple):
        return [attribute for item in node.elts for attribute in _attributes(item)]
    return []


def _receiver_contains(node: ast.AST, attribute: str) -> bool:
    return any(
        isinstance(part, ast.Attribute) and part.attr == attribute for part in ast.walk(node)
    )


def _is_self_attribute(attribute: ast.Attribute) -> bool:
    return isinstance(attribute.value, ast.Name) and attribute.value.id == "self"


def inspect_source(source: str, path: Path) -> list[Finding]:
    tree = ast.parse(source, filename=str(path))
    site = _site(path)
    findings: list[Finding] = []

    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        for target in targets:
            for attribute in _attributes(target):
                if site != _OWNER and attribute.attr in _OWNED_ATTRIBUTES:
                    findings.append(
                        Finding(path, attribute.lineno, "owned-state-write", attribute.attr)
                    )
                if (
                    site == _COMPOSITION
                    and _is_self_attribute(attribute)
                    and attribute.attr in _LEGACY_HOST_FIELDS
                ):
                    findings.append(
                        Finding(path, attribute.lineno, "legacy-owner-field", attribute.attr)
                    )

        if isinstance(node, ast.Call):
            name = _call_name(node)
            allowed = _CONSTRUCTORS.get(name or "")
            if allowed is not None and site not in allowed:
                findings.append(Finding(path, node.lineno, "owned-constructor", name or ""))
            mutator_sites = _OWNER_MUTATORS.get(name or "")
            if mutator_sites is not None and site not in mutator_sites:
                findings.append(Finding(path, node.lineno, "owner-mutator", name or ""))
            function = node.func
            if isinstance(function, ast.Attribute) and site != _OWNER:
                if _receiver_contains(function.value, "_mined_index"):
                    findings.append(
                        Finding(path, node.lineno, "owned-index-mutation", function.attr)
                    )
                if _receiver_contains(function.value, "_mined_seed"):
                    findings.append(
                        Finding(path, node.lineno, "owned-seed-mutation", function.attr)
                    )

        if (
            site == _COMPOSITION
            and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in _RETIRED_SESSION_FACADE
        ):
            findings.append(Finding(path, node.lineno, "retired-facade", node.name))

    return sorted(findings, key=lambda finding: (str(finding.path), finding.line, finding.rule))


def inspect_tree(root: Path = APP) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        findings.extend(inspect_source(path.read_text(encoding="utf-8"), path))
    return findings


def main() -> int:
    findings = inspect_tree()
    for finding in findings:
        relative = finding.path.relative_to(ROOT)
        print(f"{relative}:{finding.line}: {finding.rule}: {finding.detail}")
    if findings:
        print(f"mining-ownership: {len(findings)} violation(s)", file=sys.stderr)
        return 1
    print("mining-ownership: one writer for target, index, transaction, and store")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
