"""Reject annotation state, work policy, or construction escaping its feature owner."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"
OWNER = "features/annotation/annotation_controller.py"
ASSEMBLY = "session/assembly.py"
COMPOSITION = "session/controller.py"
RETIRED_METHODS = {
    "_annotation_disposition",
    "_annotation_identity",
    "_annotation_key",
    "_feed_episode_annotation",
    "_finish_annotation",
    "_schedule_current_annotation",
    "_start_episode_annotation",
    "_tokenize_cue",
}
RETIRED_FIELDS = {
    "_annotation",
    "_annotation_async",
    "_annotation_degraded",
    "_annotation_episode_cursor",
    "_annotation_episode_index",
    "_annotation_executor",
    "_annotation_submit",
    "_cue_retired",
    "_current_cue_identity",
    "_dependencies_settled",
    "_dependency_generation",
    "_sub_pending",
    "_warmed_index",
    "annotation",
    "token_cache",
    "warm_ports",
}


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line: int
    rule: str
    detail: str


def _site(path: Path) -> str:
    return path.resolve().relative_to(APP.resolve()).as_posix()


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _annotation_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _owner_aliases(tree: ast.AST, constructor_aliases: set[str]) -> set[str]:
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
                if _annotation_name(node.annotation) in constructor_aliases:
                    name = _annotation_name(target)
                    if name is not None and name not in aliases:
                        aliases.add(name)
                        changed = True
            if isinstance(target, ast.Name) and value is not None:
                owner_value = (
                    isinstance(value, ast.Attribute) and value.attr == "annotation_controller"
                ) or (isinstance(value, ast.Name) and value.id in aliases)
                owner_call = (
                    isinstance(value, ast.Call) and _call_name(value) in constructor_aliases
                )
                if (owner_value or owner_call) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
        for node in ast.walk(tree):
            if not isinstance(node, ast.arg):
                continue
            if _annotation_name(node.annotation) in constructor_aliases and node.arg not in aliases:
                aliases.add(node.arg)
                changed = True
    return aliases


def inspect_source(source: str, path: Path) -> list[Finding]:
    tree = ast.parse(source, filename=str(path))
    site = _site(path)
    findings: list[Finding] = []
    constructors = {"CueAnnotationController": "owner", "TokenCache": "cache"}
    aliases = dict(constructors)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in constructors:
                    aliases[alias.asname or alias.name] = constructors[alias.name]
    owner_names = _owner_aliases(
        tree,
        {name for name, kind in aliases.items() if kind == "owner"},
    )
    for node in ast.walk(tree):
        if (
            site == COMPOSITION
            and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in RETIRED_METHODS
        ):
            findings.append(Finding(path, node.lineno, "retired-facade", node.name))
        if (
            site == COMPOSITION
            and isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in RETIRED_FIELDS
        ):
            findings.append(Finding(path, node.lineno, "retired-session-field", node.attr))
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
        ):
            direct_owner = (
                isinstance(node.value, ast.Attribute) and node.value.attr == "annotation_controller"
            )
            alias_owner = isinstance(node.value, ast.Name) and node.value.id in owner_names
            if site != OWNER and (direct_owner or alias_owner):
                findings.append(Finding(path, node.lineno, "private-owner-access", node.attr))
        if isinstance(node, ast.Call):
            name = _call_name(node)
            kind = aliases.get(name) if name is not None else None
            if kind == "owner" and site != ASSEMBLY:
                findings.append(Finding(path, node.lineno, "owned-constructor", name or ""))
            if kind == "cache" and site != OWNER:
                findings.append(Finding(path, node.lineno, "private-cache-constructor", name or ""))
    return findings


def inspect_tree() -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(APP.rglob("*.py")):
        findings.extend(inspect_source(path.read_text(encoding="utf-8"), path))
    return sorted(findings, key=lambda finding: (str(finding.path), finding.line, finding.rule))


def main() -> int:
    findings = inspect_tree()
    for finding in findings:
        path = finding.path.relative_to(ROOT)
        print(f"{path}:{finding.line}: {finding.rule}: {finding.detail}")
    if findings:
        print(f"annotation-ownership: {len(findings)} violation(s)", file=sys.stderr)
        return 1
    print("annotation-ownership: session state/facades retired; owner constructed in assembly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
