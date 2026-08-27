"""Fail when tooltip policy, preparation, or mutable owner state escapes its owner."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"

_OWNER = "features/tooltip/tooltip_controller.py"
_PREPARATION_OWNER = "features/tooltip/preparation.py"
_COMPOSITION = "session/controller.py"
_ASSEMBLY = "session/assembly.py"
_PREWARM = "prewarm.py"
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
_RETIRED_PREPARATION_ATTRIBUTES = {
    "_mask_atlas_startup",
    "_head_prefetch_lookahead",
    "_head_prefetch_queue_max",
    "_mask_atlas_startup_state",
    "_mask_atlas_submit",
    "_mem_cache",
    "_prefetch_gen",
    "_prefetch_lookahead",
    "_prefetch_workers",
    "_render_cache",
    "_render_cache_min_height",
    "_render_cache_sig",
    "head_prefetch_lookahead",
    "prefetch",
    "prefetch_lookahead",
    "prefetch_state",
    "prefetch_workers",
    "render_cache",
}
_RETIRED_PREPARATION_CLOSE_ACCESS = {
    "close_mask_activation",
    "close_prefetch",
    "uninstall_mask_atlas",
}
_PREPARATION_CONSTRUCTORS = {
    "PersistentHeadCache": {_PREPARATION_OWNER, _PREWARM},
    "PrefetchState": {_PREPARATION_OWNER},
    "TooltipPreparationController": {_ASSEMBLY},
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


def _attributes(node: ast.AST) -> list[ast.Attribute]:
    if isinstance(node, ast.Attribute):
        return [node, *_attributes(node.value)]
    if isinstance(node, ast.Starred):
        return _attributes(node.value)
    if isinstance(node, ast.List | ast.Tuple):
        return [attribute for item in node.elts for attribute in _attributes(item)]
    return []


def _call_name(node: ast.Call, aliases: dict[str, str]) -> str | None:
    if isinstance(node.func, ast.Name):
        return aliases.get(node.func.id, node.func.id)
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _session_constructor_call(
    node: ast.Call,
    direct: set[str],
    modules: set[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "SessionController"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in modules
    )


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


def _is_preparation_reference(node: ast.AST, names: set[str]) -> bool:
    return any(
        (isinstance(part, ast.Name) and part.id in names)
        or (isinstance(part, ast.Attribute) and part.attr == "tooltip_preparation")
        for part in ast.walk(node)
    )


def _annotation_is_tooltip_controller(annotation: ast.AST | None) -> bool:
    if isinstance(annotation, ast.Name | ast.Attribute):
        return (
            getattr(annotation, "id", None) == "TooltipController"
            or getattr(annotation, "attr", None) == "TooltipController"
        )
    return isinstance(annotation, ast.Constant) and annotation.value == "TooltipController"


def _annotation_is_tooltip_preparation(annotation: ast.AST | None) -> bool:
    if isinstance(annotation, ast.Name | ast.Attribute):
        return (
            getattr(annotation, "id", None) == "TooltipPreparationController"
            or getattr(annotation, "attr", None) == "TooltipPreparationController"
        )
    return (
        isinstance(annotation, ast.Constant) and annotation.value == "TooltipPreparationController"
    )


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


def _preparation_names(scope: ast.AST, inherited: set[str]) -> set[str]:
    names = {*inherited, "tooltip_preparation"}
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
            if _annotation_is_tooltip_preparation(argument.annotation)
        )

    nodes = _scope_nodes(scope)
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
            if not _is_preparation_reference(value, names):
                continue
            aliases = {name for target in targets for name in _bound_names(target)}
            if not aliases <= names:
                names.update(aliases)
                changed = True
    return names


def _scoped_nodes(
    scope: ast.AST,
    inherited_owner: set[str] | None = None,
    inherited_preparation: set[str] | None = None,
):
    owner_names = _owner_names(scope, inherited_owner or set())
    preparation_names = _preparation_names(scope, inherited_preparation or set())
    yield scope, owner_names, preparation_names
    for node in _scope_nodes(scope):
        if isinstance(node, _SCOPES):
            yield from _scoped_nodes(node, owner_names, preparation_names)
        else:
            yield node, owner_names, preparation_names


def inspect_source(source: str, path: Path) -> list[Finding]:
    tree = ast.parse(source, filename=str(path))
    site = _site(path)
    findings: list[Finding] = []
    imported_aliases = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    session_controller_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "saitenka.app.session.controller"
        for alias in node.names
        if alias.name == "SessionController"
    }
    session_controller_modules = {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "saitenka.app.session.controller" and alias.asname is not None
    }

    for node, owner_names, preparation_names in _scoped_nodes(tree):
        if isinstance(node, ast.Attribute) and (
            site == _COMPOSITION
            and _is_self_attribute(node)
            and node.attr in (_LEGACY_SESSION_ATTRIBUTES | _RETIRED_PREPARATION_ATTRIBUTES)
        ):
            findings.append(Finding(path, node.lineno, "legacy-session-field", node.attr))

        if (
            site == _COMPOSITION
            and isinstance(node, ast.Attribute)
            and node.attr in _RETIRED_PREPARATION_CLOSE_ACCESS
            and _is_preparation_reference(node.value, preparation_names)
        ):
            findings.append(Finding(path, node.lineno, "preparation-close-detail", node.attr))

        if isinstance(node, ast.ClassDef) and site == _PREWARM and node.name == "_PrewarmIPC":
            findings.append(Finding(path, node.lineno, "full-session-prewarm", node.name))

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
                if site == _COMPOSITION and _is_preparation_reference(
                    attribute.value, preparation_names
                ):
                    findings.append(
                        Finding(path, attribute.lineno, "preparation-state-write", attribute.attr)
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
            name = _call_name(node, imported_aliases)
            if name in _CONSTRUCTORS and site != _OWNER:
                findings.append(Finding(path, node.lineno, "owned-constructor", name or ""))
            allowed = _PREPARATION_CONSTRUCTORS.get(name or "")
            if allowed is not None and site not in allowed:
                findings.append(Finding(path, node.lineno, "preparation-constructor", name or ""))
            if site == _PREWARM and _session_constructor_call(
                node, session_controller_names, session_controller_modules
            ):
                findings.append(Finding(path, node.lineno, "full-session-prewarm", name or ""))
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
    print("tooltip-ownership: tooltip policy, preparation, stores, and lifecycle stay owned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
