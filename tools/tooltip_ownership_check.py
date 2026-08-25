"""Fail when tooltip policy or mutable owner state escapes ``TooltipController``."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"

_OWNER = "tooltip_controller.py"
_COMPOSITION = "session_controller.py"
_CONSTRUCTORS = {
    "HoveredWordStore",
    "HoverPauseStore",
    "HoverStore",
    "PulseStore",
    "TipNavStore",
    "TooltipState",
}
_OWNED_ATTRIBUTES = {
    "_delays",
    "_flash_seconds",
    "_hover_store",
    "_nav_store",
    "_pause_enabled",
    "_pause_store",
    "_pulse_store",
    "_selected",
    "_word_store",
}
_LEGACY_SESSION_ATTRIBUTES = {
    "_hover_store",
    "_nav_store",
    "_pause_store",
    "_pulse_store",
    "flash_secs",
    "hide_delay",
    "hover",
    "hover_switch_delay",
    "pause_on_tooltip",
    "scan_delay",
    "word_store",
}
_RETIRED_TOOLTIP_STATE = {"key", "rect", "state", "tip_inflected", "tip_tok"}


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


def _attributes(node: ast.AST) -> list[ast.Attribute]:
    if isinstance(node, ast.Attribute):
        return [node, *_attributes(node.value)]
    if isinstance(node, ast.Starred):
        return _attributes(node.value)
    if isinstance(node, ast.List | ast.Tuple):
        return [attribute for item in node.elts for attribute in _attributes(item)]
    return []


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_self_attribute(attribute: ast.Attribute) -> bool:
    return isinstance(attribute.value, ast.Name) and attribute.value.id == "self"


def _contains_attribute(node: ast.AST, name: str) -> bool:
    return any(isinstance(part, ast.Attribute) and part.attr == name for part in ast.walk(node))


_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _is_tooltip_owner_reference(node: ast.AST, names: set[str]) -> bool:
    return any(
        (isinstance(part, ast.Name) and part.id in names)
        or (isinstance(part, ast.Attribute) and part.attr == "tooltip_controller")
        for part in ast.walk(node)
    )


def _annotation_is_tooltip_controller(annotation: ast.AST | None) -> bool:
    if isinstance(annotation, ast.Name | ast.Attribute):
        return (
            getattr(annotation, "id", None) == "TooltipController"
            or getattr(annotation, "attr", None) == "TooltipController"
        )
    return isinstance(annotation, ast.Constant) and annotation.value == "TooltipController"


def _bound_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.List | ast.Tuple):
        return {name for item in target.elts for name in _bound_names(item)}
    return set()


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        nodes.append(node)
        if not isinstance(node, _SCOPES):
            pending.extend(ast.iter_child_nodes(node))
    return nodes


def _owner_names(scope: ast.AST, inherited: set[str]) -> set[str]:
    names = {*inherited, "tooltip_controller"}
    if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        arguments = [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]
        if scope.args.vararg is not None:
            arguments.append(scope.args.vararg)
        if scope.args.kwarg is not None:
            arguments.append(scope.args.kwarg)
        names.difference_update(argument.arg for argument in arguments)
        names.update(
            argument.arg
            for argument in arguments
            if _annotation_is_tooltip_controller(argument.annotation)
        )

    nodes = _scope_nodes(scope)
    for node in nodes:
        if isinstance(node, ast.AnnAssign) and _annotation_is_tooltip_controller(node.annotation):
            names.update(_bound_names(node.target))
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not _is_tooltip_owner_reference(value, names):
                continue
            aliases = {name for target in targets for name in _bound_names(target)}
            if not aliases <= names:
                names.update(aliases)
                changed = True
    return names


def _scoped_nodes(scope: ast.AST, inherited: set[str] | None = None):
    names = _owner_names(scope, inherited or set())
    yield scope, names
    for node in _scope_nodes(scope):
        if isinstance(node, _SCOPES):
            yield from _scoped_nodes(node, names)
        else:
            yield node, names


def inspect_source(source: str, path: Path) -> list[Finding]:
    tree = ast.parse(source, filename=str(path))
    site = _site(path)
    findings: list[Finding] = []

    for node, owner_names in _scoped_nodes(tree):
        if isinstance(node, ast.Attribute) and (
            site == _COMPOSITION
            and _is_self_attribute(node)
            and node.attr in _LEGACY_SESSION_ATTRIBUTES
        ):
            findings.append(Finding(path, node.lineno, "legacy-session-field", node.attr))

        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        for target in targets:
            for attribute in _attributes(target):
                if (
                    site != _OWNER
                    and attribute.attr in _OWNED_ATTRIBUTES
                    and _is_tooltip_owner_reference(attribute.value, owner_names)
                ):
                    findings.append(
                        Finding(path, attribute.lineno, "owned-state-write", attribute.attr)
                    )
                if site not in {_OWNER, "popups.py"} and attribute.attr == "tip_keys_bound":
                    findings.append(
                        Finding(path, attribute.lineno, "keybinding-state-write", attribute.attr)
                    )
                if (
                    site == _COMPOSITION
                    and attribute.attr in _RETIRED_TOOLTIP_STATE
                    and _contains_attribute(attribute.value, "tip")
                ):
                    findings.append(
                        Finding(path, attribute.lineno, "tooltip-state-write", attribute.attr)
                    )

        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _CONSTRUCTORS and site != _OWNER:
                findings.append(Finding(path, node.lineno, "owned-constructor", name or ""))
            if (
                site == _COMPOSITION
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault"
                and _contains_attribute(node.func.value, "panel_cache")
            ):
                findings.append(Finding(path, node.lineno, "tooltip-cache-write", node.func.attr))

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
        print(f"tooltip-ownership: {len(findings)} violation(s)", file=sys.stderr)
        return 1
    print("tooltip-ownership: one owner for tooltip policy, stores, and mutable lifecycle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
