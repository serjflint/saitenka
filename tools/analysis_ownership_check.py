"""Reject analysis state, policy, or construction escaping its feature owner."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"
OWNER = "features/analysis/analysis_controller.py"
ASSEMBLY = "session/assembly.py"
COMPOSITION = "session/controller.py"
RETIRED_PATHS = (
    APP / "analysis_overlay.py",
    APP / "episode_analysis.py",
)
RETIRED_METHODS = {
    "_draw_analysis",
    "_finish_analysis",
    "_refresh_analysis",
    "invalidate_analysis",
    "set_analysis_open",
}
RETIRED_FIELDS = {"_analysis_submit", "analysis"}
PRIVATE_OWNER_MEMBERS = {"_finish", "_request", "_state", "_submit_pending"}


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


def inspect_source(source: str, path: Path) -> list[Finding]:
    tree = ast.parse(source, filename=str(path))
    site = _site(path)
    findings: list[Finding] = []
    constructor_names = {"AnalysisController"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            constructor_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "AnalysisController"
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
            site == COMPOSITION
            and isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "analysis_controller"
            and node.attr in PRIVATE_OWNER_MEMBERS
        ):
            findings.append(Finding(path, node.lineno, "private-owner-access", node.attr))
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name is not None and name in constructor_names and site != ASSEMBLY:
                findings.append(Finding(path, node.lineno, "owned-constructor", name))
            if name == "_AnalysisState" and site != OWNER:
                findings.append(Finding(path, node.lineno, "private-state-constructor", name))
        if isinstance(node, ast.ImportFrom) and node.names:
            findings.extend(
                Finding(path, node.lineno, "private-state-import", alias.name)
                for alias in node.names
                if alias.name == "_AnalysisState" and site != OWNER
            )
    return findings


def inspect_tree() -> list[Finding]:
    findings = [
        Finding(path, 1, "retired-module", str(path.relative_to(ROOT)))
        for path in RETIRED_PATHS
        if path.exists()
    ]
    for path in sorted(APP.rglob("*.py")):
        findings.extend(inspect_source(path.read_text(encoding="utf-8"), path))
    return sorted(findings, key=lambda finding: (str(finding.path), finding.line, finding.rule))


def main() -> int:
    findings = inspect_tree()
    for finding in findings:
        path = finding.path.relative_to(ROOT)
        print(f"{path}:{finding.line}: {finding.rule}: {finding.detail}")
    if findings:
        print(f"analysis-ownership: {len(findings)} violation(s)", file=sys.stderr)
        return 1
    print("analysis-ownership: old session state/facades retired; owner constructed in assembly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
