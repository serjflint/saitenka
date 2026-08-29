"""Enforce the settled ``saitenka.app`` package layout after the feature move."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"
FEATURES = APP / "features"

FEATURE_PACKAGES = frozenset(
    {
        "analysis",
        "annotation",
        "help",
        "history",
        "mining",
        "picker",
        "preview",
        "profiles",
        "sidebar",
        "subtitle",
        "tooltip",
        "translation",
    }
)
RETIRED_FLAT_MODULES = frozenset(
    {
        "analysis_overlay",
        "card_preview",
        "close_ledger",
        "cue_annotation",
        "episode_analysis",
        "help_controller",
        "help_overlay",
        "hover_adapter",
        "hover_intents",
        "hover_metadata",
        "hover_snapshot",
        "interaction_adapter",
        "interaction_intents",
        "interaction_jobs",
        "interaction_surfaces",
        "mine_adapter",
        "mine_intents",
        "mined_feedback",
        "mined_seed",
        "mined_set",
        "mined_store",
        "miner",
        "miner_ui",
        "mining_controller",
        "mouse_capture",
        "nested_popup",
        "panel_adapter",
        "panel_intents",
        "picker_controller",
        "popups",
        "prefetch",
        "preview_controller",
        "profile_adapter",
        "profile_cli",
        "profile_controller",
        "profile_intents",
        "reader_context",
        "reader_deps",
        "session_adapter",
        "session_assembly",
        "session_controller",
        "session_factory",
        "session_intents",
        "session_resources",
        "session_routes",
        "session_runtime",
        "sidebar",
        "sidebar_controller",
        "stateless",
        "sub_picker",
        "surfaces",
        "tooltip",
        "tooltip_controller",
        "tooltip_engaged",
        "tooltip_panel",
        "tooltip_raster",
        "word_audio",
    }
)


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line: int
    rule: str
    detail: str


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append((node.lineno, node.module))
            if node.module == "saitenka.app":
                modules.extend((node.lineno, f"{node.module}.{alias.name}") for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            modules.append((node.lineno, node.value))
    return modules


def inspect_tree(app: Path = APP) -> list[Finding]:
    findings: list[Finding] = []
    features = app / "features"
    actual = {path.name for path in features.iterdir() if path.is_dir()}
    findings.extend(
        Finding(features, 0, "missing-feature-package", name)
        for name in sorted(FEATURE_PACKAGES - actual)
    )
    findings.extend(
        Finding(features / name, 0, "undeclared-feature-package", name)
        for name in sorted(actual - FEATURE_PACKAGES - {"__pycache__"})
    )

    for name in sorted(RETIRED_FLAT_MODULES):
        path = app / f"{name}.py"
        if path.exists():
            findings.append(Finding(path, 1, "retired-flat-module", name))

    retired_paths = {f"saitenka.app.{name}" for name in RETIRED_FLAT_MODULES}
    for path in sorted(app.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in _imported_modules(tree):
            retired = next(
                (name for name in retired_paths if module == name or module.startswith(f"{name}.")),
                None,
            )
            if retired is not None:
                findings.append(Finding(path, line, "retired-flat-import", retired))
    return sorted(findings, key=lambda item: (str(item.path), item.line, item.rule))


def main() -> int:
    findings = inspect_tree()
    for finding in findings:
        path = finding.path.relative_to(ROOT)
        print(f"{path}:{finding.line}: {finding.rule}: {finding.detail}")
    if findings:
        print(f"app-package-layout: {len(findings)} violation(s)", file=sys.stderr)
        return 1
    print("app-package-layout: declared features packaged; flat module names retired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
